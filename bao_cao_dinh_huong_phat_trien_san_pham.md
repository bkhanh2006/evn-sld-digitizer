# Báo Cáo Định Hướng Đề Xuất Phát Triển Sản Phẩm: Hệ Thống Phân Tích và Số Hóa Sơ Đồ Nhất Thứ Trạm Biến Áp

> **Ngày cập nhật:** 29/10/2026 (đồng bộ với [SDD v1.2](thiet_ke_he_thong_sdd.md); bản trước: [tmp/bao_cao_dinh_huong_phat_trien_san_pham_v2026-10-26.md](tmp/bao_cao_dinh_huong_phat_trien_san_pham_v2026-10-26.md))  
> **Đối tượng mục tiêu:** Sơ đồ nhất thứ trạm biến áp theo quy chuẩn EVN (mẫu chuẩn: Trạm 110kV Thạch Linh E18.1, E18.9 và các sơ đồ cùng họ).  
> **Mục tiêu cốt lõi:** Chuyển đổi ảnh sơ đồ nhất thứ (raster/PDF scan) thành dữ liệu cấu trúc **Đồ thị mạng điện (Topology Graph) + Bản vẽ vector tương tác (SVG) + Bản vẽ kỹ thuật AutoCAD (`.dxf` / `.dwg`)**, phục vụ quản lý vận hành, chỉnh sửa thiết kế, kiểm tra đóng/cắt và mô phỏng tô màu vùng mang điện.

---

## 1. Tổng Quan Bài Toán & Định Nghĩa Sản Phẩm

### 1.1. Phạm vi giai đoạn đầu (Focus Scope)
Thực tế tài liệu tại các đơn vị điện lực có hai nhóm bản vẽ rất khác nhau:
- **Loại A – Sơ đồ nhất thứ toàn trạm (Mục tiêu giai đoạn 1):** Thể hiện rõ các cấp điện áp (110kV, 35kV, 22kV), thanh cái (`C11`, `C12`, `C31`, `C41`...), máy biến áp lực (`T1`, `T2`), và các ngăn lộ xuất tuyến/liên lạc (`E18.1`, `E18.9`).
- **Loại B – Sơ đồ lưới phân phối / Phiếu thao tác (Giai đoạn mở rộng sau):** Đường dây trải dài tự do, nhiều nhánh rẽ địa lý (`PTT 89`, `PTT 90`, `PTT 91`, `PTT 92`).

> **Quyết định chiến lược:** Giai đoạn đầu **chỉ tập trung vào Loại A (Sơ đồ nhất thứ toàn trạm)** để tận dụng tính quy chuẩn về cấu trúc ngăn lộ (bay) và hệ thống đánh mã vận hành của EVN.

### 1.2. Đầu vào và Đầu ra của hệ thống
- **Đầu vào (Input):**
  - Ảnh raster độ phân giải cao, xuất từ PDF hoặc bản scan sạch. Hệ thống đưa mọi ảnh về một tỉ lệ chuẩn (mặc định 8 px/mm, tức bản A3 ngang rộng khoảng 3360 px). Ảnh cần phóng to quá 1,5 lần để đạt tỉ lệ này vẫn được xử lý nhưng bị gắn cảnh báo `low_resolution_input`. Không dùng upscale để bù cho ảnh nguồn kém: cần lấy bản gốc độ phân giải cao.
  - Thư viện ký hiệu gồm **17 lớp** (SVG mẫu, biến thể đóng/mở, hướng hợp lệ, **tọa độ cổng chuẩn – ports/terminals** và hành vi điện của từng lớp), khai báo một lần trong `symbols_metadata.json`.
- **Đầu ra (Output – 3 tầng biểu diễn liên kết qua ID):**
  1. **Tầng Hình học & Bản vẽ kỹ thuật (`SVG` + `AutoCAD .dxf/.dwg`):** Tọa độ `bbox`, hướng xoay, tỉ lệ của từng ký hiệu; đường tâm (`polyline`) của dây dẫn và thanh cái; vị trí nhãn chữ. Xuất đồng thời ra **SVG** (cho Web/App) và **AutoCAD `.dxf`** (với đầy đủ `BLOCK`, `ATTRIB`, `LWPOLYLINE`, `LAYER` cho kỹ sư điện).
  2. **Tầng Đấu nối (`topology_graph.json`, schema `SubstationTopologyGraph` v1.1):** Danh sách ngăn lộ (`bays`), thiết bị (`devices`), các đầu cực (`ports/terminals`), thanh cái (`busbars`), dây (`wires`), các điểm nút (`junction` / `crossing_no_connect`), toàn bộ văn bản đọc được (`texts`) và danh sách mạng dây (`nets`). Đảm bảo biết chính xác **cổng nào của thiết bị nào nối vào thanh cái hay đường dây nào**. Quan hệ đấu nối chỉ lưu ở một nơi là `nets`.
  3. **Tầng Nghiệp vụ & Tương tác (`interactive UI`):** Trạng thái đóng/mở trên bản vẽ (`drawingState`), trạng thái vận hành (`operationalState`), giao diện chồng lớp (overlay) để kỹ sư duyệt/sửa nhanh các vị trí độ tin cậy thấp (`reviewStatus: pending`).

---

## 2. Chiến Lược Xây Dựng Dữ Liệu (Data Engineering)

Chất lượng dữ liệu là yếu tố quyết định hàng đầu với một hệ thống nhận dạng chuyên ngành có ít dữ liệu thật. Dữ liệu của dự án được xây dựng từ **hai nguồn bổ trợ nhau**: Dữ liệu tổng hợp chuẩn EVN (Synthetic Dataset) và Dữ liệu thực tế (Real Gold Dataset).

### 2.1. Đánh giá phương án công cụ sinh dữ liệu: Tại sao thay `PyPowSyBl` bằng `EVN Modular Bay Generator`?

Qua quá trình khảo sát bài báo *arXiv:1903.09495* và thư viện mã nguồn mở `PowSyBl` (`PyPowSyBl`), báo cáo rút ra kết luận kỹ thuật quan trọng:

| Tiêu chí | Phương án dùng `PyPowSyBl` + Hậu xử lý SVG | Phương án `EVN Modular Bay Generator` (Đề xuất) |
|---|---|---|
| **Độ phủ ký hiệu EVN** | **Thiếu nhiều ký hiệu xuất hiện trên bản vẽ thực tế**: không có sẵn `TI` (biến dòng), `TU` (biến điện áp), `Chống sét van`, `Cầu chì`, `Máy cắt hợp bộ rút kéo` (22kV). | **Phủ đủ 17 lớp** đã thống kê từ `E18.1` / `E18.9`, dùng trực tiếp bộ ký hiệu SVG trích từ các bản vẽ này. |
| **Bố cục (Macro Layout)** | Chỉ xếp ngăn lộ một chiều theo chuẩn châu Âu; không vẽ được bố cục hỗn hợp của EVN (110kV/22kV nằm ngang, 35kV như `C31/C32` dựng dọc bên phải). | Mỗi ngăn lộ dựng trong một hệ tọa độ chuẩn rồi xoay/lật khi đặt, nên cùng một mẫu cho ra cả ngăn dọc (110kV, 22kV) và ngăn ngang (35kV). Bố cục vùng theo preset cấu hình được. |
| **Rủi ro khi chèn linh kiện** | `PowSyBl` tính khoảng cách cố định. Cắt dây SVG hậu kỳ để nhét thêm `TI`, nhánh `TU`, `Dao tiếp địa` sẽ gây **đè hình (overlap)** và lệch tọa độ. | Tính tọa độ từ gốc theo kích thước thật của từng linh kiện; **kiểm tra va chạm khi đặt**, mẫu nào còn chồng hình hoặc đứt dây thì bị loại bởi `validate_dataset.py`. |
| **Chi phí lập trình** | Phải can thiệp vào nhân Java (`powsybl-diagram`) nếu muốn sửa layout và custom symbol chuẩn. | Viết thuần bằng **Python** (`lxml` / `svgwrite` + `networkx`), toàn quyền kiểm soát mã nguồn. Đổi lại phải tự viết bộ đi dây (A\* trực giao). |

### 2.2. Kiến trúc Bộ sinh dữ liệu tổng hợp theo Mô-đun Ngăn lộ (Bay-based Synthetic Pipeline)

Nguyên tắc bất di bất dịch: **"Graph-first" (Cấu trúc mạng có trước → Hình ảnh và Đáp án sinh ra cùng lúc)**. Không bao giờ vẽ ảnh rồi mới dùng thuật toán đoán lại nhãn.

```
[1. Thư viện Ký hiệu SVG + Port Metadata] 
              │
              ▼
[2. Bộ sinh Topology Trạm (20–30 cấu trúc trạm gốc)]
   (Tổ hợp từ 6 loại Khối Ngăn Lộ chuẩn EVN + Sinh mã thiết bị tự động)
              │
              ▼
[3. Engine Bố cục, Đi dây & Vẽ SVG (Python Parametric Renderer)]
   (Đặt ngăn lộ theo preset, đi dây A* trực giao, sinh điểm giao nối / không nối,
    biến thiên khoảng cách, độ dày nét, phông chữ, trạng thái đóng/mở,
    thêm khung tên, con dấu, chữ ký làm vật gây nhiễu)
              │
       ┌──────┴──────────────────────┬───────────────────────┐
       ▼                             ▼                       ▼
[Ảnh PNG + SVG]             [Detection & Mask GT]     [Topology Graph GT]
(Đầu vào cho Model)         (BBox, Class, OCR,        (Devices, Terminals,
                             Wire Mask cho U-Net)      Nets, Junctions)
```

#### Sáu khối mô-đun ngăn lộ (Bay Templates) cần xây dựng:
1. **Khối Ngăn đường dây 110kV / 35kV:** Thanh cái → DCL thanh cái (`-1`/`-2`) → Máy cắt → TI → DCL đường dây (`-7`) + DTĐ (`-15`, `-76`) → Nhánh TU/Chống sét → Đường dây.
2. **Khối Ngăn xuất tuyến trung áp 22kV / 35kV:** Thanh cái (`C41`, `C42`) → Máy cắt hợp bộ (có mũi tên rút kéo) → TI → DTĐ (`-38`) → Cáp xuất tuyến + Chống sét.
3. **Khối Máy biến áp chính (`T1`, `T2`):** MBA 2 hoặc 3 cuộn dây + Chống sét + Khe hở phóng điện + Dao cách ly trung tính (`131-08`, `132-08`) + Tụ bù/Kháng.
4. **Khối Ngăn liên lạc / Phân đoạn thanh cái:** (`112`, `312`, `412`) gồm MC + 2 DCL + 2 DTĐ + TI.
5. **Khối Ngăn Đo lường TU Thanh cái:** (`TUC11`, `TUC12`, `TUC31`, `TUC41`) gồm DCL + Cầu chì + DTĐ + TU 3 cuộn/2 cuộn.
6. **Khối Ngăn Tự dùng & Tụ bù ngang:** (`TD31`, `TD41`, `TBN301`, `TBN302`).

Mã thiết bị trong dữ liệu tổng hợp được sinh ngược từ chính ngữ pháp mã EVN mà pipeline dùng để đọc nhãn (`configs/evn_codes.yaml`, SDD mục 8.2.4). Nhờ vậy mã sinh ra luôn hợp lệ theo cùng một luật. Đặc tả hàm và chuỗi thiết bị của từng khối: SDD mục 8.1.1.

### 2.3. Quy chuẩn danh mục lớp (Classes) và Schema Nhãn

Để tránh nhập nhằng khi huấn luyện Detector, các ký hiệu có hình thái đóng/mở khác nhau được tách lớp rõ ràng. Danh mục gồm **17 lớp detector**. Trong đồ thị, lớp detector được quy về `type` + `drawingState` (ví dụ `disconnector_open` → `type: disconnector`, `drawingState: open`). Danh mục đầy đủ kèm vai trò cổng, hành vi khi dựng Net (`netBehavior`) và khi tô vùng mang điện (`conduction`) nằm ở SDD mục 5.

| Nhóm | Tên lớp (`class_name`) | Mô tả / Đặc điểm nhận diện trên bản vẽ EVN | Số cổng (`ports`) |
|---|---|---|---|
| **Đóng cắt** | `circuit_breaker` | Máy cắt thường (hình chữ nhật đứng/ngang) | 2 |
| | `drawout_breaker` | Máy cắt hợp bộ / xe kéo (hình chữ nhật có 2 đầu mũi tên kép ở lưới 22kV) | 2 |
| | `disconnector_closed` | Dao cách ly đang đóng | 2 |
| | `disconnector_open` | Dao cách ly đang mở (lưỡi dao hở góc nghiêng) | 2 |
| | `earthing_switch_closed` | Dao tiếp địa đóng (phía đất nằm trong thân ký hiệu) | 1 |
| | `earthing_switch_open` | Dao tiếp địa mở | 1 |
| | `fuse_disconnector` | Cầu chì / Dao cách ly cầu chì (`CC-TD31`, `CC-TUC41`) | 2 |
| **Biến đổi & Đo lường** | `transformer_2w` | Máy biến áp 2 cuộn dây (gồm cả MBA tự dùng `TD31`, `TD41`) | 2 (`hv`, `lv`) |
| | `transformer_3w` | Máy biến áp lực 3 cuộn dây (`T1`, `T2`) | 3 (`hv`, `mv`, `lv`) + 1 trung tính |
| | `current_transformer` | Biến dòng `TI` (1 hoặc 2 vòng tròn xuyên qua đường dây); hai phía TI thuộc cùng một Net | 2 |
| | `voltage_transformer` | Biến điện áp `TU` (2 vòng tròn hoặc 3 vòng tròn ghép Y/Δ) | 1 (nhánh đo) |
| **Bảo vệ & Bù** | `surge_arrester` | Chống sét van (`CS1T1`, `CS-TUC32`...) | 1 (nhánh xuống đất) |
| | `spark_gap` | Khe hở phóng điện trung tính MBA | 1 |
| | `shunt_capacitor` | Tụ bù ngang (`TBN301`, `TBN302`) | 1 |
| | `shunt_reactor` | Kháng điện (`L301`, `L302`) | 2 |
| **Phụ trợ** | `ground_symbol` | Ký hiệu nối đất (nếu tách riêng khỏi DTĐ/CS) | 1 |
| | `line_feeder_arrow` | Mũi tên đầu đường dây / cáp xuất tuyến | 1 |

> Số cổng của dao tiếp địa và tụ bù ngang được chốt là 1 theo bản vẽ E18.1. Nếu bản vẽ khác vẽ 2 cổng thì bổ sung lớp biến thể, không đổi lớp hiện có (SDD mục 13).

#### Quy mô dữ liệu theo từng giai đoạn:
- **Giai đoạn Khởi động (Pilot Dataset):**
  - **100 ảnh tổng hợp** (sinh từ 25–30 topology gốc $\times$ 3–4 biến thể bố cục/phông chữ/độ dày nét; chia 25 đơn giản – 45 trung bình – 30 phức tạp). Chia tập `Train/Val` **theo topology gốc**, tuyệt đối không trộn ảnh cùng một topology vào cả hai tập.
  - **5–10 bản vẽ thực tế** (trích từ `E18.1`, `E18.9` và các trang sơ đồ trạm có sẵn) được gán nhãn chuẩn bằng CVAT/Label Studio để làm **Gold Evaluation Set** (Tập kiểm thử chuẩn).
- **Giai đoạn Mở rộng (Production Dataset):**
  - Nâng bộ sinh tổng hợp lên **1.000 – 3.000 ảnh** và gán nhãn bán tự động (Model gán nháp → Người sửa) trên **30 – 60 bản vẽ thực tế**.

---

## 3. Định Hướng Kiến Trúc Mô Hình & Thuật Toán (System Architecture)

Hệ thống không dùng một mô hình "hộp đen" duy nhất mà được thiết kế theo **kiến trúc mô-đun kết hợp Thị giác máy tính cổ điển (Classical CV) + Học sâu (Deep Learning) + Luật chuyên ngành điện (Domain Rules)**.

```
            ┌──────────────────────────────────────────────┐
            │ Ảnh sơ đồ nhất thứ / PDF                     │
            │ 0. Chuẩn hóa tỉ lệ (độ dày nét → px/mm)      │
            └───────────────┬──────────────────┬───────────┘
                            ▼                  ▼
┌─────────────────────────────────┐  ┌─────────────────────────────────┐
│ 1. NHẬN DẠNG KÝ HIỆU            │  │ 2a. ĐỌC VĂN BẢN (OCR)           │
│ - Chia patch (Tiling) + WBF     │  │ - PaddleOCR PP-OCRv5            │
│ - Faster R-CNN / YOLO           │  │ - Xử lý chữ dọc 90°/270°        │
│ - Ước lượng hướng (xoay, lật)   │  │ - Phân tích mã theo ngữ pháp EVN│
│ - Chiếu tọa độ cổng (Ports)     │  │                                 │
└───────┬─────────────────┬───────┘  └───────┬─────────────────┬───────┘
        │                 └────────┬─────────┘                 │
        │                          ▼                           │
        │            ┌───────────────────────────┐             │
        │            │ 2b. GHÉP NHÃN – THIẾT BỊ  │             │
        │            │ Hungarian có "không ghép" │             │
        │            └─────────────┬─────────────┘             │
        ▼                          │                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 3. TRÍCH XUẤT DÂY DẪN (cần vùng ký hiệu và vùng chữ để che)         │
│ - Baseline: Morph + Hough + Skeleton │ Nâng cao: U-Net              │
│ - Tách thanh cái │ Phân loại điểm giao (luật hình học / CNN)        │
└──────────────────────────────────┬──────────────────────────────────┘
                                   ▼
            ┌──────────────────────────────────────────────┐
            │ 4. KHÔI PHỤC ĐỒ THỊ & HẬU KIỂM EVN           │
            │ - Ghép dây vào cổng (gán toàn cục)           │
            │ - Dựng Net theo hành vi điện của từng lớp    │
            │ - Kiểm tra bằng luật mã EVN → danh sách issue│
            └──────────────────────┬───────────────────────┘
                                   ▼
            ┌──────────────────────────────────────────────┐
            │ 5. GIAO DIỆN DUYỆT ⇄ dựng lại Net, kiểm tra   │
            │    → XUẤT JSON + SVG tương tác + AutoCAD DXF │
            └──────────────────────────────────────────────┘
```

Bước 1 và 2a chạy song song. Bước 2b và 3 phải chờ bước 1: ghép nhãn cần danh sách thiết bị, còn trích dây cần che vùng ký hiệu và vùng chữ. Thứ tự phụ thuộc chi tiết và interface của từng khâu: SDD mục 3 và 7.

### 3.1. Mô-đun 1: Tổ chức ảnh (Tiling) & Nhận dạng ký hiệu (Symbol Detection)
- **Vấn đề:** Bản vẽ toàn trạm như `E18.1` chứa 300–500 ký hiệu; các ký hiệu như `TI` hay `Dao tiếp địa` chỉ rộng vài chục pixel. Nếu thu nhỏ cả trang về $640 \times 640$ hoặc $1024 \times 1024$, ký hiệu sẽ bị mất nét.
- **Giải pháp:**
  1. **Cắt mảnh có chồng lấn (Overlapping Tiling / SAHI):** Cắt ảnh gốc thành các patch kích thước $1024 \times 1024$ với độ chồng lấn (overlap) 15–20%. Khi suy luận xong, dùng thuật toán NMS / WBF (Weighted Boxes Fusion) để gộp các box ở biên patch về hệ tọa độ toàn trang.
  2. **Mô hình Detector:**
     - **Phương án chủ lực 1:** `Faster R-CNN ResNet-50-FPN v2` (Pretrained COCO), mạnh về bắt vật thể nhỏ khi giữ kích thước input lớn.
     - **Phương án chủ lực 2 (Đối chứng):** `YOLOv8 / YOLOv11` kết hợp thư viện `SAHI`, tốc độ nhanh và dễ triển khai.
  3. **Ước lượng hướng ký hiệu:**
     - Với ký hiệu đối xứng về điện (máy cắt, dao cách ly, TI…), chỉ cần biết ngang hay dọc, suy ra từ tỉ lệ `bbox`.
     - Với ký hiệu bất đối xứng (dao tiếp địa, TU, chống sét, MBA, mũi tên đường dây), detector không cho biết hướng. Hệ thống so khớp ảnh với mẫu SVG ở mọi góc xoay 0/90/180/270 và có/không lật, chọn tổ hợp khớp nhất; nếu hai tổ hợp tốt nhất quá sát nhau thì gắn cảnh báo.
  4. **Định vị Cổng (Port Estimation):**
     - Từ `bbox`, `class`, góc xoay và cờ lật, ánh xạ bộ tọa độ cổng chuẩn (`ports`) từ thư viện ký hiệu lên ảnh, sau đó tinh chỉnh nhẹ theo điểm đầu mút nét dây lân cận. Quy ước tọa độ và công thức: SDD mục 2.

### 3.2. Mô-đun 2: Phát hiện, Đọc văn bản (OCR) & Ghép nhãn thông minh
- **Công cụ nền:** `PaddleOCR (PP-OCRv5)` — chạy độc lập trên ảnh để bắt toàn bộ văn bản (không chỉ bắt chữ bên trong `bbox` vì nhãn thiết bị luôn nằm bên cạnh ký hiệu).
- **Xử lý đặc thù bản vẽ EVN:**
  1. **Chữ xoay dọc $90^\circ / 270^\circ$:** Tên các lộ xuất tuyến 22kV phía dưới (`Cầu Phủ`, `Thạch Tiến`, `Tỉnh Ủy`, `Vincom TP Hà Tĩnh`...) và thông số TI được viết dọc. Cần bổ sung bước phát hiện box chữ có tỉ lệ $H/W > 2.5$ (ngưỡng cấu hình được). Với các box này, xoay cả $90^\circ$ và $270^\circ$, đưa qua bộ đọc chữ (Recognizer) và giữ kết quả có điểm tin cậy cao hơn.
  2. **Ghép nhãn vào thiết bị (Text-to-Symbol Association):**
     - Chuẩn hóa chuỗi trước khi phân tích: viết hoa, bỏ khoảng trắng giữa tiền tố và số (`TI 173` → `TI173`), chỉ sửa nhầm lẫn OCR (`O→0`, `I→1`…) ở vị trí bắt buộc là chữ số.
     - Phân loại vai trò chuỗi chữ bằng **ngữ pháp mã EVN khai báo trong file cấu hình** (`configs/evn_codes.yaml`), không viết cứng regex trong mã nguồn. Các nhóm mẫu đã thống kê từ E18.1:
       - Thanh cái: `C11`, `C12`, `C31`, `C42`...
       - Máy cắt chính: `171`, `131`, `112`, `371`, `471`...
       - Dao cách ly / tiếp địa theo ngăn: `171-1`, `171-7`, `171-15`, `171-76`, `131-08`, `431-38`...
       - Dao thuộc ngăn TU thanh cái hoặc ngăn tụ bù: `TUC32-28`, `TUC41-14`, `TUC42-24`, `T302-2`...
       - Đo lường / bảo vệ / bù: `TI171`, `TU173`, `TUC11`, `CS1T1`, `CS-TUC32`, `CC-TD31`, `TD41`, `TBN302`, `L302`, `T302`, `T1`.
       - Nhãn ngăn: `DP` (dự phòng). Thông số: `63/63/63MVA`, `4MVAr`... Chữ không khớp mẫu nào nằm cạnh mũi tên đường dây được coi là tên lộ.
     - Ghép nhãn bằng thuật toán **Hungarian** dựa trên khoảng cách giữa hai hộp (nhãn và ký hiệu) và độ thẳng hàng (cùng cột với ngăn dọc, cùng hàng với ngăn ngang):
       - Nhãn chỉ được ghép với loại thiết bị mà mẫu mã cho phép (ràng buộc cứng).
       - Ma trận được mở rộng để **cho phép không ghép**: có chữ không thuộc thiết bị nào (tên lộ, khung tên), có thiết bị không có nhãn.
       - Dòng thông số (ví dụ `63/63/63MVA` và tỉ số điện áp của T1) được gắn thêm vào thiết bị gần nhất, không tham gia ghép 1–1.
       - Công thức chi phí: SDD mục 8.2.5.

### 3.3. Mô-đun 3: Trích xuất Đường dây, Thanh cái và Điểm giao
Triển khai theo lộ trình 2 cấp độ để vừa có sản phẩm chạy ngay, vừa có chiều sâu nghiên cứu:
- **Cấp độ 1 – Baseline Hình học cổ điển (Không cần đợi train):**
  1. Dùng phép hình thái học (`Morphology` với kernel ngang và dọc đa tỉ lệ) kết hợp `HoughLinesP` để trích xuất các đoạn thẳng.
  2. Che vùng không phải dây trước khi trích:
     - **Ký hiệu:** xóa phần trong `bbox` thu nhỏ vào một độ dày nét. Cổng nằm trên cạnh `bbox`, nên dây bị cắt đúng tại biên ký hiệu và đầu mút rơi sát cổng.
     - **Chữ:** xóa vùng chữ nhưng giữ lại các nét thẳng dài đi xuyên qua, để dây chạy ngang nhãn không bị đứt.
     - **Khung bản vẽ và khung tên:** loại bỏ.
  3. Làm mảnh (`Skeletonization`) và truy vết (`Tracing`) các đoạn polyline từ cổng đến cổng hoặc điểm giao. Góc rẽ chỉ là đỉnh của polyline, không thành nút.
  4. Tách thanh cái khỏi dây theo độ dày nét, độ dài và nhãn `C..` ghép được.
- **Cấp độ 2 – Học sâu với `U-Net` & `CNN Điểm giao` (Thử nghiệm nâng cao):**
  1. Huấn luyện mạng `U-Net` (hàm lỗi `Weighted BCE + Dice` / `soft-clDice`) trên patch $512 \times 512$ để tách trực tiếp lớp `conductor` (dây + thanh cái) khỏi nền, chữ và ký hiệu.
  2. Với các điểm giao chữ X hoặc chữ T, cắt vùng ảnh nhỏ ($64 \times 64$) quanh tâm giao điểm từ ảnh gốc và dùng quy tắc hình học hoặc mạng **CNN Classifier 4 lớp** (`junction` – có nối, `crossing_no_connect` – giao không nối, `bend` – góc rẽ, `false_positive`) để quyết định xem hai nhánh có thông mạch với nhau hay không.

**Quy ước vẽ cần nhận biết:**
- E18.1 vẽ giao không nối bằng **cầu vượt nửa cung tròn** (`hop_arc`), ví dụ chỗ cáp từ T2 đi xuống. Bộ phân loại phải nhận được cả kiểu này lẫn chữ thập trơn.
- Tại điểm giao không nối, hai đoạn đối diện được ghép lại thành **một dây liên tục**. Nút giao chỉ ghi nhận vị trí, không cắt dây.

### 3.4. Mô-đun 4: Khôi phục Đồ thị (Graph Reconstruction) & Hậu kiểm bằng Luật EVN
- **Nguyên tắc dựng Net:**
  - Tách biệt hoàn toàn **liên thông nét vẽ** và **liên thông mạng điện**. Cách dựng Net của mỗi lớp được khai báo trong thư viện ký hiệu (`netBehavior`), không viết cứng trong mã:
    - `split` (máy cắt, dao cách ly, cầu chì, MBA, kháng): mỗi cổng thuộc một Net riêng, không gộp xuyên qua thân thiết bị.
    - `passthrough` (TI): hai phía thuộc cùng một Net, vì TI lồng qua cùng một dây dẫn.
    - `terminal` (dao tiếp địa, TU, chống sét, khe hở, tụ bù, ký hiệu đất, mũi tên đường dây): thiết bị một cổng, treo vào một Net. Phía đất **không** được gộp thành một nút đất chung; nếu gộp, mọi Net có tiếp địa sẽ dính thành một.
  - Ghép đầu mút dây vào **Cổng thiết bị (`Port`)** dựa trên hàm chi phí kết hợp khoảng cách và **hướng tiếp cận có dấu** (dây phải đi vào từ phía ngoài của cổng). Việc ghép là **gán toàn cục**, mỗi cổng nhận tối đa một dây; không ghép bừa vào tâm `bbox`. Đầu dây không ghép được cổng nào thì nối vào thanh cái nếu nằm sát thân thanh cái, ngược lại bị đánh dấu là đầu dây hở để kỹ sư kiểm tra.
- **Hậu kiểm thông minh bằng Mã vận hành EVN (Domain Rule Validation):**
  - Hệ thống đánh số của EVN mang sẵn cấu trúc topology:
    - Chữ số đầu chỉ cấp điện áp: `1xx` (110kV), `3xx` (35kV), `4xx` (22kV). Đây là các cấp có trên E18.1; bảng chữ số cho các cấp khác được bổ sung vào file cấu hình khi có bản vẽ.
    - Chữ số thứ hai của mã máy cắt chỉ vị trí (Thông tư 44/2014/TT-BCT, Điều 58): `x3x` MBA, `x7x`/`x8x` (hoặc `x5x`–`x8x`) đường dây, `x4x` MBA tự dùng, `x0x` tụ bù ngang/máy phát. Máy cắt liên lạc mang số hai thanh cái (`112` nối C11–C12). Máy cắt tụ có ký tự đầu `T` (`T301`), máy cắt kháng có ký tự đầu `K`.
    - Hậu tố dao cách ly chỉ rõ vị trí kết nối:
      - `-1`: Dao cách ly nối vào Thanh cái 1 (`C11`, `C31`, `C41`).
      - `-2`: Dao cách ly nối vào Thanh cái 2 (`C12`, `C32`, `C42`).
      - `-7`: Dao cách ly phía đường dây.
      - `-3`: Dao cách ly phía máy biến áp.
      - Dao tiếp địa (Điều 60): hậu tố = hậu tố dao cách ly kề nó + một chữ số vị trí (`4` phía thanh cái, `5` phía máy cắt, `6` phía đường dây/tụ, `8` phía MBA/kháng/TU). Ví dụ `-15`, `-14`, `-75`, `-76`, `-38`, `-28`.
      - `-08`: Dao nối đất trung tính MBA (`131-08`, `132-08`).
      - Hậu tố chưa có trong danh sách: vẫn chấp nhận là dao cách ly hoặc dao tiếp địa, kèm thông báo `unknown_code_suffix`.
  - **Ứng dụng:**
    - **Phân xử khi mơ hồ:** nếu dây mờ hoặc đứt nét tại chỗ nối vào thanh cái `C11` hay `C12` và có nhiều ứng viên kết nối, hệ thống dùng nhãn `171-1` để chọn ứng viên nối vào thanh cái số 1. Kết nối vẫn ở trạng thái chờ duyệt, kèm ghi chú "chọn theo luật EVN" để kỹ sư thấy.
    - **Phát hiện mâu thuẫn:** dao `-1` không nối vào `C?1`, cấp điện áp của mã khác cấp điện áp của Net, trùng mã, chuỗi thiết bị trong một ngăn bị đứt.
    - **Nguyên tắc:** luật **không được tự tạo kết nối** khi không có bằng chứng hình ảnh. Danh mục đầy đủ các loại cảnh báo: SDD mục 8.3.4.
  - **Tô vùng mang điện:** từ các Net nguồn, duyệt BFS qua thiết bị đang dẫn điện (máy cắt/dao đóng, MBA, kháng, cầu chì, TI). Net có dao tiếp địa đóng được đánh dấu nối đất; Net vừa có điện vừa nối đất được cảnh báo trên giao diện.

### 3.5. Mô-đun 5: Tái dựng Bản vẽ Vector (SVG) và AutoCAD (`.dxf` / `.dwg`)
Từ cùng một cấu trúc dữ liệu trung gian `topology_graph.json`, hệ thống cung cấp hai bộ xuất (Exporter) độc lập phục vụ hai nhóm nhu cầu:
1. **Bộ xuất SVG tương tác (`SVGExporter`):**
   - File SVG độc lập, mở được bằng trình duyệt: mỗi thiết bị là một `<use>` trỏ tới ký hiệu chuẩn, kèm thuộc tính `data-*` (id, mã, Net); có thể bật lớp ảnh gốc mờ phía dưới để đối chiếu.
   - Dùng cho giao diện Web/App để kiểm duyệt kết quả, bật/tắt lớp hiển thị và tô màu vùng mang điện (BFS) khi giả lập đóng/cắt: click vào máy cắt hoặc dao để đổi trạng thái.
2. **Bộ xuất AutoCAD (`AutoCADDXFExporter` dùng thư viện `ezdxf`):**
   - Chạy hoàn toàn bằng Python, không cần cài AutoCAD trên máy chủ, sinh file `.dxf` (R2018, đơn vị mm):
     - **Ký hiệu thiết bị $\rightarrow$ `BLOCK` & `INSERT`:** Mỗi lớp thiết bị (`circuit_breaker`, `disconnector_open`, `current_transformer`...) là một Block có tâm/cổng trùng khớp với SVG. 17 block nằm chung trong một file `assets/symbols.dxf`, **vẽ ở hệ trục Y hướng lên**; nếu chép nguyên hình SVG (Y hướng xuống), các ký hiệu bất đối xứng sẽ bị soi gương trong AutoCAD.
     - **Nhãn thiết bị $\rightarrow$ `ATTRIB` / `MTEXT`:** Gắn mã hiệu (`171`, `171-1`, `T1`) thành thuộc tính `ATTRIB` đi kèm Block để khi kỹ sư di chuyển thiết bị trên AutoCAD, nhãn tự động di chuyển theo và trích xuất được bảng kê vật tư (`DATAEXTRACTION`). Chữ không thuộc thiết bị (tên lộ, thông số) xuất thành `MTEXT`.
     - **Đường dây & Thanh cái $\rightarrow$ `LWPOLYLINE`:** Dây dẫn xuất thành đường đa tuyến liền mạch; thanh cái có bề rộng nét (`const_width`) lớn hơn. Điểm nối vẽ chấm đặc, giao không nối kiểu cầu vượt vẽ cung tròn.
     - **Tự động phân lớp (`LAYER`):** Dựa vào cấp điện áp của Net, gán đối tượng vào layer theo cấp áp. Tên và màu layer nằm trong file cấu hình `cad_layers.yaml`; mặc định là `HV_110KV`, `MV_35KV`, `MV_22KV`, `BUSBAR_<cấp áp>`, `TEXT`, và layer cảnh báo `AI_REVIEW` (khoanh đám mây vàng tại các vị trí cần kỹ sư kiểm tra). Có thể đổi sang quy định layer riêng của đơn vị nếu có.
     - **Biến đổi hệ tọa độ:** Lật trục $Y$ và đổi px sang mm: $X_{\text{CAD}} = x / \text{pxPerMm}$, $Y_{\text{CAD}} = (H_{\text{page}} - y) / \text{pxPerMm}$, $\theta_{\text{CAD}} = -\theta_{\text{img}}$. Ký hiệu bị lật ngang được xuất bằng `xscale` âm. Chứng minh công thức: SDD mục 2.3.
   - **File `.dwg`:** `ezdxf` không ghi được `.dwg`. Khi cần thì chuyển đổi bằng **ODA File Converter** (cài riêng, gọi qua add-on `ezdxf.addons.odafc`).

---

## 4. Phương Pháp Đánh Giá & Thí Nghiệm Đối Chứng

Để báo cáo và sản phẩm có sức thuyết phục cao, hệ thống không chỉ đo bằng chỉ số xử lý ảnh đơn thuần mà được đánh giá ở **4 mức độ**:

1. **Mức Nhận dạng ký hiệu (Detection):** `mAP@0.5`, `mAP@0.5:0.95`, và đặc biệt là **Recall theo từng lớp** (chú trọng các lớp nhỏ như `TI`, `disconnector`, `earthing_switch`). Kèm độ chính xác ước lượng hướng (góc xoay, lật) trên các lớp bất đối xứng.
2. **Mức Văn bản & Ghép nhãn (OCR & Association):** Tỉ lệ đọc đúng hoàn toàn chuỗi mã thiết bị sau chuẩn hóa (exact match) và **F1-score ghép đúng cặp (Text – Thiết bị)**.
3. **Mức Đường dây (Wire Extraction):** `Dice / IoU` của mask dây, tỉ lệ đứt đoạn (false splits), tỉ lệ dính đoạn (false merges), và độ chính xác phân loại điểm giao (đặc biệt recall của giao không nối).
4. **Mức Đồ thị Đấu nối (Topology Metrics – Quan trọng nhất):**
   - **Terminal-pair Precision / Recall / F1:** so tập các cặp cổng `(Thiết bị A, Cổng i) — (Thiết bị B, Cổng j)` cùng thuộc một Net giữa đồ thị dự đoán và đáp án. ID của hai đồ thị không trùng nhau, nên thiết bị được so khớp trước bằng IoU của `bbox` (≥ 0,5). Với thiết bị đối xứng về điện, thử cả hai cách hoán vị cổng. Chi tiết: SDD mục 9.2.
   - **Tỉ lệ ngăn lộ (Bay) khôi phục đúng hoàn toàn** mà không cần con người can thiệp.
   - **Thời gian hiệu chỉnh thủ công trung bình (phút/bản vẽ)** trên giao diện duyệt, đo từ nhật ký sửa.

Tập thật được chia thành `dev` (dùng để chỉnh tham số, gồm E18.1) và `test` (giữ riêng, chỉ chạy khi chốt kết quả). Ngưỡng nghiệm thu đề xuất cho từng giai đoạn nằm ở SDD mục 9.3. Các ngưỡng này sẽ được chốt lại sau khi có số liệu baseline ở Giai đoạn 3.

---

## 5. Lộ Trình Triển Khai Đề Xuất (Actionable Roadmap)

Lộ trình được chia thành **6 giai đoạn ngắn gọn, làm đến đâu ra sản phẩm kiểm chứng được đến đó**:

| Giai đoạn | Mục tiêu trọng tâm | Đầu ra cụ thể (Deliverables) |
|---|---|---|
| **Giai đoạn 1: Chuẩn hóa Schema & Thư viện Ký hiệu** | Chốt cấu trúc JSON đầu ra và bộ ký hiệu chuẩn EVN. | - Schema `SubstationTopologyGraph` v1.1 dạng Pydantic (`src/core/schema.py`), gồm `bays`, `devices`, `busbars`, `wires`, `nodes`, `texts`, `nets`, `issues`.<br>- Bộ **17 file SVG** ký hiệu trích từ `E18.1` / `E18.9`, file `assets/symbols.dxf` chứa 17 block tương ứng, và `symbols_metadata.json` định nghĩa tọa độ cổng, `netBehavior`, `conduction`, hướng hợp lệ.<br>- Ngữ pháp mã `configs/evn_codes.yaml`.<br>- Unit test khớp tọa độ cổng SVG ↔ DXF và test phân tích mã EVN. |
| **Giai đoạn 2: Xây dựng Bộ sinh Dữ liệu Tổng hợp (100 mẫu)** | Tạo bộ sinh `EVN Modular Bay Generator` bằng Python và xuất 100 bộ dữ liệu đầu tiên. | - Script `generate_substation_dataset.py` ghép 6 loại khối ngăn lộ, có bộ đi dây A\* trực giao.<br>- Dataset 100 mẫu (25 đơn giản, 45 trung bình, 30 phức tạp) có đủ `PNG + SVG + DXF + BBox (COCO) + Wire Mask + Graph JSON`, kèm `split.json` chia theo topology gốc và `manifest.json` ghi seed.<br>- Script `validate_dataset.py` kiểm tra tự động 8 tiêu chí (SDD mục 8.1.5). |
| **Giai đoạn 3: Xây dựng Baseline Không Học Máy trên ảnh thật** | Chạy thử pipeline cổ điển trên `E18.1` và `E18.9` để có thước đo cơ sở (Baseline). | - Bản gốc độ phân giải cao của `E18.1` / `E18.9` (ảnh 1070 × 765 px hiện có chỉ đủ cho smoke test).<br>- Pipeline chuẩn hóa tỉ lệ + `Morphology + HoughLinesP + Skeleton` trích dây + phân loại điểm giao bằng luật hình học.<br>- Tích hợp `PaddleOCR` (có xoay chữ dọc $90^\circ$) + ghép nhãn Hungarian + luật mã EVN.<br>- Báo cáo số liệu baseline theo 4 mức đánh giá trên `E18.1`, dùng để chốt ngưỡng nghiệm thu. |
| **Giai đoạn 4: Gán nhãn Tập thực tế & Huấn luyện Detector** | Giúp mô hình nhận diện chính xác mọi ký hiệu và trạng thái đóng/mở. | - Gán nhãn tay 5–10 bản vẽ thực tế (`E18.1`, `E18.9`...) làm Gold Set, chia `dev` / `test`.<br>- Fine-tune `Faster R-CNN` / `YOLOv8+SAHI` trên 100 ảnh tổng hợp + một phần ảnh thật.<br>- Đạt `Recall ≥ 90%` trên các ký hiệu chính, đọc đúng mã ≥ 95%, F1 ghép nhãn ≥ 90% (mục tiêu đề xuất). |
| **Giai đoạn 5: Hoàn thiện Lắp ráp Đồ thị & Thử nghiệm U-Net** | Kết nối toàn bộ Ký hiệu + Cổng + Dây + OCR thành Đồ thị hoàn chỉnh. | - Ghép dây vào cổng (gán toàn cục), dựng Net theo `netBehavior`, hậu kiểm luật EVN.<br>- Thực nghiệm so sánh `Baseline Hough` với `U-Net` (và luật hình học với CNN điểm giao) trên chỉ số **Terminal-pair F1**. U-Net chỉ thay baseline nếu tăng ít nhất 2 điểm. |
| **Giai đoạn 6: Xuất SVG / AutoCAD (`.dxf`) & Giao diện Kiểm duyệt (UI)** | Đóng gói thành sản phẩm hoàn chỉnh cho người dùng cuối. | - Bộ xuất song song `SVG tương tác` (tô vùng mang điện) và `AutoCAD .dxf` (dùng `ezdxf` với đầy đủ Block, Attribute, Layer, Polyline, đơn vị mm).<br>- Giao diện Web chồng kết quả lên ảnh gốc, liệt kê các issue và vị trí `reviewStatus: pending` để kỹ sư xác nhận hoặc sửa nhanh. Mỗi lần sửa thì dựng lại Net và kiểm tra lại; nhật ký sửa được lưu để đo thời gian hiệu chỉnh. |

---

## 6. Việc Cần Làm Ngay Tiếp Theo (Next Steps)

1. **Tập hợp các file PDF bản vẽ thực tế** (`E18.9.pdf`, `E18.1`, và các file có sơ đồ trạm) vào một thư mục `data/raw_pdfs/` trong workspace. Ưu tiên **bản gốc độ phân giải cao** của `E18.1`.
2. **Trích xuất bộ 17 ký hiệu chuẩn SVG** từ `E18.1` / `E18.9`, vẽ 17 block DXF tương ứng (hệ Y hướng lên) và khai báo `symbols_metadata.json` (tọa độ `ports`, `netBehavior`, `conduction`, hướng hợp lệ).
3. **Viết nguyên mẫu (Prototype) script Python `EVN Bay Generator`** cho 1 sơ đồ rút gọn của `E18.1` / `E18.9` (1 thanh cái 110kV, 1 MBA, 2 ngăn lộ). Mục đích là kiểm chứng việc xuất đồng thời **Ảnh PNG + BBox + Graph JSON + SVG + DXF** và vòng kiểm tra đọc lại, trước khi nhân bản lên 100 ảnh.
4. **Hỏi chuyên môn để chốt các điểm còn mở** (SDD mục 13):
   - Ý nghĩa chữ số thứ hai của mã máy cắt và vai trò nhãn `T301`/`T302`.
   - Danh sách đầy đủ hậu tố dao cách ly / dao tiếp địa.
   - Quy ước vẽ điểm nối và giao không nối trên các bản vẽ ngoài E18.1.
