# Kế hoạch thực hiện: Số hóa sơ đồ nhất thứ trạm biến áp (EVN) → Đồ thị + SVG + DXF

## Context

Thư mục dự án hiện **chỉ có tài liệu, chưa có mã nguồn**. Thiết kế đã khá chín:
- `thiet_ke_he_thong_sdd.md` (SDD v1.1): schema `SubstationTopologyGraph`, 17 lớp ký hiệu, interface từng khâu, thuật toán, tiêu chí nghiệm thu, cây thư mục `src/`.
- `bao_cao_dinh_huong_phat_trien_san_pham.md`: lộ trình 6 giai đoạn.

Mục tiêu của kế hoạch: biến SDD thành chuỗi công việc lập trình cụ thể cho cả 6 giai đoạn, có tiêu chí xong cho từng giai đoạn, đồng thời bổ sung những gì tìm được khi khảo sát dữ liệu thật và tài liệu bên ngoài. Dự án là nghiên cứu/nội bộ, nên dùng được cả YOLO (AGPL) để đối chứng.

## Phát hiện mới (cần đưa vào thiết kế)

**1. Dữ liệu trong `data mẫu/` đều là PDF vector xuất từ CAD** (kiểm tra bằng PyMuPDF):

| File | Loại | Ghi chú |
|---|---|---|
| `E18.9.pdf` | **A** – sơ đồ nhất thứ trạm Cẩm Xuyên | A4 dọc, bản vẽ xoay 90°; 3.511 path vector; **chữ đã bị chuyển thành nét vẽ** (không có lớp text); 7 ảnh nhúng (dấu, chữ ký) |
| `ĐZ 371, 375 E18.9.pdf` | B – lưới phân phối | 14.478 path, có một ít text (chiều dài đoạn dây) |
| `PTT 89/90/91/92 …pdf` | B – phiếu thao tác | Trang 1–2 là văn bản có lớp text, trang 3 là sơ đồ vector |

Hệ quả:
- Raster hóa ở DPI tùy ý, nên **không còn vấn đề ảnh độ phân giải thấp** với các file này.
- Khổ giấy là **A4, không phải A3**: `pxPerMm` lấy từ kích thước trang PDF, không giả định A3. Phải xử lý trang xoay (bản vẽ ngang đặt trên trang dọc).
- **Path vector là nguồn nhãn gần như miễn phí** cho dây, thanh cái và hình học ký hiệu. Pipeline vẫn nhận ảnh raster theo quyết định đã chốt, nhưng công cụ gán nhãn được phép đọc vector để tạo nhãn nháp chính xác tới từng pixel.
- **Mới có 2 bản vẽ Loại A** (E18.9 vector, E18.1 chỉ có ảnh 1070×765). Gold set cần 5–10 bản, nên **thu thập thêm bản vẽ Loại A là việc phải làm song song từ tuần đầu**.

**2. Quy tắc đánh số EVN đã có văn bản pháp lý: Thông tư 44/2014/TT-BCT, Chương IV (Điều 40–61).** Văn bản này giải quyết phần lớn câu hỏi mở số 1–2 ở SDD mục 13:
- Chữ số cấp điện áp (Điều 42): 5=500, 2=220, 1=110, 7=66, 3=35, 4=22, 8=15, 9=10, 6=6 kV.
- Thanh cái (Điều 43): `C` + cấp áp + số thứ tự; số 9 là thanh cái vòng (C29).
- Máy cắt (Điều 58): ký tự 1 là cấp áp (máy cắt tụ dùng `T`, máy cắt kháng dùng `K`). Ký tự 2: 3 = MBA, 7–8 (hoặc 5–8) = đường dây, 4 = MBA tự dùng, 0 = tụ bù ngang/máy phát. Máy cắt liên lạc dùng số của hai thanh cái (112, 312, 412); máy cắt vòng là `x00`.
  → `T301`/`T302` là **máy cắt tụ bù 35 kV**, khớp giả định trong SDD.
- Dao cách ly (Điều 59): `<thiết bị>-<hậu tố>`. Hậu tố: số thanh cái (1, 2), 7 = đường dây, 3 = MBA, 9 = thanh cái vòng, 0/9 = nối tắt.
- **Dao tiếp địa (Điều 60): hậu tố = hậu tố DCL kề nó + 1 chữ số vị trí**: 4 = thanh cái, 5 = máy cắt, 6 = đường dây/tụ, 8 = MBA/kháng/TU; `08` = trung tính.
  Ví dụ: `171-15` là tiếp địa của DCL `-1` phía máy cắt; `171-76` là tiếp địa của DCL `-7` phía đường dây; `431-38` là tiếp địa của DCL `-3` phía MBA; `TUC41-14` là tiếp địa của DCL `-1` phía thanh cái.
  → Danh sách hậu tố liệt kê cứng trong `evn_codes.yaml` nên thay bằng **ngữ pháp sinh**: `earthing = <dcl_suffix><4|5|6|8> | 08`.
- TU/TI/CS (Điều 54–56): `TU`/`TI`/`CS` + tên thiết bị đấu vào. `CS0T1` là chống sét trung tính. Cầu chì (Điều 57): `CC-<thiết bị>` hoặc `FCO-…`.
- Trên E18.9 có các biến thể chưa có trong grammar: `SI-TD31`, `CCTUC31` (thiếu dấu `-`), `CS0T1`, `CS3T1`, `CS4T1`, `131-35`, các số cột/vị trí cạnh mũi tên đường dây (`02`, `106`). Grammar cần có vai trò `pole_number`, và bước chuẩn hóa phải chấp nhận `CC` không có gạch.

**3. Tài liệu bên ngoài bổ sung:**
- *Intelligent Digitization of Substation One-Line Diagrams Based on Computer Vision* (Chen Yang et al., IEEE TPWRD). Pipeline 3 mô-đun (phần tử, nhãn, topology), sát bài toán nhất; nên đọc trước khi làm Giai đoạn 3.
- PID2Graph (arXiv 2411.13929, Zenodo 14803338). Dataset tải được, chưa thấy code Relationformer công khai, nên chỉ dùng làm tham khảo schema và đánh giá.
- PaddleOCR PP-OCRv5: chưa có mô hình nhận dạng riêng cho tiếng Việt; tiếng Việt có vẻ thuộc nhóm `latin_PP-OCRv5_mobile_rec` nhưng tài liệu không nói rõ. Mã thiết bị là ASCII nên không ảnh hưởng. Tên lộ có dấu (`Cầu Phủ`, `Thiên Cầm`) cần đo riêng; nếu kém thì có thể dùng VietOCR cho riêng chữ có dấu.

**4. Môi trường máy:** Python 3.11 (`py -3.11`) đã có `torch`, `pydantic`, `pymupdf`, `pypdfium2`; chưa có `opencv`, `paddleocr`, `ezdxf`. GPU RTX 4060 Laptop **8 GB**, vừa đủ mức tối thiểu của SDD, nên Faster R-CNN cần AMP và batch nhỏ. Thư mục chưa phải git repo.

---

## Giai đoạn 0 – Khởi tạo dự án và dữ liệu (≈ 1 tuần)

1. `git init`, `.gitignore` (bỏ `data/synthetic/`, `tmp/`, trọng số mô hình), `pyproject.toml` + venv Python 3.11, `requirements.lock`.
2. Cây thư mục theo SDD mục 4. Chuyển `data mẫu/` sang `data/raw_pdfs/` (tên không dấu): `typeA/E18.9.pdf`, `typeB/…`.
3. `scripts/rasterize_pdf.py`: `pypdfium2` → PNG ở 8 px/mm theo khổ trang thật, tự xoay trang ngang, ghi `page_meta.json` (`pxPerMm`, `pxPerMmSource: pdf_page_size`, góc xoay).
4. `scripts/extract_vector.py` (PyMuPDF `get_drawings()`): xuất đoạn thẳng, cung, hình chữ nhật đã đổi về tọa độ ảnh. Dùng làm nguồn nhãn nháp (Giai đoạn 1 và 4).
5. **Cập nhật SDD lên v1.2** với các phát hiện ở trên: khổ A4, quy tắc TT 44/2014, nguồn nhãn vector. Đóng các mục SDD 13.1–13.2 có trích dẫn điều khoản.
6. Việc song song suốt dự án: xin thêm bản vẽ Loại A (PDF CAD) từ các trạm khác của PC Hà Tĩnh; mục tiêu ≥ 10 bản trước Giai đoạn 4.

**Xong khi:** `rasterize_pdf.py` cho ra PNG của E18.9 đúng chiều với `pxPerMm` ≈ 8; `extract_vector.py` vẽ chồng được các đoạn vector lên PNG khớp từng pixel.

## Giai đoạn 1 – Tầng lõi: schema, thư viện ký hiệu, ngữ pháp mã, NetBuilder (≈ 2–3 tuần)

| File | Nội dung | Theo SDD |
|---|---|---|
| `src/core/schema.py` | Pydantic v2 cho `SubstationTopologyGraph` v1.1, tham chiếu có kiểu (discriminated union theo `kind`), chỉ mục ngược `ref → netId`, kiểm tra 6 bất biến | 2.4, 6.2, 6.3 |
| `src/core/types.py`, `interfaces.py`, `registry.py` | Dataclass trung gian, `Protocol` từng khâu, ánh xạ tên trong config → class | 7.1, 7.2 |
| `src/core/geometry.py` | Chiếu cổng `X = c + s·R(θ)·M·u`, quy đổi CAD (lật Y, px → mm) | 2.2, 2.3 |
| `assets/symbols_svg/*.svg` (17 file) | Vẽ lại từng ký hiệu bằng tay, **lấy hình từ path vector của E18.9** (cắt vùng, chuẩn hóa về tâm, đơn vị `lu`) | 5 |
| `assets/symbols_metadata.json` | 17 lớp: size, ports, `netBehavior`, `conduction`, hướng | 5, 6.1 |
| `scripts/build_symbols_dxf.py` → `assets/symbols.dxf` | Sinh 17 block **tự động từ SVG** (lật Y, `ezdxf`, có ATTDEF), không vẽ tay hai lần | 2.3, 8.4.2 |
| `src/core/symbol_lib.py` | Nạp metadata, `PortProjector` | 8.2.3 |
| `configs/evn_codes.yaml` + `src/core/evn_codes.py` | Ngữ pháp mã viết lại theo TT 44/2014: bảng cấp áp đầy đủ, hậu tố tiếp địa sinh theo luật, thêm `pole_number`, `SI-`, `CC` không gạch; có chiều sinh ngược để bộ sinh dùng | 8.2.4 |
| `src/core/net_builder.py` | Union-Find dựng Net theo `netBehavior`, gán `kind`/`voltageKv`, đồ thị thiết bị, `Energizer` BFS | 8.3.3 |

Test: `test_geometry_svg_dxf.py` (17 lớp × mọi góc × lật, sai số < 1e-6 mm), `test_evn_codes.py` (bảng nhãn thật từ **cả E18.1 và E18.9**, ca lỗi OCR), `test_net_builder.py` (6 tình huống SDD 10.3).

**Xong khi:** `pytest` xanh; mở `symbols.dxf` bằng ezdxf/viewer thấy 17 block đúng hướng; mọi nhãn đọc được bằng mắt trên E18.1/E18.9 đều phân tích ra đúng vai trò.

## Giai đoạn 2 – Bộ xuất và bộ sinh dữ liệu tổng hợp (≈ 4 tuần)

Làm **lát cắt dọc trước**, theo nguyên tắc SDD 1.1-5: 1 sơ đồ rút gọn (1 thanh cái 110 kV, 1 MBA, 2 ngăn) → graph → SVG/DXF/PNG → đọc lại và kiểm tra. Chạy đúng rồi mới nhân rộng.

1. `src/exporters/svg_exporter.py`: `<defs><symbol>`, các layer, `data-*`, nhúng graph JSON, JS bật/tắt layer và BFS tô màu (port từ `Energizer`, có test so khớp kết quả Python ↔ JS chạy bằng Node).
2. `src/exporters/dxf_exporter.py`: theo SDD 8.4.2 (layer từ `configs/cad_layers.yaml`, INSERT + ATTRIB, LWPOLYLINE, HATCH chấm, ARC cầu vượt, đám mây `AI_REVIEW`, `doc.audit()`).
3. `src/generator/bay_templates.py`: 6 mẫu ngăn trong hệ chuẩn, mã sinh từ `EVNCodeGrammar`.
4. `topology_sampler.py` → `layout_composer.py` (preset `E18_like`, thêm `E18_9_like` theo bố cục thật của E18.9) → `router.py` (A* trực giao trên lưới thô, phạt rẽ và cắt, sinh `crossing_no_connect`/`junction`).
5. `renderer.py`: SVG → PNG bằng `resvg` (`resvg-py`; `cairosvg` khó cài trên Windows), biến thiên nét/font/nhiễu, khung tên, dấu đỏ, chữ ký làm vật gây nhiễu.
6. `dataset_writer.py` + `scripts/generate_substation_dataset.py` + `scripts/validate_dataset.py` (8 tiêu chí SDD 8.1.5), `split.json` chia theo topology, `manifest.json`.

Test: `test_generator_and_exporters.py`; mở thủ công 3 DXF mẫu trong AutoCAD/DWG TrueView (nếu có) hoặc LibreCAD.

**Xong khi:** đạt mốc SDD 9.3-GĐ2: 100/100 mẫu qua `validate_dataset`; round-trip DXF có số INSERT = số thiết bị, sai số cổng < 0,01 mm. So bằng mắt: ảnh tổng hợp trông "giống họ" E18.1/E18.9.

## Giai đoạn 3 – Baseline cổ điển trên ảnh thật (≈ 4–5 tuần)

Thứ tự cài đặt theo mức phụ thuộc SDD mục 3 (bảng "Thứ tự phụ thuộc"):
1. `scale_normalizer.py`: PDF lấy `pxPerMm` theo trang; ảnh raster ước lượng từ độ dày nét (distance transform).
2. **Detector tạm thời không học máy**: template matching đa góc dùng chính 17 SVG (NCC), đủ cho baseline. Đặt sau interface `SymbolDetector`, Giai đoạn 4 thay bằng mô hình học.
3. `orientation.py` (NCC 8 tổ hợp cho lớp bất đối xứng) + `PortProjector`.
4. `ocr_engine.py`: PaddleOCR 3.x PP-OCRv5 (`paddlepaddle-gpu` trên Windows; nếu cài lỗi thì chạy CPU), xử lý chữ dọc 90/270, chạy theo tile. Đo riêng chữ có dấu; nếu kém, thêm VietOCR cho vai trò `line_name`.
5. `label_matcher.py`: Hungarian có hàng/cột "không ghép" (`scipy.optimize.linear_sum_assignment`).
6. `wire_classical.py` (che ký hiệu và chữ → morphology → HoughLinesP → skeleton `skimage` → truy vết → Douglas–Peucker), `busbar_classifier.py`, `junction_classifier.py` (luật hình học, gồm `hop_arc`).
7. `port_matcher.py` (chi phí có dấu hướng, gán toàn cục), `assembler.py`, `graph_validator.py` (bảng issue SDD 8.3.4 + luật TT 44/2014).
8. `src/evaluation/graph_matching.py` + `metrics.py` + `scripts/run_pipeline.py`, `scripts/evaluate_metrics.py`.
9. **Đáp án thật đầu tiên**: gán nhãn E18.9 bằng công cụ bán tự động. `extract_vector.py` cho dây/thanh cái chính xác; người chỉ thêm loại ký hiệu, mã và sửa đấu nối trong CVAT hoặc viewer SVG đơn giản. Xuất ra `data/real_gold/dev/E18.9_graph.json`.

Test: `test_graph_matching.py`, `test_pipeline_e18_1.py` (smoke test trên ảnh 1070×765 phải có `low_resolution_input`), round-trip 10 mẫu tổng hợp (SDD 10.7).

**Xong khi:** pipeline chạy hết trên E18.9 và ảnh E18.1, xuất JSON + SVG + DXF hợp lệ; báo cáo số liệu baseline đủ 4 mức đánh giá; **chốt lại ngưỡng nghiệm thu** của SDD 9.3 dựa trên số liệu này.

## Giai đoạn 4 – Gold set và huấn luyện detector (≈ 4 tuần, phụ thuộc số bản vẽ thu được)

1. Hướng dẫn gán nhãn `docs/annotation_guide.md` (bbox ôm sát ký hiệu, không ôm nhãn; vùng ignore; lớp đóng/mở tách riêng). Gán thử 1 trang để đo công.
2. Gán nhãn 5–10 bản vẽ Loại A: vector cho nhãn nháp → người sửa. Chia `dev`/`test` **theo trạm**; `test` khóa, không dùng để chỉnh tham số.
3. `symbol_detector.py`, hai cài đặt cùng interface:
   - Faster R-CNN R50-FPN v2 (TorchVision, trọng số COCO), tile 1024, AMP, batch 2.
   - YOLOv8/11 + SAHI để đối chứng.
   Pretrain trên 100 → 1.000 ảnh tổng hợp, rồi fine-tune trên dev thật. So sánh chiến lược COCO→thật và COCO→tổng hợp→thật.
4. `tiler.py` gộp box bằng WBF (`ensemble-boxes`), thêm lượt 0,5× cho lớp MBA.
5. Quy trình huấn luyện: cho học thuộc 2–5 patch trước, dừng sớm theo recall từng lớp, lưu `manifest.json`.

**Xong khi:** đạt mốc SDD 9.3-GĐ4 trên `test`: recall ≥ 0,90 các lớp chính, exact match mã ≥ 0,95, F1 ghép nhãn ≥ 0,90 (hoặc ngưỡng đã chốt lại ở GĐ3).

## Giai đoạn 5 – Hoàn thiện đồ thị và thử U-Net / CNN điểm giao (≈ 3–4 tuần)

1. Chạy pipeline đầy đủ với detector đã học, sửa lỗi lắp ráp theo `issues` hay gặp nhất.
2. `wire_unet.py`: U-Net (`segmentation_models_pytorch`), patch 512, loss weighted BCE + Dice, sau đó thử soft-clDice. Mask đáp án lấy từ bộ sinh và từ vector của bản vẽ thật.
3. CNN điểm giao 4 lớp trên crop 64×64 lấy từ ảnh gốc.
4. Thí nghiệm đối chứng: Hough so với U-Net, luật so với CNN, đo bằng **Terminal-pair F1**. U-Net chỉ thay baseline khi tăng ≥ 2 điểm.

**Xong khi:** mốc SDD 9.3-GĐ5: Terminal-pair F1 ≥ 0,90, ≥ 70% ngăn lộ đúng hoàn toàn, có bảng kết quả thí nghiệm đối chứng.

## Giai đoạn 6 – Giao diện duyệt và đóng gói (≈ 3–4 tuần)

1. `src/review_ui/api.py` (FastAPI): tải graph, nhận `EditOperation`, chạy lại NetBuilder + Validator, trả issue.
2. `review_ui/web/`: ảnh gốc + lớp SVG chồng (dùng lại `svg_exporter`), danh sách issue, bảng thuộc tính, các thao tác sửa ở SDD 8.5, nhật ký `*_edits.jsonl`.
3. Chặn xuất bản vẽ khi còn issue mức `error`; tùy chọn chuyển `.dwg` bằng ODA File Converter.
4. Dùng nhật ký sửa làm nhãn bán tự động cho vòng gán nhãn tiếp theo.

**Xong khi:** mốc SDD 9.3-GĐ6: trung vị thời gian sửa ≤ 15 phút/bản vẽ cỡ E18.1; kỹ sư mở DXF của 3 bản vẽ mẫu trong AutoCAD và xác nhận block, thuộc tính đúng.

---

## Rủi ro chính và cách xử lý

| Rủi ro | Xử lý |
|---|---|
| Ít bản vẽ Loại A thật (hiện có 2) | Thu thập từ tuần 1; dùng nhãn vector để giảm công gán; dựa nhiều vào dữ liệu tổng hợp; nếu chỉ có ≤ 3 bản thì GĐ4 dùng cross-validation theo trạm thay cho tập test cố định |
| Khoảng cách giữa ảnh tổng hợp và ảnh thật | Lấy ký hiệu từ chính vector E18.9; preset bố cục theo bản vẽ thật; đo round-trip trên ảnh thật ngay từ GĐ3 |
| PaddlePaddle GPU trên Windows khó cài | Cho phép chạy OCR trên CPU (OCR không phải nút thắt thời gian); hoặc dùng WSL2 |
| GPU 8 GB | AMP, batch 2, tile 1024; nếu thiếu thì dùng tile 800 hoặc Colab/thuê GPU khi huấn luyện |
| Quy ước vẽ khác nhau giữa các đơn vị | Cấu hình trong `configs/`; thêm lớp biến thể thay vì sửa lớp cũ (SDD 13) |

## Kiểm chứng tổng thể

- Mỗi giai đoạn có tiêu chí "Xong khi" ở trên, và `pytest` phải xanh trước khi chuyển giai đoạn.
- Lệnh end-to-end (từ GĐ3): `python scripts/run_pipeline.py data/raw_pdfs/typeA/E18.9.pdf --out out/E18.9/` → mở `graph.json`, `E18.9.svg` (trình duyệt: click máy cắt thấy tô màu đổi), `E18.9.dxf` (AutoCAD/LibreCAD).
- `python scripts/evaluate_metrics.py --split real_gold/dev` in bảng chỉ số 4 mức; lưu kết quả để so hồi quy (không được tụt quá 2 điểm).

## Tổng thời gian ước lượng
Khoảng **21–26 tuần** cho 1 người làm toàn thời gian. GĐ0–2 (≈ 7–8 tuần) không phụ thuộc dữ liệu thật, nên trong thời gian đó thu thập thêm bản vẽ.

## Nguồn tham khảo bên ngoài đã dùng
- Thông tư 44/2014/TT-BCT, Chương IV: https://caselaw.vn/van-ban-phap-luat/264311-thong-tu-44-2014-tt-bct-quy-dinh-quy-trinh-thao-tac-trong-he-thong-dien-quoc-gia-do-bo-truong-bo-cong-thuong-ban-hanh (cần kiểm tra văn bản hợp nhất/hiệu lực hiện hành trên luatvietnam.vn)
- PID2Graph / Relationformer: https://arxiv.org/abs/2411.13929, https://zenodo.org/records/14803338
- Substation one-line diagram CV digitization (Chen Yang et al.): https://bohrium.dp.tech/paper/arxiv/619b7c071c45e57ce9b5aa9d
- PaddleOCR PP-OCRv5 đa ngôn ngữ: https://www.paddleocr.ai/latest/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.html
- Sinh sơ đồ từ CIM/E (arXiv 1903.09495): https://arxiv.org/abs/1903.09495
