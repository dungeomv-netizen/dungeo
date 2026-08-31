# -*- coding: utf-8 -*-
"""제품(바코드)별 날짜 표기 순서 기억.
확인필요에서 사용자가 한 번 고르면 그 바코드의 순서(예: DMY)를 저장 →
다음부터 같은 제품은 애매해도 자동 해석(확인필요 안 뜸)."""
import json, re, datetime
from itertools import permutations
import config

PATH = config.DATA_DIR / "date_formats.json"


def _load():
    if config.live_mode():
        try:
            import kvstore
            return kvstore.get("date_formats", {}) or {}
        except Exception:
            pass
    if PATH.exists():
        try:
            return json.loads(PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(d):
    if config.live_mode():
        try:
            import kvstore
            kvstore.set("date_formats", d)
            return
        except Exception:
            pass
    PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _norm(bc):
    bc = (bc or "").strip()
    return bc[:-2] if bc.endswith(".0") else bc


def _tokens(raw):
    nums = re.findall(r"\d+", raw or "")
    if len(nums) == 1 and len(nums[0]) == 8:
        s = nums[0]; return [int(s[:4]), int(s[4:6]), int(s[6:8])]
    if len(nums) == 1 and len(nums[0]) == 6:
        s = nums[0]; return [int(s[:2]), int(s[2:4]), int(s[4:6])]
    if len(nums) >= 3:
        return [int(x) for x in nums[:3]]
    return None


def infer_order(raw, chosen_iso):
    """raw 토큰과 선택된 날짜로 순서(예:'DMY') 추론."""
    toks = _tokens(raw)
    if not toks or not chosen_iso:
        return None
    try:
        y, m, d = [int(x) for x in chosen_iso.split("-")]
    except Exception:
        return None
    for perm in permutations("YMD"):
        role = {perm[i]: toks[i] for i in range(3)}
        yy = role["Y"]; yy = 2000 + yy if yy < 100 else yy
        try:
            dt = datetime.date(yy, role["M"], role["D"])
        except Exception:
            continue
        if (dt.year, dt.month, dt.day) == (y, m, d):
            return "".join(perm)
    return None


def apply_order(raw, order):
    toks = _tokens(raw)
    if not toks or not order or len(order) != 3:
        return None
    role = {order[i]: toks[i] for i in range(3)}
    if "Y" not in role:
        return None
    yy = role["Y"]; yy = 2000 + yy if yy < 100 else yy
    try:
        return datetime.date(yy, role["M"], role["D"]).isoformat()
    except Exception:
        return None


def get_order(barcode):
    return _load().get(_norm(barcode))


def learn_from_choice(barcode, raw, chosen_iso):
    o = infer_order(raw, chosen_iso)
    if o:
        d = _load(); d[_norm(barcode)] = o; _save(d)
    return o


def resolve(barcode, raw):
    """저장된 순서로 애매한 날짜 자동해석. 없으면 None."""
    o = get_order(barcode)
    return apply_order(raw, o) if o else None
