"""Chạy pipeline nhận dạng trên một bản vẽ: ảnh/PDF → đồ thị JSON + SVG tương tác + DXF (SDD 3).

    python scripts/run_pipeline.py data/raw_pdfs/typeA/E18.9.pdf --out output/pipeline/E18.9
    python scripts/run_pipeline.py data/processed/E18.9/page.png --out output/pipeline/E18.9
    python scripts/run_pipeline.py data/raw_images/E18.1_low_res.png --out output/pipeline/E18.1
    python scripts/run_pipeline.py data/synthetic/v001_n100/images/0003.png --text-reader ground_truth

Ghi <id>_graph.json, <id>.svg, <id>.dxf, <id>_page.png (ảnh đã chuẩn hóa tỉ lệ) và <id>_log.json
(thời gian từng khâu, số issue theo loại — SDD 11).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.pipeline import CONFIG_PATH, Pipeline, export_result, load_input  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="PDF (khai báo trong raw_pages.yaml) hoặc ảnh")
    parser.add_argument("--out", type=Path, help="Thư mục kết quả (mặc định output/pipeline/<id>)")
    parser.add_argument("--id", help="Mã trang (mặc định suy từ đầu vào)")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--text-reader", help="Ghi đè pipeline.text_reader (vd. ground_truth)")
    parser.add_argument("--gt-graph", type=Path, help="Đồ thị đáp án cho text_reader ground_truth")
    args = parser.parse_args()

    inp = load_input(args.input, args.id)
    overrides: dict = {}
    if args.text_reader:
        overrides["pipeline"] = {"text_reader": args.text_reader}
        if args.text_reader == "ground_truth":
            gt = args.gt_graph or args.input.parent.parent / "graphs" / f"{args.input.stem}_graph.json"
            overrides["text_reader_args"] = {"graph_path": str(gt)}
    pipe = Pipeline(args.config, overrides)
    result = pipe.run(inp.image, inp.page_id, inp.source_file, inp.page_size_mm, log=print)
    out = args.out or Path("output/pipeline") / inp.page_id
    paths = export_result(result, out, inp.page_id)
    log = json.loads(paths["log"].read_text(encoding="utf-8"))
    print(json.dumps({k: log[k] for k in ("counts", "issuesByType", "timingsSec")}, ensure_ascii=False, indent=1))
    for k, p in paths.items():
        print(f"{k:6s} {p}")


if __name__ == "__main__":
    main()
