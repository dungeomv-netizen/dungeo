# -*- coding: utf-8 -*-
"""촬영 순서 규칙(1앞면·2바코드·3~날짜)에 맞춘 제품 그룹핑.
- 앞면(front): 다음에 오는 바코드 제품에 붙임 (forward)
- 바코드(barcode): 새 제품 시작 (anchor)
- 날짜/기타(date/other): 직전 바코드 제품에 붙임 (backward)
이러면 연속 촬영해도 제품이 안 섞임. 순서는 EXIF 촬영시각(없으면 업로드 순서).
각 photo 는 p['ptype'] in {'front','barcode','date','other'} 를 가져야 함.
★안전장치: 촬영시각이 있으면, 날짜가 직전 바코드와 시간차가 크면(다른 제품) 안 붙이고 따로 뺌.
"""
import config

_GAP = getattr(config, "GROUP_GAP_SEC", 90)


def _gap_ok(p, cur):
    """cur(현재 바코드 제품)의 마지막 사진과 시간차가 _GAP 이내면 True(같은 제품).
    둘 중 하나라도 촬영시각이 없으면 판단불가 → True(기존 동작 유지, 규제 안 함)."""
    ta = p.get("taken_at")
    if ta is None:
        return True
    last = None
    for q in cur["photos"]:
        qt = q.get("taken_at")
        if qt is not None:
            last = qt if last is None else max(last, qt)
    if last is None:
        return True
    try:
        return abs((ta - last).total_seconds()) <= _GAP
    except Exception:
        return True


def group_photos(photos):
    if not photos:
        return []
    have_time = sum(1 for p in photos if p.get("taken_at"))
    if have_time >= max(2, int(len(photos) * 0.6)):
        ordered = sorted(photos, key=lambda p: (p.get("taken_at") is None,
                                                p.get("taken_at") or 0, p["index"]))
    else:
        ordered = sorted(photos, key=lambda p: p["index"])

    groups, pending, cur, orphans = [], [], None, []
    for p in ordered:
        t = p.get("ptype", "other")
        if t == "barcode":
            bc = p["barcodes"][0] if p.get("barcodes") else None
            # 같은 바코드를 연속으로 두 번 찍은 경우 → 같은 제품으로 합침
            if cur is not None and cur.get("bc") and bc and cur["bc"] == bc:
                cur["photos"].append(p)
            else:
                cur = {"photos": pending + [p], "bc": bc}
                pending = []
                groups.append(cur)
        elif t == "date":              # 날짜 찍힌 사진만 직전 바코드 제품에 붙임
            if cur is not None and _gap_ok(p, cur):
                cur["photos"].append(p)   # 시간차 가까울 때만(다른 제품이면 시간차 큼)
            else:
                orphans.append(p)         # 바코드 전이거나 시간차 큼 → 별도(확인필요)
        else:                          # front / other(앞면·포장사진 등) → 다음 바코드로
            # ★앞 제품에 안 섞이게: 다음에 오는 바코드 제품에 붙임
            pending.append(p)
    if pending:                        # 앞면만 있고 바코드 못 읽은 잔여
        groups.append({"photos": pending, "bc": None})
    for o in orphans:                  # 경계 고아: 각각 확인필요로
        groups.append({"photos": [o], "bc": None})
    return [g["photos"] for g in groups]
