# -*- coding: utf-8 -*-
"""매장 GPS 기준좌표 관리 + 사진 GPS로 매장 판별.
저장: 라이브(구글연결)면 시트 _앱설정 탭에, 아니면 로컬 파일."""
import json
import config
from imaging import haversine_m


def _use_kv():
    return config.live_mode()


def load_stores():
    if _use_kv():
        try:
            import kvstore
            return kvstore.get("stores", []) or []
        except Exception:
            pass
    if config.STORES_JSON.exists():
        try:
            return json.loads(config.STORES_JSON.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _write(stores):
    if _use_kv():
        import kvstore
        kvstore.set("stores", stores)
    else:
        config.STORES_JSON.write_text(json.dumps(stores, ensure_ascii=False, indent=2),
                                      encoding="utf-8")


def save_store(tab, lat, lng, label=""):
    stores = [s for s in load_stores() if s["tab"] != tab]
    stores.append({"tab": tab, "lat": lat, "lng": lng, "label": label or tab})
    _write(stores)
    return stores


def locate(gps):
    """gps=(lat,lng) -> (tab, meters) 또는 (None, None)"""
    if not gps:
        return None, None
    best_tab, best_m = None, None
    for s in load_stores():
        m = haversine_m(gps, (s["lat"], s["lng"]))
        if best_m is None or m < best_m:
            best_tab, best_m = s["tab"], m
    if best_tab is not None and best_m is not None and best_m <= config.STORE_MATCH_METERS:
        return best_tab, best_m
    return None, best_m
