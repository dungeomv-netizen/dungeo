# -*- coding: utf-8 -*-
"""바코드 디코딩 (zxing-cpp). 흐린 사진 대비 확대/대비 보정 재시도.
- 상품 바코드(EAN-13/EAN-8/UPC)만 인정. QR/URL/기타는 걸러냄.
- 단, 한국 식품 QR(foodqr.kr/01/{GTIN}) 안엔 EAN이 들어있어 그건 추출해서 사용.
- 체크섬(검증숫자)으로 진짜 바코드인지 확인.
"""
import re
import zxingcpp
from PIL import Image, ImageOps


def gtin_valid(code):
    """EAN-8/UPC-A(12)/EAN-13/GTIN-14 체크섬 검증."""
    if not code or not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    ds = [int(c) for c in code]
    body = ds[:-1][::-1]
    s = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(body))
    return (10 - (s % 10)) % 10 == ds[-1]


def to_ean13(code):
    """유효한 GTIN을 시트 키(EAN-13 13자리)로 정규화. 아니면 None."""
    if not code or not code.isdigit():
        return None
    # 14자리(앞 0 포함) → 뒤 13자리가 EAN-13
    if len(code) == 14 and gtin_valid(code):
        cand = code[1:]
        if gtin_valid(cand):
            return cand
    if len(code) == 13 and gtin_valid(code):
        return code
    if len(code) == 12 and gtin_valid(code):   # UPC-A → 앞에 0 붙여 EAN-13
        return "0" + code
    if len(code) == 8 and gtin_valid(code):    # EAN-8 그대로
        return code
    return None


def extract_barcode(text):
    """디코딩된 문자열에서 '상품 바코드'만 뽑아냄.
    - 순수 숫자 GTIN → 정규화
    - 식품QR 등 URL 안의 /01/{GTIN} 또는 유효한 13/14자리 숫자열 → 추출
    - 그 외(일반 URL·QR) → None
    """
    t = (text or "").strip()
    if not t:
        return None
    # 1) 순수 숫자 바코드
    if t.isdigit():
        return to_ean13(t)
    # 2) GS1 QR: .../01/{14자리 GTIN}
    m = re.search(r"/01/(\d{14})", t)
    if m:
        e = to_ean13(m.group(1))
        if e:
            return e
    # 3) URL/문자열 속 숫자열 중 유효한 GTIN 찾기(긴 것 우선)
    for run in sorted(re.findall(r"\d{8,14}", t), key=len, reverse=True):
        e = to_ean13(run)
        if e:
            return e
    return None


def _decode(img):
    res = []
    try:
        res = zxingcpp.read_barcodes(img, try_rotate=True, try_downscale=True)
    except TypeError:
        try:
            res = zxingcpp.read_barcodes(img)
        except Exception:
            res = []
    except Exception:
        res = []
    out = []
    for r in res:
        bc = extract_barcode(getattr(r, "text", "") or "")
        if bc and bc not in out:
            out.append(bc)          # 상품 바코드만(QR/URL 걸러짐)
    return out


def read_barcodes(img: Image.Image):
    """사진에서 상품 바코드 리스트(EAN-13 정규화, 중복제거)."""
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
