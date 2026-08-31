# -*- coding: utf-8 -*-
"""이미지 로드(HEIC 포함), EXIF에서 촬영시각·GPS 추출, 썸네일 생성."""
import io, math
from datetime import datetime
from PIL import Image, ExifTags

# 아이폰 HEIC 지원 등록
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

_TAGS = {v: k for k, v in ExifTags.TAGS.items()}
_GPS  = {v: k for k, v in ExifTags.GPSTAGS.items()}


def load_image(raw: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(raw))
    # JPEG는 디코더가 축소 로드(draft) → 큰 폰사진도 메모리 대폭 절약(바코드는 충분히 선명)
    try:
        img.draft("RGB", (1600, 1600))
    except Exception:
        pass
    img.load()
    return img


def _to_deg(val, ref):
    if not val:
        return None
    try:
        d, m, s = [float(x[0]) / float(x[1]) if isinstance(x, tuple) else float(x) for x in val]
    except Exception:
        try:
            d, m, s = [float(x) for x in val]
        except Exception:
            return None
    deg = d + m / 60.0 + s / 3600.0
    if ref in ("S", "W"):
        deg = -deg
    return deg


def read_exif(img: Image.Image):
    """returns (taken_at: datetime|None, gps: (lat,lng)|None)"""
    taken_at, gps = None, None
    try:
        exif = img._getexif() or {}
    except Exception:
        exif = {}
    if not exif:
        return taken_at, gps

    for key in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
        tag = _TAGS.get(key)
        if tag and tag in exif:
            try:
                taken_at = datetime.strptime(str(exif[tag]), "%Y:%m:%d %H:%M:%S")
                break
            except Exception:
                pass

    gtag = _TAGS.get("GPSInfo")
    if gtag and gtag in exif:
        g = exif[gtag]
        try:
            lat = _to_deg(g.get(_GPS["GPSLatitude"]), g.get(_GPS["GPSLatitudeRef"]))
            lng = _to_deg(g.get(_GPS["GPSLongitude"]), g.get(_GPS["GPSLongitudeRef"]))
            if lat is not None and lng is not None:
                gps = (lat, lng)
        except Exception:
            pass
    return taken_at, gps


def make_thumb(img: Image.Image, max_px=420) -> str:
    """썸네일을 data URI 문자열로 반환(파일 저장 안 함 → 클라우드 호환)."""
    import base64
    im = img.convert("RGB")
    im.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=78)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def haversine_m(a, b):
    """두 (lat,lng) 사이 거리(m)"""
    R = 6371000.0
    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))
