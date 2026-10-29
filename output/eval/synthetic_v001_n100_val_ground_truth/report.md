# Đánh giá baseline GĐ3 — synthetic/v001_n100:val

- Số bản vẽ: 10
- split: synthetic/v001_n100:val
- text_reader: ground_truth
- config: inference_config.yaml
- thời gian trung bình / bản vẽ: 40.4 s

| Mức | Chỉ số | Giá trị |
|---|---|---|
| 1. Ký hiệu | mAP@0.5 | 0.945 |
| | mAP@0.5:0.95 | 0.766 |
| | Độ chính xác hướng (lớp bất đối xứng) | 94.9% (n=592) |
| | Ghép được nhưng sai loại | 10 |
| 2. Chữ & nhãn | Exact match mã (thiết bị + thanh cái) | 100.0% (n=1094) |
| | Exact match mọi chữ (trừ khung tên) | 98.7% |
| | Ghép nhãn P / R / F1 | 0.999 / 0.949 / 0.974 |
| | Thiết bị đúng mã | 94.9% |
| 3. Dây | Dice / IoU mask | 0.769 / 0.625 |
| | Net bị tách / bị dính (trên 456 Net đáp án) | 96 / 20 |
| | Recall nút / độ chính xác loại nút | 76.0% / 99.0% |
| | Recall giao không nối | 58.3% (n=12) |
| 4. Đồ thị | Terminal-pair P / R / F1 | 0.704 / 0.748 / 0.725 |
| | Ngăn lộ đúng hoàn toàn | 57.1% (120/210) |

## Recall và AP@0.5 theo lớp

| Lớp | Đáp án | Dự đoán | Recall | AP@0.5 |
|---|---|---|---|---|
| `earthing_switch_open` | 297 | 306 | 98.7% | 0.980 |
| `current_transformer` | 164 | 164 | 100.0% | 1.000 |
| `disconnector_closed` | 103 | 84 | 73.8% | 0.718 |
| `disconnector_open` | 101 | 149 | 100.0% | 1.000 |
| `line_feeder_arrow` | 98 | 157 | 100.0% | 1.000 |
| `cable_termination` | 98 | 131 | 91.8% | 0.906 |
| `circuit_breaker` | 88 | 88 | 100.0% | 1.000 |
| `drawout_breaker` | 76 | 68 | 89.5% | 0.891 |
| `surge_arrester` | 66 | 66 | 100.0% | 1.000 |
| `voltage_transformer` | 64 | 64 | 100.0% | 1.000 |
| `earthing_switch_closed` | 39 | 37 | 89.7% | 0.891 |
| `coupling_capacitor` | 28 | 29 | 100.0% | 0.978 |
| `fuse_disconnector` | 24 | 22 | 91.7% | 0.911 |
| `transformer_2w` | 16 | 16 | 100.0% | 1.000 |
| `ground_symbol` | 14 | 15 | 85.7% | 0.841 |
| `transformer_3w` | 8 | 8 | 100.0% | 1.000 |
| `spark_gap` | 0 | 23 | – | – |
