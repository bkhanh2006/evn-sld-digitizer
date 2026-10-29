# Báo cáo baseline Giai đoạn 3 — pipeline cổ điển, không học máy

> Ngày: 29/10/2026 · SDD v1.5 · cấu hình `configs/inference_config.yaml` · máy: CPU (PaddleOCR chạy CPU, chưa dùng GPU)
>
> Lệnh tái lập:
> ```
> .venv/Scripts/python.exe scripts/evaluate_metrics.py --split real_gold/dev
> .venv/Scripts/python.exe scripts/evaluate_metrics.py --split synthetic/v001_n100:val --limit 10
> .venv/Scripts/python.exe scripts/evaluate_metrics.py --split synthetic/v001_n100:val --limit 10 --text-reader ground_truth
> ```
> Kết quả chi tiết (từng bản vẽ, từng ngăn) ở `output/eval/<bộ>/metrics.json` và `report.md`.

## 1. Cấu hình pipeline

| Khâu | Cài đặt GĐ3 |
|---|---|
| Chuẩn hóa tỉ lệ | `stroke_based`: pxPerMm theo khổ trang (PDF, dữ liệu sinh) hoặc ước lượng từ độ dày nét; resample khi ngoài [6, 16] px/mm |
| Ký hiệu | `template_ncc`: so khớp NCC đa góc với mẫu vẽ từ 19 SVG thư viện (SDD 8.2.2a) |
| Hướng | NCC trên mọi tổ hợp góc × lật, mẫu có dây từ cổng |
| Chữ | PaddleOCR 3.7: `PP-OCRv5_mobile_det` + `latin_PP-OCRv5_mobile_rec`, tile 1600 px, chữ dọc thử 90°/270° |
| Ghép nhãn | Hungarian có hàng/cột "không ghép", ràng buộc lớp theo ngữ pháp mã EVN |
| Dây | che ký hiệu + chữ → mở hình thái 0,8 mm + Hough xiên → skeleton → đồ thị điểm ảnh |
| Điểm giao | luật hình học (bậc, chấm đặc, khe cầu vượt) |
| Lắp ráp | PortMatcher (Hungarian, τ 0,60 / 1,50) → NetBuilder → EVNRuleValidator |

## 2. Bộ dữ liệu đánh giá

| Bộ | Nội dung | Ghi chú |
|---|---|---|
| **Thật — `real_gold/dev`** | E18.9 (12 px/mm, 3565 × 2521): 161 thiết bị, 77 Net, 22 ngăn, 103 chữ mã | Đáp án **bán tự động do Claude soát, chưa có kỹ sư duyệt** (SDD 8.6, 13.12). `real_gold/test` còn trống. |
| Tổng hợp, OCR thật | 10 mẫu val đầu của `v001_n100` (7,3–9,6 px/mm): 1046 thiết bị, 456 Net, 210 ngăn | Pipeline đầy đủ |
| Tổng hợp, chữ đáp án | như trên, `text_reader: ground_truth` | Cô lập phần ký hiệu + dây + lắp ráp (SDD 10.7) |
| Smoke E18.1 | ảnh 1070 × 765 px | Không có đáp án; chỉ kiểm chạy hết và xuất hợp lệ |

E18.1 bản độ phân giải cao chưa có, nên baseline thật dùng E18.9 thay cho E18.1 như kế hoạch viết.

## 3. Kết quả 4 mức

| Mức | Chỉ số | E18.9 (thật) | Tổng hợp, OCR thật | Tổng hợp, chữ đáp án |
|---|---|---|---|---|
| **1. Ký hiệu** | mAP@0.5 | **0,487** | 0,942 | 0,945 |
| | mAP@0.5:0.95 | 0,487 ¹ | 0,763 | 0,766 |
| | Recall lớp chính (MC, MC hợp bộ, DCL, DTĐ, TI) | **0,506** (41/81) | 0,948 | 0,950 |
| | Độ chính xác hướng (lớp bất đối xứng) | 91,7% (n=36) | 94,9% (n=588) | 94,9% (n=592) |
| | Ghép được nhưng sai loại | 22 | 9 | 10 |
| **2. Chữ & nhãn** | Exact match mã thiết bị + thanh cái | **78,6%** (n=103) | 56,4% (n=1094) | 100% |
| | Exact match mọi chữ (trừ khung tên) | 72,1% | 53,4% | 98,7% |
| | Ghép nhãn P / R / F1 | 0,807 / 0,469 / **0,594** | 0,733 / 0,506 / 0,598 | 0,999 / 0,949 / 0,974 |
| | Thiết bị đúng mã | 46,0% | 68,5% | 94,9% |
| **3. Dây** | Dice / IoU mask | **0,787** / 0,648 | 0,767 / 0,622 | 0,769 / 0,625 |
| | Net bị tách / bị dính | 13 / 2 (trên 77) | 97 / 20 (trên 456) | 96 / 20 (trên 456) |
| | Recall nút / độ chính xác loại nút | 78,6% / 100% | 76,1% / 99,0% | 76,0% / 99,0% |
| | Recall giao không nối | 1/2 | 58,3% (n=12) | 58,3% (n=12) |
| **4. Đồ thị** | Terminal-pair P / R / F1 | 0,830 / 0,357 / **0,499** | 0,755 / 0,745 / **0,750** | 0,704 / 0,748 / 0,725 |
| | Ngăn lộ đúng hoàn toàn | **0/22** | 21,0% (44/210) | 57,1% (120/210) |
| Thời gian | giây / bản vẽ (CPU) | 77 (OCR 62, detector 11) | 98 | 40 |

¹ Trên E18.9, bbox của thiết bị mà detector tìm đúng được lấy nguyên làm đáp án, nên mọi ngưỡng IoU cho cùng kết quả; phần IoU của mức 1 lệch có lợi cho pipeline. Recall, precision và các mức 2–4 không bị ảnh hưởng.

**Recall theo lớp trên E18.9:** máy cắt 100% (5), chống sét 100% (5), TU 62,5% (8), TI 64,7% (17), dao cách ly 61,5% (13), mũi tên đường dây 64,3% (14), máy cắt hợp bộ 50% (12), dao tiếp địa 32,4% (34), đầu cáp 11,5% (26), tụ ghép 0% (12), MBA 3 cuộn 0% (1).

### Smoke test E18.1 (1070 × 765)
Ước lượng 3,6 px/mm → phóng 2,78× lên 10 px/mm → issue `low_resolution_input` (error). Chạy hết 75 s, không khâu nào lỗi; xuất 156 thiết bị, 6 thanh cái, 1330 dây (phần lớn là mảnh vụn, 1041 đầu dây treo). JSON hợp lệ schema, `nets` nhất quán với NetBuilder, DXF `audit` sạch, 156 INSERT = 156 `<use>` trong SVG. Đúng như SDD: ảnh dưới mức tối thiểu, không đánh giá độ chính xác.

### Xuất bản vẽ E18.9
`run_pipeline.py data/raw_pdfs/typeA/E18.9.pdf`: JSON không vi phạm bất biến (sai số đầu dây ≤ 8 px), `nets` nhất quán, DXF `audit` sạch, 119 INSERT = 119 `<use>` = 119 thiết bị. Chưa ai mở DXF trong AutoCAD hay click SVG trong trình duyệt.

## 4. Phân tích lỗi

1. **Ký hiệu thật khác thư viện là nguyên nhân lớn nhất** (SDD 13.10). Trên E18.9: đầu cáp là chữ V hở, dao tiếp địa có vạch đất kiểu khác và đặt sát nhãn, máy cắt hợp bộ 22 kV có chevron đôi và nhỏ hơn ~20%, TU thanh cái là TU kiểu tụ cỡ nhỏ, MBA T1 có cuộn đậm và mũi tên điều áp. NCC chỉ đạt 0,72–0,85 nên phải chọn giữa recall và nhiễu. Đây đúng là phần GĐ4 (detector học) cần giải.
2. **Thiếu một thiết bị trên chuỗi ngăn làm dính hai Net** → nhiều cặp đầu cực sai cùng lúc (mẫu tổng hợp 0005: thiếu 11 máy cắt hợp bộ, precision Terminal-pair còn 0,36). Vì vậy precision đồ thị nhạy với recall ký hiệu hơn là với lỗi dây.
3. **Net bị tách** (~20% số Net) chủ yếu ở khe ngắn giữa hai thiết bị kề nhau và ở chỗ dây chạm sát bbox thiết bị lớn (dây bị che theo bbox). Luật nối cổng trống bù được một phần.
4. **OCR:** ASCII của mã đọc tốt khi chữ nằm ngang; lỗi chính là chữ dọc không được phát hiện (`371-76`… trên E18.9), hộp chữ dính ký hiệu (`TI 175 φ`), và chữ nhỏ trên ảnh tổng hợp 7–8 px/mm (56% exact match so với 79% trên E18.9 ở 12 px/mm). Tên lộ có dấu mất dấu (`Cẩm` → `Cm`): mô hình `latin_PP-OCRv5_mobile_rec` không giữ dấu tiếng Việt.
5. **Phần lắp ráp tự nó tốt:** với chữ đúng, ghép nhãn F1 0,974; độ chính xác loại nút 99%; trên mẫu 0000 (lúc chỉnh tham số) Terminal-pair F1 đạt 0,94 khi detector tìm đủ ký hiệu.

## 5. Đề xuất chốt lại ngưỡng nghiệm thu (SDD 9.3)

| Mốc | Ngưỡng cũ (đề xuất v1.1) | Baseline GĐ3 | Đề xuất chốt | Lý do |
|---|---|---|---|---|
| GĐ4 – detector | Recall ≥ 0,90 lớp chính | 0,51 thật / 0,95 tổng hợp | **Giữ ≥ 0,90** trên thật; thêm **mAP@0.5 ≥ 0,85** thật | Tổng hợp đã đạt bằng mẫu khớp tuyệt đối; khoảng cách thật–tổng hợp là việc của detector học |
| GĐ4 – OCR | Exact match mã ≥ 0,95 | 0,786 thật | **≥ 0,92** cho mã thiết bị/thanh cái ở GĐ4; nâng **0,95 ở GĐ6** | Lỗi còn lại cần phát hiện chữ dọc + tách hộp chữ dính ký hiệu (+ thử `server_det` trên GPU); tên lộ có dấu đo riêng, mục tiêu ≥ 0,80 |
| GĐ4 – ghép nhãn | F1 ≥ 0,90 | 0,594 thật; 0,974 khi chữ đúng | **Giữ ≥ 0,90** | Bộ ghép đã đủ tốt; F1 thấp do thiếu ký hiệu và chữ |
| GĐ5 – đồ thị | Terminal-pair F1 ≥ 0,90 | 0,499 thật / 0,750 tổng hợp | **≥ 0,85 trên thật, ≥ 0,90 trên tổng hợp (OCR thật)** | Thật có thêm ký hiệu ngoài danh mục (SDD 13.11) và đáp án chưa duyệt |
| GĐ5 – ngăn lộ | ≥ 70% đúng hoàn toàn | 0% thật / 21% tổng hợp | **≥ 60% thật, ≥ 75% tổng hợp** | Ngăn đúng hoàn toàn đòi đúng cả mã; baseline cho thấy mã là nút thắt |
| GĐ5 – dây (mới) | — | Net tách + dính = 19% thật / 26% tổng hợp | **≤ 5% số Net đáp án** | Chỉ số trực tiếp của WireExtractor + PortMatcher, độc lập với mã |
| GĐ5 – U-Net | +2 điểm Terminal-pair F1 | — | Giữ | |
| Thời gian | ≤ 10 phút/bản vẽ CPU | 77–98 s | Đạt; giữ | |

Điều kiện đi kèm: `real_gold/test` hiện trống và `dev` chỉ có một bản vẽ. Trước khi dùng các ngưỡng trên để nghiệm thu GĐ4 cần (a) kỹ sư duyệt đáp án E18.9, (b) thêm ít nhất 2 bản vẽ Loại A làm `test`; nếu chỉ có ≤ 3 bản thì đánh giá chéo theo trạm như kế hoạch đã ghi.

## 6. Việc còn nợ sau GĐ3

- Kỹ sư duyệt `data/real_gold/dev/E18.9_graph.json` (soát theo ảnh `output/gold/E18.9_review.png`).
- Mở DXF trong AutoCAD/LibreCAD và click thử SVG trong trình duyệt (nợ từ GĐ2, vẫn chưa làm).
- OCR chạy CPU; chưa cài `paddlepaddle-gpu`.
- Chưa dùng vector PDF cho đáp án dây (SDD 8.6); đáp án dây lấy từ mực qua cùng bộ trích dây.
