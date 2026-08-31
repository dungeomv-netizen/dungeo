# -*- coding: utf-8 -*-
"""바코드 디코딩 (zxing-cpp). 흐린 사진 대비 확대/대비 보정 재시도."""
import zxingcpp
from PIL import Image, ImageOps


def _decode(img):
    try:
        res = zxingcpp.read_barcodes(img)
    except Exception:
        res = []
    out = []
    for r in res:
        t = (r.text or "").strip()
        if t:
            out.append(t)
    return out


def read_barcodes(img: Image.Image):
    """사진에서 바코드 문자열 리스트(중복제거, 우선순위 순)."""
    found = []

    def add(lst):
        for t in lst:
            if t not in found:
                found.append(t)

    base = img.convert("RGB")
    add(_decode(base))
    if not found:
        g = ImageOps.autocontrast(base.convert("L"))
        add(_decode(g))
    if not found:
        w, h = base.size
        big = base.resize((w * 2, h * 2))
        add(_decode(big))
    return found
