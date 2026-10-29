# Bổ sung nghiên cứu: trích xuất đường dây và huấn luyện CNN cho sơ đồ nhất thứ

Ngày đối chiếu tài liệu: 02/10/2026.

Tài liệu này bổ sung cho bản “Phân tích mạch điện”, ngày 26/09/2026, và sơ đồ raster trạm 110 kV người dùng cung cấp. Phạm vi giữ nguyên đầu vào raster và thư viện ký hiệu; tạm gác nghiên cứu lọc nhiễu, khử nghiêng, tăng tương phản. Tiling được xét như cách tổ chức dữ liệu và suy luận, không mở rộng thành nghiên cứu tiền xử lý.

Các kiến trúc, số lượng dữ liệu và tham số bên dưới là phương án thực nghiệm đề xuất. Chưa huấn luyện mô hình, chưa đo độ chính xác và chưa trích xuất được mạng điện có kiểm chứng từ sơ đồ mẫu.

## 1. Những nội dung cần bổ sung hoặc sửa trong bản hiện tại

| Nội dung hiện tại | Nội dung cần bổ sung |
|---|---|
| Dùng YOLO, Faster R-CNN hoặc DETR | Chọn một mô hình nền, xác định đầu vào, loại nhãn, cách huấn luyện và tiêu chí so sánh. DETR là kiến trúc dựa trên Transformer; nếu nghiên cứu tập trung CNN, có thể chọn Faster R-CNN với backbone CNN hoặc một detector YOLO cụ thể. |
| Dùng thư viện ký hiệu để sinh dữ liệu | Mỗi ký hiệu cần có định nghĩa lớp, biến thể, cổng và hướng; sinh cả sơ đồ có cấu trúc thay vì chỉ ghép các ký hiệu rời. |
| Morphology + Hough | Bổ sung giữ dây ngắn, phân biệt nét ký hiệu, truy vết, xử lý rẽ nhánh, điểm giao, cổng và nối đứt có điều kiện. |
| Ẩn ký hiệu và văn bản | Không xóa toàn bộ bounding box một cách mặc định vì có thể xóa cả dây đi qua và dây sát cổng. |
| Phân biệt dây và thanh cái | Dựa trên nhãn, bố cục và vai trò kết nối; độ dày chỉ là một dấu hiệu. |
| Gom dây thành một sợi dây thẳng | Chỉ gộp đoạn liên tiếp phù hợp về hình học; mạng dây phải giữ được góc, nhánh và các điểm đấu nối. |
| Mỗi văn bản gắn với một object | Có văn bản gắn với thiết bị, thanh cái, ngăn lộ hoặc toàn sơ đồ; không ép mọi text vào một thiết bị. |

Với ảnh mẫu, các thanh cái C11/C12, C41/C42, C31/C32, hai vùng biến áp T1/T2 và các ngăn lộ dày đặc tạo ra những loại tình huống rất khác nhau. Kết quả tốt ở một nhánh thẳng phía dưới chưa chứng minh xử lý được cả vùng nhiều nhánh quanh biến áp.

## 2. Định nghĩa đầu ra trước khi chọn thuật toán

### 2.1. Ba tầng biểu diễn

1. **Tầng hình học:** ký hiệu, bounding box, cổng, polyline dây, thanh cái, góc rẽ và điểm giao. Mục đích là giữ bố cục và tái dựng SVG.
2. **Tầng đấu nối:** thiết bị có các cổng; mỗi cổng được gắn với một net. Một net là tập dây/thanh cái dẫn nối liên tục theo quy ước bản vẽ, không đi xuyên qua thiết bị để đồng nhất hai phía.
3. **Tầng trạng thái điện:** trạng thái đóng/mở, quan hệ nội bộ của thiết bị và các quy tắc điện. Chỉ xây dựng nếu có yêu cầu và đủ thông tin.

Không dùng tính liên thông của toàn bộ nét đen để kết luận liên thông điện. Hai cổng máy cắt vẫn là hai cổng khác nhau; một ký hiệu biến áp không làm các phía của nó thành cùng một net. Với dao cách ly mở, cần giữ thiết bị cùng các cổng và trạng thái nếu xác định được, không lấp khe hở như lỗi dây đứt.

### 2.2. Quy ước điểm giao

- `junction`: các nhánh thuộc cùng net theo quy ước được xác nhận.
- `crossing_no_connect`: các dây giao về hình học nhưng thuộc các net khác nhau; lưu cả cặp nhánh nào đi cùng nhau.
- `bend`: hai đoạn của cùng dây đổi hướng.
- `device_port`: chỗ dây đi vào cổng thiết bị.
- `unknown`: ảnh hoặc quy ước không đủ rõ.

Không đặt luật chung “có chấm thì nối, không chấm thì không nối”. Một bộ bản vẽ có thể dùng giao chữ T không chấm để biểu diễn kết nối. Quy ước phải được xác nhận trên dữ liệu mục tiêu.

## 3. Thuật toán trích xuất đường dây

### 3.1. Hệ thống nền bằng hình học

**Bước 1 - Sinh ứng viên đường.** Dùng morphology với nhiều độ dài phần tử cấu trúc cho đường ngang/dọc và HoughLinesP hoặc bộ phát hiện đoạn thẳng cho ứng viên đoạn. Giữ thêm nét ngắn quanh cổng và giao điểm; nếu chỉ giữ đường dài sẽ bỏ mất dây nối giữa hai ký hiệu gần nhau. Hough phát hiện đoạn thẳng, không cung cấp trực tiếp quan hệ đấu nối. Xem [tài liệu Hough của OpenCV](https://docs.opencv.org/4.13.0/d9/db0/tutorial_hough_lines.html).

**Bước 2 - Phân biệt ứng viên dây với nét thiết bị/chữ.** Dùng vị trí ký hiệu và text làm bằng chứng. Một cạnh hình chữ nhật của máy cắt hoặc nét chữ cũng có thể là đoạn thẳng, nên không coi mọi kết quả Hough là dây. Lưu ảnh gốc và tọa độ cổng trước khi loại nét thiết bị. Với dây bị text che, có thể tạo ứng viên nối ở tầng hình học, nhưng phải lưu là đoạn suy đoán và kiểm tra điều kiện.

**Bước 3 - Tạo đường tâm và truy vết.** Từ mask dây, làm mảnh để tạo skeleton. Truy các đường từ đầu mút đến đầu mút, cổng hoặc điểm rẽ nhánh, rồi lưu thành polyline. Các cụm pixel có nhiều hàng xóm cần được gom thành một ứng viên điểm nối; số hàng xóm trong lân cận 8 chỉ là heuristic vì tại góc và giao điểm có thể xuất hiện nhiều pixel dư.

**Bước 4 - Xử lý điểm giao.** Phân loại từng điểm giao bằng ảnh cục bộ, dấu chấm, cung vượt, hướng nhánh và quy ước bản vẽ. Giao không nối phải được tách thành các tuyến độc lập trước khi tìm thành phần liên thông/net. Một mask nhị phân có giao chữ X chưa đủ chứa thông tin nhánh nào nối với nhánh nào.

**Bước 5 - Gắn cổng.** Mỗi loại và biến thể ký hiệu có bộ cổng trong hệ tọa độ chuẩn. Sau khi phát hiện ký hiệu, dùng hướng và phép biến đổi phù hợp để ước lượng cổng trên trang. Với vị trí còn lệch, tinh chỉnh bằng nét cục bộ hoặc head dự đoán keypoint. Không dùng khoảng cách gần nhất giữa tâm thiết bị và dây.

Một tiêu chí chọn cổng ứng viên có thể là:

`cost = a * khoảng_cách_chuẩn_hóa + b * sai_lệch_hướng + c * thiếu_bằng_chứng_nét + d * vi_phạm_loại_cổng`.

Chuẩn hóa khoảng cách theo bề rộng nét hoặc kích thước ký hiệu; ngưỡng được chọn trên validation. Nếu hai ứng viên gần tương đương, lưu `unknown` thay vì ép gán.

**Bước 6 - Nối các khoảng đứt có điều kiện.** Chỉ thêm liên kết khi hai đầu mút gần, hướng phù hợp, nằm trong vùng có khả năng bị che/mất nét và không vượt qua thiết bị, điểm giao không nối hoặc ký hiệu mở. Không dùng phép closing diện rộng để nối tất cả nét gần nhau. Khe hở có thể có ý nghĩa thiết bị.

**Bước 7 - Dựng mạng.** Tách polyline tại cổng và junction, lưu đồ thị hình học; sau khi xử lý crossing, dùng thành phần liên thông hoặc union-find trên các liên kết đã được chấp nhận để gom net. Giữ tọa độ điểm đấu trên thanh cái cho tái dựng, dù chúng có thể cùng net.

### 3.2. Dây và thanh cái

Trong mask ban đầu có thể gộp dây/thanh cái thành một lớp conductor để giữ liên tục, rồi phân loại ở tầng đối tượng. Thanh cái có bằng chứng từ nhãn Cxx, dạng đường dài và số nhánh đi vào; không xác định chỉ từ “đường dày”. Nếu có đủ nhãn, thử thêm head busbar hoặc lớp busbar riêng và so sánh với cách gộp.

### 3.3. Vai trò của CNN trong trích xuất dây

Thay bước tạo mask dây bằng U-Net: ảnh cục bộ → xác suất pixel thuộc dây/thanh cái. Sau đó vẫn cần truy vết và dựng mạng. U-Net là CNN cho phân đoạn, có thiết kế phù hợp để kết hợp đặc trưng nhiều mức và được đề xuất cùng chiến lược augmentation/tile trong [bài gốc U-Net](https://arxiv.org/abs/1505.04597).

Khởi đầu bằng hai lớp conductor/background. Lớp background bao gồm text, nét ký hiệu, khung và mọi vùng không phải dây mục tiêu. Nếu cần hiểu lỗi rõ hơn, thử các lớp conductor, symbol, text, frame, background, nhưng phải giải quyết thứ tự lớp tại vùng chồng lấp; không thêm lớp khi chưa đủ nhãn.

Một mạng CNN nhỏ khác nhận crop điểm giao để phân biệt junction/crossing. Với bài toán nhiều kiểu crossing, đầu ra cần cả kiểu kết nối hoặc cặp nhánh, không chỉ nhãn “crossing”.

**Giới hạn cần nhớ:** phân đoạn đúng pixel không đồng nghĩa dựng đúng mạng; CNN không thể xác định chắc chắn kết nối nếu ảnh và quy ước không chứa đủ bằng chứng.

## 4. Bài toán nào cần huấn luyện CNN?

| Nhiệm vụ | Đầu vào | Đầu ra | Nhãn huấn luyện | Khởi đầu đề xuất |
|---|---|---|---|---|
| Phát hiện ký hiệu | Tile có nhiều đối tượng | Lớp, box, độ tin cậy | Box + class; hướng/trạng thái nếu cần | Detector pretrained có backbone CNN |
| Phân loại hai ký hiệu dễ nhầm | Crop có ký hiệu và chút ngữ cảnh | Lớp hoặc thuộc tính | Class/attribute | CNN classifier nhỏ, chỉ thêm khi có lỗi thực tế |
| Phân đoạn dây | Tile sơ đồ gốc | Xác suất conductor từng pixel | Mask conductor + vùng ignore | U-Net |
| Phân biệt điểm giao | Crop tâm giao có các nhánh xung quanh | Loại giao và quan hệ nhánh | Class + branch pairing | CNN classifier nhỏ |
| Định vị cổng | Crop thiết bị | Tọa độ và độ thấy rõ của cổng | Keypoint + port role + visibility | Quy tắc template trước; keypoint head sau |

Phân loại ảnh ký hiệu rời không thay thế phát hiện ký hiệu trong sơ đồ. Detector học đồng thời “ở đâu” và “loại gì”; classifier chỉ học “loại gì” trên crop đã cho.

Không cần huấn luyện toàn bộ chuỗi từ đầu. Detector có thể fine-tune từ trọng số pretrained, nhưng vẫn cần nhãn lớp điện riêng. OCR nên dùng mô hình có sẵn trước, chỉ fine-tune khi đã đo được lỗi trên số thiết bị, chữ xoay hoặc tiếng Việt.

## 5. Xây dựng dữ liệu: ba nguồn bổ sung cho nhau

### 5.1. Dữ liệu thật cùng miền

Thu thập nhiều sơ đồ nhất thứ raster cùng kiểu với ảnh mẫu, ưu tiên đa dạng trạm, họ bản vẽ, bố cục và quy ước ký hiệu. Không tính các ngăn lộ lặp lại trong một trang là nhiều sơ đồ độc lập.

Một phạm vi pilot có thể là 60-100 trang nguồn đủ đa dạng, chọn khoảng 6-10 lớp phổ biến theo thư viện thực tế. Nếu chỉ có 10-20 trang, vẫn làm được thử nghiệm nhỏ và phân tích lỗi, nhưng chưa đủ để kết luận tổng quát hóa. Có thể mở rộng lên 200-500 trang khi đường cong học và test khác trạm cho thấy cần thêm dữ liệu. Các con số này là quy mô lập kế hoạch, không phải ngưỡng bảo đảm chính xác.

Số trang phải đi kèm số instance từng lớp, số junction/crossing, số kiểu cổng và số họ bản vẽ. 100 trang cùng một mẫu có thể kém đa dạng hơn 30 trang từ nhiều mẫu khác nhau. Với lớp hiếm, cần chủ động bổ sung ví dụ và bối cảnh dễ nhầm, không chỉ nhân bản crop.

Gán nhãn 5 trang trước để đo công sức. Tổng công gán nhãn xấp xỉ số trang nhân thời gian trung vị/trang, cộng thời gian kiểm tra và sửa. Dây và topology thường cần nhiều công hơn box; có thể gán box trên toàn bộ pilot, rồi chọn một tập con giàu tình huống để gán mask/topology đầy đủ. Khi nhãn chỉ phủ một vùng, phải có vùng ignore; phần chưa gán không được coi là background.

### 5.2. Dữ liệu tổng hợp từ thư viện ký hiệu

**Nguyên tắc: sinh cấu trúc → bố trí → vẽ → xuất nhãn.**

1. Tạo thư viện ký hiệu chuẩn với `class_id`, `variant_id`, hình vẽ, vùng thân ký hiệu, cổng có vai trò và hướng được phép.
2. Chọn mẫu cấu trúc lấy từ các họ sơ đồ đã xác nhận: thanh cái với nhiều ngăn lộ, chuỗi thiết bị, nhánh biến áp, liên lạc giữa các thanh cái. Không sinh topology điện hoàn toàn tùy tiện.
3. Tạo thiết bị, cổng và netlist đúng của mẫu đó.
4. Bố trí ký hiệu, route dây theo đoạn ngang/dọc hoặc dạng được cho phép. Tạo cả nhánh nối và giao không nối với quy ước nhất quán.
5. Vẽ text, số thiết bị, nhãn thanh cái, khung và vùng chú giải tương tự miền đích.
6. Raster hóa ảnh cùng các lớp nhãn độc lập: box/class, mask dây, cổng, junction, crossing, graph.
7. Kiểm tra nhãn tự động và bằng mắt: dây có vào đúng cổng, graph có khớp ảnh, ký hiệu không bị che ngoài ý muốn.

Ví dụ một mẫu có thanh cái N0 và các thiết bị Q1, Q2. Cổng thứ nhất Q1 thuộc N0, cổng thứ hai thuộc N1. Dù cả hai cổng cùng thiết bị, N0 và N1 không được gộp. Khi thêm dây ngang cắt nhánh N1 nhưng không nối, generator lưu pairing của hai tuyến và hai net riêng, kể cả chúng chạm cùng vùng pixel.

Một quy mô ban đầu có thể là 2.000-5.000 sơ đồ tổng hợp từ nhiều graph/layout/style độc lập, sau đó tăng nếu thực nghiệm cần. 5.000 ảnh đổi vị trí từ một graph không tạo ra 5.000 cấu trúc độc lập.

**Vì sao chỉ paste ký hiệu chưa đủ?** Nó có thể giúp detector nhìn thấy các lớp, nhưng không cung cấp dữ liệu đúng cho cổng, dây, giao điểm và netlist. Mô hình cũng có thể học mép crop/nền khác nhau thay vì hình học ký hiệu.

**Vì sao không dùng ảnh do AI tạo làm nhãn topology mặc định?** Ảnh có thể trông hợp lý nhưng không giữ đúng dây/cổng/ký hiệu. SVG hoặc công cụ vẽ có cấu trúc cho phép xuất ground truth chính xác hơn. Ảnh sinh không có cấu trúc chỉ nên dùng sau khi kiểm tra và gán nhãn tương ứng.

### 5.3. Dữ liệu công khai

- [CGHD do DFKI công bố](https://github.com/DFKI/cghd): có ảnh mạch vẽ tay, box ký hiệu/text, một phần segmentation và netlist. Theo hướng dẫn dữ liệu, segmentation là nét vẽ so với nền, không mặc nhiên là mask chỉ dây. Cần chuyển đổi/gán lại nhãn nếu dùng cho wire segmentation. Miền ký hiệu và phong cách khác sơ đồ trạm của đề tài.
- [AITEE](https://github.com/CKnievel/aitee-dataset): dữ liệu mạch giáo dục, chủ yếu các cấu hình điện trở, có box cho phần tử và điểm kết nối. Phù hợp tham khảo pipeline và định dạng nhãn, không thay thế tập trạm điện mục tiêu.
- [SKID trong nghiên cứu KIEP](https://link.springer.com/article/10.1007/s42452-026-08271-3): nghiên cứu dùng 347 bản vẽ trạm thực, phục vụ text, ký hiệu và ghép quan hệ text-ký hiệu. Đây không phải bằng chứng về bộ nhãn dây/netlist đầy đủ. Mục Data availability nêu dữ liệu không công khai tự do và có điều kiện cấp quyền; không lập kế hoạch phụ thuộc vào khả năng tải ngay.

## 6. Quy chuẩn gán nhãn

### 6.1. Ký hiệu

- Box ôm thân ký hiệu; không bao trọn số thiết bị hoặc dây dài bên ngoài.
- Thống nhất ký hiệu phức hợp được gán là một thiết bị hay nhiều phần. Không lúc gán cả biến áp, lúc gán từng vòng tròn của cùng loại ký hiệu.
- Tách `device_type` với `state`: mở/đóng là thuộc tính nếu hình biểu diễn cho phép nhận biết. Nếu không thấy rõ, để `unknown`.
- Ký hiệu tương tự chỉ khác nét nhỏ phải có ví dụ dương và ví dụ dễ nhầm trong dữ liệu.
- Ký hiệu bị cắt ở mép tile có `truncated`; quyết định giữ/ignore theo quy chuẩn nhất quán.

### 6.2. Dây

- Gán nét dây nhìn thấy bên ngoài thân thiết bị, đến vị trí cổng đã định nghĩa.
- Không coi đường bao máy cắt, nét cuộn dây hoặc tiếp điểm bên trong thiết bị là dây ngoài.
- Lưu thanh cái dạng đối tượng/polyline và cho nó thuộc mask conductor nếu dùng mô hình nhị phân.
- Dây bị text che: mask visible chỉ chứa phần nhìn thấy; liên kết suy ra được lưu riêng trong graph với căn cứ và trạng thái nhãn.
- Không ép pixel bị che thành positive nếu mục tiêu mô hình là visible mask. Nếu muốn amodal segmentation, phải tạo một bài toán và quy chuẩn nhãn riêng.
- Mask raster giữ bề rộng nét thực; polyline đường tâm phục vụ dựng mạng. Không phình mask tùy ý vì có thể làm hai dây sát nhau dính lại.

### 6.3. Điểm giao và cổng

Mỗi giao có tọa độ, loại, các nhánh incident và nhóm nhánh được nối. Mỗi cổng có ID, thiết bị, vai trò, tọa độ, hướng tiếp cận nếu có, visibility và net ID.

Khi chia tile, đầu mút sinh do crop không phải đầu mút thật của dây. Cần đánh dấu `tile_boundary`; không gán nó là cổng hoặc junction thật.

### 6.4. Kiểm tra nhãn

Hai người gán độc lập một tập nhỏ rồi thống nhất các bất đồng, đặc biệt giao không nối, ranh giới ký hiệu và cổng. Kiểm tra chéo box, mask và graph: cổng có thiết bị; mỗi cổng được gán tối đa một net; crossing không nối không hợp nhất net; liên kết có đường vẽ hoặc căn cứ suy ra được ghi rõ.

## 7. Chia train/validation/test đúng cách

Chia theo bản vẽ nguồn và nhóm liên quan **trước khi cắt tile hoặc augmentation**. Các trang cùng trạm/họ bản vẽ, bản sửa đổi gần như giống nhau và mọi biến thể của cùng nguồn cần ở cùng split. Kiểm tra near-duplicate.

Ví dụ có 100 trang đủ đa dạng: có thể bố trí 70/15/15 theo nhóm, chấp nhận lệch số trang để giữ nhóm nguyên vẹn. Đây là tỷ lệ tham khảo. Test nên có nhóm trạm hoặc phong cách chưa xuất hiện trong train; báo cáo rõ test cùng phong cách và test khác phong cách nếu có.

Không crop 1.000 patch từ một trang rồi chia ngẫu nhiên 800/100/100. Model đã nhìn thấy gần như cùng bản vẽ và các vùng overlap, khiến điểm đánh giá cao giả tạo.

Synthetic cũng chia theo graph/layout/template và seed nguồn trước khi tạo biến thể. Không dựng ảnh train từ topology/layout của một trang test thật. Có thể dùng thư viện ký hiệu được cung cấp như tài sản đầu vào đã biết, nhưng phải công bố điều này; nếu muốn đánh giá ký hiệu hoặc biến thể chưa thấy, cần một phép chia riêng.

Validation dùng chọn siêu tham số, ngưỡng và checkpoint. Test chỉ dùng đánh giá cuối sau khi chốt. Nếu dùng test để chọn lỗi rồi bổ sung train, tập đó đã thành dữ liệu phát triển; cần test mới.

## 8. Quy trình huấn luyện cụ thể

### 8.1. Huấn luyện detector ký hiệu

1. Chọn một detector pretrained có backbone CNN làm baseline. Khởi đầu với 6-10 lớp đã có quy chuẩn, không mở ngay toàn bộ thư viện.
2. Dùng tile khoảng 640-1024 pixel, chọn kích thước theo kích thước thực của ký hiệu và bộ nhớ. Không thu nhỏ toàn trang khiến nét phân biệt còn 1-2 pixel. Phóng lớn không phục hồi chi tiết đã mất.
3. Fine-tune trên synthetic có cấu trúc, rồi fine-tune trên dữ liệu thật. Đối chứng với fine-tune trực tiếp dữ liệu thật. Có thể thử mixed batch nếu fine-tune thật làm mất lớp hiếm.
4. Dùng loss phân loại và hồi quy box của detector được chọn; thêm loss hướng/keypoint khi có đầu ra và nhãn tương ứng.
5. Tăng tần suất mẫu có lớp hiếm và cặp lớp dễ nhầm; giữ đủ ví dụ âm như text, khung, nét dây và hình không thuộc lớp mục tiêu.
6. Ghép kết quả tile về tọa độ trang; giải quyết phát hiện trùng do overlap và ký hiệu bị cắt ở mép. Đánh giá trên trang, không chỉ trên crop.

Trọng số pretrained từ ảnh tự nhiên cung cấp đặc trưng khởi đầu, không cung cấp hiểu biết sẵn về các lớp máy cắt/dao cách ly trong dữ liệu mục tiêu.

### 8.2. Huấn luyện U-Net phân đoạn dây

**Một mẫu:** `X` là tile ảnh; `Y` là mask conductor cùng kích thước; `V` là mask vùng có nhãn hợp lệ. Model xuất logits cùng kích thước, loss chỉ tính trên vùng V.

Với dây mảnh, background áp đảo. Không dùng pixel accuracy tổng thể làm tiêu chí chính. Khởi đầu bằng:

`L = L_weighted_BCE + L_Dice`.

Nếu model bỏ dây mảnh hoặc ưu tiên nền, thử điều chỉnh trọng số dương theo phân bố train hoặc Focal loss, đồng thời kiểm tra precision vì tăng trọng số có thể gây nối giả. Focal loss được đề xuất để giảm ảnh hưởng của nhiều ví dụ nền dễ trong [bài gốc Focal Loss](https://arxiv.org/abs/1708.02002).

Một nhánh thực nghiệm bổ sung:

`L = L_weighted_BCE + L_Dice + lambda * L_soft-clDice`.

[clDice](https://arxiv.org/abs/2003.07311) xét skeleton và mask để khuyến khích giữ cấu trúc liên tục. Đây là lý do đáng thử cho dây mảnh, nhưng không bảo đảm CNN dựng đúng mạng điện. Nếu hai dây giao không nối có mask chạm nhau, clDice không tự biết hai net khác nhau; vẫn cần nhãn crossing và xử lý graph.

Không bật nhiều loss cùng lúc mà thiếu đối chứng. So sánh BCE+Dice trước, sau đó thêm clDice, giữ split và điều kiện đánh giá giống nhau.

**Patch sampling:** chọn cả vùng dây, cổng, giao điểm, vùng nhiều text/thiết bị và vùng âm khó. Có thể khởi đầu với 50% patch có dây, 25% patch quanh cổng/giao và 25% vùng âm khó; tỷ lệ được chỉnh theo phân bố thật. Tránh toàn patch trắng cũng như chỉ patch có dây.

**Cấu hình chạy thử:** patch 512x512, batch 4-8 nếu vừa bộ nhớ, AdamW, learning rate khoảng 1e-4 khi fine-tune, ngân sách 50-100 epoch và early stopping theo validation. Khi huấn luyện từ đầu hoặc đổi backbone cần dò learning rate lại. Đây là điểm khởi đầu, chưa là cấu hình tối ưu; không có ước lượng thời gian GPU khi chưa chạy đo.

**Kiểm tra trước khi chạy lớn:** cho model overfit 2-5 patch có nhãn. Nếu vẫn không học được, kiểm tra ảnh/mask lệch tọa độ, class ID, sigmoid/loss, ignore mask và biến đổi augmentation trước khi thêm dữ liệu.

**Vòng huấn luyện ở mức nguyên lý:** lấy minibatch → biến đổi đồng bộ ảnh và nhãn → forward CNN → tính loss trên vùng hợp lệ → backpropagation → cập nhật trọng số. Sau mỗi epoch đánh giá validation không cập nhật trọng số, lưu checkpoint tốt nhất. Đến cuối, chạy checkpoint được chọn trên test và dựng mạng bằng cùng thuật toán đã chốt.

### 8.3. Huấn luyện CNN cho giao điểm

Lấy ứng viên từ hệ thống dây, crop đủ rộng để thấy các nhánh, cung vượt và nét xung quanh. Gán class và pairing. Bộ train cần cả junction có/không chấm theo quy ước, crossing có cung, nét ký hiệu trông như giao và ứng viên giả.

Trong giai đoạn kiểm thử toàn chuỗi, crop phải đến từ các ứng viên dự đoán thực tế. Chỉ đánh giá crop có tâm giao ground truth sẽ bỏ qua lỗi không phát hiện được giao hoặc crop lệch tâm.

Nếu quy ước không đủ bằng chứng cho một giao chữ X, model không nên bị buộc học một nhãn đoán. Lưu unknown và đánh giá riêng tỷ lệ cần kiểm tra.

### 8.4. Augmentation hợp lệ

Thử tịnh tiến, thay đổi tỷ lệ trong miền thực, đổi khoảng cách giữa các phần tử bằng generator và xoay các hướng được thư viện cho phép. Mọi phép hình học phải biến đổi box, polyline, keypoint và nhãn pairing tương ứng; mask rời rạc dùng nội suy nearest-neighbor.

Không lật ảnh mặc định khi làm sai chữ, vai trò cổng hoặc quy ước ký hiệu. Không xóa/giãn nét phân biệt giữa hai lớp và vẫn giữ nhãn cũ. Nếu tạo khe đứt nhân tạo, phải bảo đảm không biến nó thành biểu diễn thiết bị mở có nhãn sai. Trong phạm vi hiện tại chưa cần thêm nghiên cứu noise/blur.

### 8.5. Suy luận bằng tile

Overlap được chọn theo kích thước ký hiệu/ngữ cảnh lớn nhất cần nhìn, không chỉ một tỷ lệ cố định. U-Net ghép bản đồ xác suất ở vùng overlap bằng trọng số ưu tiên tâm tile, sau đó threshold và truy vết ở tọa độ toàn trang. Không dựng net riêng từng tile rồi bỏ mất dây chạy qua mép.

Detector ghép box toàn trang và xử lý trùng. Giữ danh sách ký hiệu/cổng ở vùng biên cần xử lý lại nếu tile đã cắt mất thân. Các threshold và chính sách overlap được chọn trên validation.

## 9. Đánh giá ở cả mức nhận dạng và mức đấu nối

| Tầng | Chỉ số cần báo cáo | Điều nó chưa chứng minh |
|---|---|---|
| Ký hiệu | mAP, recall từng lớp, confusion matrix | Box đúng chưa chắc cổng đúng |
| Dây | Precision/recall, Dice/IoU, clDice; lỗi thiếu/thừa đoạn | Mask đẹp chưa chắc đúng crossing |
| Giao | F1 từng loại, pairing accuracy; recall phát hiện ứng viên | Classifier crop chuẩn không đại diện lỗi toàn chuỗi |
| Cổng | Precision/recall ghép cổng, sai số tọa độ chuẩn hóa | Cổng gần đúng chưa chắc gán đúng net |
| Mạng | F1 quan hệ thiết bị-cổng-net, lỗi gộp net/tách net | Điểm trung bình có thể che một lỗi quan trọng |
| Toàn trang | Tỷ lệ trang có netlist đúng hoàn toàn, số sửa/trang | Đạt trên vài mẫu chưa đại diện mọi họ sơ đồ |

Không so graph chỉ bằng ID net vì ID có thể đổi mà cấu trúc vẫn giống. Trước hết ghép thiết bị dự đoán với thiết bị thật bằng lớp/vị trí, đối chiếu cổng theo vai trò. So sánh partition net hoặc cặp cổng thuộc cùng net; báo cáo thêm exact-net và quan hệ từng cổng để tránh thanh cái nhiều cổng chi phối toàn bộ điểm. Ground truth và prediction đều cần giữ thiết bị ngăn giữa hai net.

Chạy hai cách để định vị lỗi:

1. Dùng ký hiệu và cổng ground truth để đánh giá riêng thuật toán dây/topology.
2. Dùng toàn bộ dự đoán để đánh giá hệ thống thực.

Tách lỗi false merge (nối hai net khác nhau), false split (đứt một net), nhầm crossing, nhầm cổng và nhầm thiết bị. Vẽ overlay để chỉ rõ từng lỗi. Một khoảng pixel sai nhỏ ở giao điểm có thể gây thay đổi mạng lớn dù IoU vẫn cao.

## 10. Các thí nghiệm tối thiểu để trả lời nhận xét

### 10.1. So sánh thuật toán dây

- W0: morphology/Hough + quy tắc + dựng graph.
- W1: U-Net BCE+Dice + cùng quy tắc/dựng graph.
- W2: W1 thêm soft-clDice.
- W3: W2 thêm CNN giao điểm; nếu clDice không giúp, dùng W1 làm nền W3.

Giữ ground truth ký hiệu/cổng cố định trong phép so sánh riêng dây, rồi chạy lại toàn chuỗi. So sánh cả mask và graph; không chọn mô hình chỉ vì IoU tăng.

### 10.2. So sánh cách xây dựng dữ liệu

- D0: pretrained detector fine-tune dữ liệu thật.
- D1: synthetic có cấu trúc, kiểm thử trực tiếp trên dữ liệu thật để đo khoảng cách miền.
- D2: synthetic pretraining → real fine-tuning.
- D3: D2 + bổ sung dữ liệu thật từ lỗi trên validation/pool chưa gán.

Giữ test thật cố định, ghi rõ lượng dữ liệu và tổng số bước cập nhật. Để tách hiệu quả synthetic khỏi việc train lâu hơn, có đối chứng dữ liệu thật với ngân sách bước tương đương. Nếu có đủ tài nguyên, dùng 3 seed và báo cáo trung bình/độ phân tán.

### 10.3. Đường cong học

Giữ validation/test cố định; train trên các tập con 10/25/50/100 trang khi nguồn cho phép. Kiểm tra lớp hiếm và graph F1 tăng ra sao. Nếu thêm dữ liệu cùng kiểu không giúp, bổ sung kiểu giao/cổng/style thiếu thay vì chỉ tăng số ảnh.

## 11. Lộ trình triển khai nghiên cứu

| Mốc | Công việc | Sản phẩm kiểm chứng |
|---|---|---|
| 1 | Chốt lớp, cổng, net và quy ước giao; gán thử 5 trang | Hướng dẫn nhãn, danh sách tình huống chưa rõ, công gán/trang |
| 2 | Tạo tập nhóm nguồn; gán box và tập con mask/graph | Dataset có split cố định và kiểm tra nhất quán |
| 3 | Làm baseline hình học và detector fine-tune thật | Bảng lỗi ban đầu trên cùng validation |
| 4 | Tạo generator từ graph và thư viện có cổng | Ảnh tổng hợp và nhãn đa tầng đúng |
| 5 | Train U-Net/CNN giao; chạy các đối chứng dữ liệu/loss | Kết quả riêng từng mô-đun và toàn chuỗi |
| 6 | Chốt hệ thống và đánh giá test khác nguồn | Báo cáo tổng quát hóa, số sửa/trang và giới hạn |

Không đặt mục tiêu “trích dây thật chuẩn” bằng một con số chưa có căn cứ. Chuyển thành tiêu chí đo được: lỗi gộp/tách net, recall cổng, F1 đấu nối và tỷ lệ sơ đồ đúng hoàn toàn; ngưỡng nghiệm thu được chốt sau baseline và theo yêu cầu ứng dụng.

## 12. Đoạn có thể dùng để cập nhật định hướng nghiên cứu

> Đề tài tập trung phân tích thuật toán nhận dạng và phục hồi cấu trúc đấu nối từ sơ đồ nhất thứ dạng raster, với chất lượng đầu vào được giả định cố định. Hệ thống đề xuất kết hợp CNN phát hiện ký hiệu, CNN phân đoạn dây và thuật toán dựng đồ thị dựa trên cổng thiết bị cùng quy ước điểm giao. Morphology và Hough được dùng làm hệ thống nền để so sánh. Dữ liệu huấn luyện gồm sơ đồ thật cùng miền và sơ đồ tổng hợp được sinh từ graph có cổng, sau đó bố trí và raster hóa để xuất tự động box, mask, keypoint và netlist. Mô hình được huấn luyện có giám sát, thử nghiệm fine-tuning từ pretrained và pretraining trên synthetic trước khi fine-tuning trên real. Việc chia dữ liệu thực hiện theo trạm/bản vẽ nguồn trước khi cắt patch nhằm tránh rò rỉ. Đánh giá gồm độ chính xác nhận dạng, chất lượng phân đoạn và đặc biệt các lỗi gộp/tách net cùng độ đúng của quan hệ cổng-net. Các nội dung này là phương án nghiên cứu sẽ được kiểm chứng bằng thực nghiệm.

## 13. Tài liệu nghiên cứu để đọc theo thứ tự

1. [Bayer et al., Modular Graph Extraction for Handwritten Circuit Diagram Images, 2024](https://arxiv.org/abs/2402.11093): tham khảo phân rã mô-đun nhận dạng, segmentation và dựng graph. Dữ liệu vẽ tay nên không chuyển nguyên kết quả sang sơ đồ trạm.
2. [Ronneberger et al., U-Net, 2015](https://arxiv.org/abs/1505.04597): nền kiến trúc CNN phân đoạn và tổ chức tile.
3. [Shit et al., clDice, CVPR 2021](https://arxiv.org/abs/2003.07311): loss/metric cho cấu trúc tuyến; cần phân biệt topology mask với topology điện.
4. [Lin et al., Focal Loss, ICCV 2017](https://arxiv.org/abs/1708.02002): nguyên lý giảm chi phối bởi mẫu nền dễ.
5. [CGHD - kho chính thức của DFKI](https://github.com/DFKI/cghd): nhãn, quy chuẩn dữ liệu và giới hạn segmentation nét vẽ.
6. [AITEE - kho của nhóm tác giả](https://github.com/CKnievel/aitee-dataset): mẫu dữ liệu box/điểm nối và pipeline mạch giáo dục.
7. [Yang et al., Automated extraction and parsing of key information in complex substation drawings, 2026](https://link.springer.com/article/10.1007/s42452-026-08271-3): dữ liệu cùng miền trạm, nhận dạng và quan hệ text-ký hiệu; đọc cả phần Data availability.
8. [OpenCV Hough Line Transform](https://docs.opencv.org/4.13.0/d9/db0/tutorial_hough_lines.html): công cụ phát hiện đoạn thẳng cho baseline hình học.
