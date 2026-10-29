# Đánh giá baseline GĐ3 — real_gold/dev

- Số bản vẽ: 1
- split: real_gold/dev
- text_reader: theo cấu hình
- config: inference_config.yaml
- thời gian trung bình / bản vẽ: 77.8 s

| Mức | Chỉ số | Giá trị |
|---|---|---|
| 1. Ký hiệu | mAP@0.5 | 0.487 |
| | mAP@0.5:0.95 | 0.487 |
| | Độ chính xác hướng (lớp bất đối xứng) | 91.7% (n=36) |
| | Ghép được nhưng sai loại | 22 |
| 2. Chữ & nhãn | Exact match mã (thiết bị + thanh cái) | 78.6% (n=103) |
| | Exact match mọi chữ (trừ khung tên) | 72.1% |
| | Ghép nhãn P / R / F1 | 0.807 / 0.469 / 0.594 |
| | Thiết bị đúng mã | 46.0% |
| 3. Dây | Dice / IoU mask | 0.787 / 0.648 |
| | Net bị tách / bị dính (trên 77 Net đáp án) | 13 / 2 |
| | Recall nút / độ chính xác loại nút | 78.6% / 100.0% |
| | Recall giao không nối | 50.0% (n=2) |
| 4. Đồ thị | Terminal-pair P / R / F1 | 0.830 / 0.357 / 0.499 |
| | Ngăn lộ đúng hoàn toàn | 0.0% (0/22) |

## Recall và AP@0.5 theo lớp

| Lớp | Đáp án | Dự đoán | Recall | AP@0.5 |
|---|---|---|---|---|
| `earthing_switch_open` | 34 | 15 | 32.4% | 0.319 |
| `cable_termination` | 26 | 4 | 11.5% | 0.109 |
| `current_transformer` | 17 | 24 | 64.7% | 0.644 |
| `line_feeder_arrow` | 14 | 9 | 64.3% | 0.644 |
| `disconnector_open` | 13 | 17 | 61.5% | 0.572 |
| `drawout_breaker` | 12 | 6 | 50.0% | 0.505 |
| `coupling_capacitor` | 12 | 5 | 0.0% | 0.000 |
| `ground_symbol` | 9 | 5 | 55.6% | 0.554 |
| `voltage_transformer` | 8 | 5 | 62.5% | 0.624 |
| `circuit_breaker` | 5 | 11 | 100.0% | 1.000 |
| `surge_arrester` | 5 | 5 | 100.0% | 1.000 |
| `fuse_disconnector` | 3 | 1 | 33.3% | 0.337 |
| `transformer_2w` | 2 | 1 | 50.0% | 0.505 |
| `transformer_3w` | 1 | 0 | 0.0% | 0.000 |
| `spark_gap` | 0 | 10 | – | – |
| `shunt_capacitor` | 0 | 1 | – | – |
