"""SDD 10.1: cổng chiếu qua SVG và qua INSERT của DXF phải trùng nhau cho mọi lớp × góc × lật."""
import itertools

import ezdxf
import pytest
from ezdxf import bbox as dxf_bbox

from scripts.build_symbols_dxf import DXF_PATH, PORT_LAYER, XDATA_APPID
from src.core.geometry import (ROTATIONS, Placement, cad_insert_params, from_cad_point, local_to_page,
                               placement_from_bbox, rotated_bbox_local, to_cad_point)
from src.core.symbol_lib import default_library
from src.core.symbol_svg import parse_symbol_svg, primitives_bounds

LIB = default_library()
PX_PER_MM = 8.0
PAGE_H = 2480.0
TOL_MM = 1e-6


@pytest.fixture(scope="module")
def dxf_doc():
    return ezdxf.readfile(DXF_PATH)


def _block_ports(doc, block_name):
    ports = {}
    for e in doc.blocks.get(block_name):
        if e.dxftype() == "POINT" and e.dxf.layer == PORT_LAYER:
            ports[e.get_xdata(XDATA_APPID)[0][1]] = e
    return ports


CASES = [(cls, rot, mirror, k) for cls in LIB.classes for rot in ROTATIONS for mirror in (False, True)
         for k in (1.0, 1.3)]


@pytest.mark.parametrize("cls,rot,mirror,k", CASES)
def test_port_svg_equals_dxf(dxf_doc, cls, rot, mirror, k):
    sym = LIB[cls]
    s = k * PX_PER_MM * LIB.library_unit_mm
    placement = Placement(1234.5, 876.25, rot, mirror, s)
    page_ports = {p.port_id: p.xy for p in LIB.project_ports(cls, placement)}

    msp = dxf_doc.modelspace()
    insert = msp.add_blockref(sym.dxf_block_name, to_cad_point(placement.x, placement.y, PAGE_H, PX_PER_MM),
                              dxfattribs=cad_insert_params(placement, PX_PER_MM, LIB.library_unit_mm))
    try:
        m = insert.matrix44()
        block_ports = _block_ports(dxf_doc, sym.dxf_block_name)
        assert set(block_ports) == set(page_ports)
        for pid, point in block_ports.items():
            cad = m.transform(point.dxf.location)
            x_px, y_px = from_cad_point(cad.x, cad.y, PAGE_H, PX_PER_MM)
            ex, ey = page_ports[pid]
            assert abs(x_px - ex) / PX_PER_MM < TOL_MM and abs(y_px - ey) / PX_PER_MM < TOL_MM, pid
    finally:
        msp.delete_entity(insert)


@pytest.mark.parametrize("cls", LIB.classes)
def test_metadata_bbox_matches_svg(cls):
    sym = LIB[cls]
    assert primitives_bounds(parse_symbol_svg(sym.svg_file)) == pytest.approx(sym.bbox_local)
    assert sym.size == pytest.approx((sym.bbox_local[2] - sym.bbox_local[0], sym.bbox_local[3] - sym.bbox_local[1]))


@pytest.mark.parametrize("cls", LIB.classes)
def test_dxf_block_is_svg_flipped(dxf_doc, cls):
    """Block DXF là hình SVG lật trục Y (vẽ ở hệ Y hướng lên), không phải bản sao nguyên."""
    sym = LIB[cls]
    shapes = [e for e in dxf_doc.blocks.get(sym.dxf_block_name)
              if e.dxftype() in ("LWPOLYLINE", "CIRCLE")]
    ext = dxf_bbox.extents(shapes)
    x0, y0, x1, y1 = sym.bbox_local
    u = LIB.library_unit_mm
    assert (ext.extmin.x, ext.extmin.y) == pytest.approx((x0 * u, -y1 * u))
    assert (ext.extmax.x, ext.extmax.y) == pytest.approx((x1 * u, -y0 * u))


@pytest.mark.parametrize("cls", LIB.classes)
def test_port_directions_point_outward(cls):
    sym = LIB[cls]
    cx = 0.5 * (sym.bbox_local[0] + sym.bbox_local[2])
    cy = 0.5 * (sym.bbox_local[1] + sym.bbox_local[3])
    for p in sym.ports:
        assert (p.local_x - cx) * p.direction[0] + (p.local_y - cy) * p.direction[1] > 0, p.id


@pytest.mark.parametrize("cls,rot,mirror", list(itertools.product(LIB.classes, ROTATIONS, (False, True))))
def test_placement_from_bbox_inverts_projection(cls, rot, mirror):
    sym = LIB[cls]
    truth = Placement(500.0, 400.0, rot, mirror, 1.5)
    x0, y0, x1, y1 = rotated_bbox_local(sym.bbox_local, rot, mirror)
    page_bbox = (truth.x + truth.scale * x0, truth.y + truth.scale * y0,
                 truth.scale * (x1 - x0), truth.scale * (y1 - y0))
    est = placement_from_bbox(page_bbox, sym.bbox_local, rot, mirror)
    assert (est.x, est.y, est.scale) == pytest.approx((truth.x, truth.y, truth.scale))


def test_rotation_is_clockwise_on_screen():
    """θ = 90 quay điểm phía trên tâm sang phía phải tâm (Y hướng xuống)."""
    assert local_to_page(0, -10, Placement(0, 0, 90)) == pytest.approx((10, 0))
    assert local_to_page(10, 0, Placement(0, 0, 0, mirror_x=True)) == pytest.approx((-10, 0))


def test_cad_point_round_trip():
    x, y = to_cad_point(800, 200, PAGE_H, PX_PER_MM)
    assert (x, y) == pytest.approx((100, 285))
    assert from_cad_point(x, y, PAGE_H, PX_PER_MM) == pytest.approx((800, 200))
