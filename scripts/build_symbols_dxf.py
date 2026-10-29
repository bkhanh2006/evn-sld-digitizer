"""Sinh assets/symbols.dxf (mỗi lớp một block) từ assets/symbols_svg/*.svg.

Block được vẽ ở hệ Y hướng lên, đơn vị mm: điểm cục bộ (u, v) lu → (u, −v) × libraryUnitMm
(SDD 2.3). Mỗi block có POINT đánh dấu cổng (layer SYM_PORTS, xdata = id cổng) để kiểm thử,
và ATTDEF cho các thuộc tính CAD khai báo trong metadata.

    python scripts/build_symbols_dxf.py                    # kiểm tra + ghi symbols.dxf
    python scripts/build_symbols_dxf.py --update-metadata  # ghi lại bboxLocal/size từ SVG trước
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import ezdxf
from ezdxf.document import Drawing

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.core.geometry import local_to_block_mm  # noqa: E402
from src.core.symbol_lib import ASSETS_DIR, METADATA_PATH  # noqa: E402
from src.core.symbol_svg import parse_symbol_svg, primitives_bounds, viewbox  # noqa: E402

DXF_PATH = ASSETS_DIR / "symbols.dxf"
PORT_LAYER = "SYM_PORTS"
XDATA_APPID = "SLD_DIGITIZER"
ATTDEF_HEIGHT_MM = 2.0
VISIBLE_ATTRIBUTES = {"DEVICE_CODE", "LINE_NAME"}
PORT_TOLERANCE_LU = 1e-6
_LINEWEIGHTS = (0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211)


def _lineweight(stroke_lu: float, unit_mm: float) -> int:
    target = stroke_lu * unit_mm * 100       # 1/100 mm
    return min(_LINEWEIGHTS, key=lambda w: abs(w - target))


def check_symbol(cls: str, meta: dict) -> tuple[float, float, float, float]:
    """Trả về bboxLocal của hình; báo lỗi nếu cổng nằm ngoài hình hoặc hình tràn viewBox."""
    svg_path = ASSETS_DIR / meta["svgFile"]
    prims = parse_symbol_svg(svg_path)
    x0, y0, x1, y1 = primitives_bounds(prims)
    for p in meta["ports"]:
        u, v = p["localX"], p["localY"]
        if not (x0 - PORT_TOLERANCE_LU <= u <= x1 + PORT_TOLERANCE_LU and y0 - PORT_TOLERANCE_LU <= v <= y1 + PORT_TOLERANCE_LU):
            raise ValueError(f"{cls}: cổng {p['id']} ({u}, {v}) nằm ngoài hình {x0, y0, x1, y1}")
        on_edge = min(abs(u - x0), abs(u - x1), abs(v - y0), abs(v - y1)) <= PORT_TOLERANCE_LU
        if not on_edge:
            raise ValueError(f"{cls}: cổng {p['id']} không nằm trên cạnh hộp bao (SDD 8.2.6 cần cổng ở biên)")
    vx0, vy0, vx1, vy1 = viewbox(svg_path)
    if x0 < vx0 or y0 < vy0 or x1 > vx1 or y1 > vy1:
        raise ValueError(f"{cls}: hình {x0, y0, x1, y1} tràn viewBox {vx0, vy0, vx1, vy1}")
    return x0, y0, x1, y1


def update_metadata(metadata: dict) -> None:
    for cls, meta in metadata["symbols"].items():
        x0, y0, x1, y1 = check_symbol(cls, meta)
        meta["bboxLocal"] = [round(v, 3) for v in (x0, y0, x1, y1)]
        meta["size"] = {"width": round(x1 - x0, 3), "height": round(y1 - y0, 3)}


def build_dxf(metadata: dict) -> Drawing:
    unit = float(metadata["libraryUnitMm"])
    doc = ezdxf.new(dxfversion="R2018", setup=True)
    doc.units = ezdxf.units.MM
    doc.appids.add(XDATA_APPID)
    port_layer = doc.layers.add(PORT_LAYER, color=8)
    port_layer.dxf.plot = 0

    for cls, meta in metadata["symbols"].items():
        x0, y0, x1, y1 = check_symbol(cls, meta)
        blk = doc.blocks.new(name=meta["dxfBlockName"])
        for prim in parse_symbol_svg(ASSETS_DIR / meta["svgFile"]):
            attribs = {"layer": "0", "lineweight": _lineweight(prim.stroke_width, unit)}
            if prim.kind == "circle":
                (cx, cy), r = prim.points[0], prim.radius
                blk.add_circle(local_to_block_mm(cx, cy, unit), r * unit, dxfattribs=attribs)
                continue
            pts = [local_to_block_mm(u, v, unit) for u, v in prim.points]
            blk.add_lwpolyline(pts, close=prim.closed, dxfattribs=attribs)
            if prim.filled:
                hatch = blk.add_hatch(dxfattribs={"layer": "0"})
                hatch.paths.add_polyline_path(pts, is_closed=True)

        for p in meta["ports"]:
            point = blk.add_point(local_to_block_mm(p["localX"], p["localY"], unit), dxfattribs={"layer": PORT_LAYER})
            point.set_xdata(XDATA_APPID, [(1000, p["id"])])

        # Thuộc tính CAD xếp dọc bên phải hình (Y lên: y0 cục bộ là cạnh trên)
        ax, ay = x1 * unit + 1.0, -y0 * unit
        for i, tag in enumerate(meta["cadAttributes"]):
            flags = 0 if tag in VISIBLE_ATTRIBUTES else ezdxf.const.ATTRIB_INVISIBLE
            blk.add_attdef(tag, insert=(ax, ay - (i + 1) * ATTDEF_HEIGHT_MM * 1.5),
                           dxfattribs={"height": ATTDEF_HEIGHT_MM, "flags": flags, "layer": "0"})
    return doc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update-metadata", action="store_true", help="Ghi lại bboxLocal và size từ SVG")
    args = parser.parse_args()

    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    if args.update_metadata:
        update_metadata(metadata)
        METADATA_PATH.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Đã cập nhật bboxLocal/size cho {len(metadata['symbols'])} lớp trong {METADATA_PATH.name}")

    doc = build_dxf(metadata)
    auditor = doc.audit()
    if auditor.has_errors:
        auditor.print_error_report()
        sys.exit("symbols.dxf có lỗi, không ghi file")
    doc.saveas(DXF_PATH)
    print(f"Đã ghi {DXF_PATH.relative_to(ASSETS_DIR.parent)} với {len(metadata['symbols'])} block")


if __name__ == "__main__":
    main()
