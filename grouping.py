# -*- coding: utf-8 -*-
"""촬영 순서 규칙(1앞면·2바코드·3~날짜)에 맞춘 제품 그룹핑.
- 앞면(front): 다음에 오는 바코드 제품에 붙임 (forward)
- 바코드(barcode): 새 제품 시작 (anchor)
- 날짜/기타(date/other): 직전 바코드 제품에 붙임 (backward)
이러면 연속 촬영해도 제품이 안 섞임. 순서는 EXIF 촬영시각(없으면 업로드 순서).
각 photo 는 p['ptype'] in {'front','barcode','date','other'} 를 가져야 함.
"""

def group_photos(photos):
    if not photos:
        return []
    have_time = sum(1 for p in photos if p.get("taken_at"))
    if have_time >= max(2, int(len(photos) * 0.6)):
        ordered = sorted(photos, key=lambda p: (p.get("taken_at") is None,
                                                p.get("taken_at") or 0, p["index"]))
    else:
        ordered = sorted(photos, key=lambda p: p["index"])

    groups, pending, cur = [], [], None
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
        elif t == "front":
            pending.append(p)          # 다음 바코드까지 대기
        else:                          # date / other
            if cur is not None:
                cur["photos"].append(p)
            else:
                pending.append(p)      # 바코드 전에 나온 날짜 → 다음 제품에 합류
    if pending:                        # 바코드 못 읽은 잔여(앞면/날짜만)
        groups.append({"photos": pending, "bc": None})
    return [g["photos"] for g in groups]
