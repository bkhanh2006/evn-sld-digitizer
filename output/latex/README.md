# Báo cáo định hướng xử lý sơ đồ điện bằng Computer Vision

Báo cáo tiếng Việt về số hóa sơ đồ điện từ ảnh/PDF scan. Tài liệu là đề xuất thiết kế, chưa phải kết quả huấn luyện hoặc đánh giá một hệ thống đã triển khai.

## Tệp chính

- `bao_cao_dinh_huong_cv.tex`: nguồn LaTeX, có trích dẫn và danh mục tài liệu tham khảo.
- `sample_output.json`: đầu ra minh họa, không phải kết quả nhận dạng từ ảnh.
- `assets/`: hai ảnh người dùng cung cấp; báo cáo sử dụng ảnh sơ đồ làm minh họa.

## Biên dịch

Dùng **XeLaTeX** (không dùng pdfLaTeX), hoặc Tectonic. Nguồn UTF-8; font TeX Gyre Termes/Heros và Latin Modern Mono đi kèm các bản phân phối TeX thông dụng.

```sh
xelatex bao_cao_dinh_huong_cv.tex
xelatex bao_cao_dinh_huong_cv.tex
```

Hoặc:

```sh
tectonic bao_cao_dinh_huong_cv.tex
```

Chạy trong thư mục chứa tệp `.tex`. Cần giữ nguyên `assets/` và `sample_output.json`. Không cần BibTeX/Biber vì tài liệu tham khảo nằm trong `thebibliography`.

Với Overleaf: tải lên toàn bộ bộ nguồn, đặt Main document là `bao_cao_dinh_huong_cv.tex`, chọn compiler **XeLaTeX**.

## Phạm vi

Xác định bài toán; input/output; phân rã P1-P7; xử lý ảnh; so khớp; detection/classification/segmentation; OCR/VLM; dây và đồ thị; tích hợp JSON/SVG; đánh giá; lộ trình cho công ty nhỏ.

Các con số về phạm vi dữ liệu là gợi ý khảo sát, không phải cam kết độ chính xác. Các tài liệu trực tuyến được đối chiếu ngày 24/09/2026.
