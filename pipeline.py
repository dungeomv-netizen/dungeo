# -*- coding: utf-8 -*-
"""업로드 배치 처리: 사진 → 그룹핑 → 바코드/날짜 인식 → 매장판별 → 시트 기입 결정."""
import re, uuid, datetime
import config, vision, store_geo, date_prefs
from imaging import load_image, read_exif, make_thumb
from barcode_read import read_barcodes
from grouping import group_photos

COL_LETTER = "ABCDEFGHI"
COL_NAME_KO = {config.COL_EXP1: "유통기한", config.COL_EXP2: "유통기한2", config.COL_EXP3: "유통기한3"}


def _looks_like_date(s):
    s = (s or "").strip()
    return bool(re.match(r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}", s))


def _edate_months(formula):
    m = re.search(r"edate\s*\([^,]*,\s*(\d+)\s*\)", formula or "", re.I)
    return int(m.group(1)) if m else None


def date_candidates(raw, today=None):
    """애매한 날짜문자열에서 '말이 되는' 후보 날짜들을 뽑음(월/일 순서 등).
    예: '06-08-27' -> ['2027-06-08','2027-08-06'] (탭으로 고르게)"""
    today = today or datetime.date.today()
    nums = re.findall(r"\d+", raw or "")
    toks = None
    if len(nums) == 1 and len(nums[0]) == 8:          # YYYYMMDD
        s = nums[0]; toks = [int(s[:4]), int(s[4:6]), int(s[6:8])]
    elif len(nums) == 1 and len(nums[0]) == 6:        # YYMMDD/여러순서
        s = nums[0]; toks = [int(s[:2]), int(s[2:4]), int(s[4:6])]
    elif len(nums) >= 3:
        toks = [int(x) for x in nums[:3]]
    if not toks:
        return []
    lo = today - datetime.timedelta(days=60)
    hi = today + datetime.timedelta(days=365 * 3)
    cands = set()
    for yi in range(3):
        yr = toks[yi]
        year = (2000 + yr) if yr < 100 else yr
        rest = [toks[j] for j in range(3) if j != yi]
        for m, d in ((rest[0], rest[1]), (rest[1], rest[0])):
            try:
                dt = datetime.date(year, m, d)
            except Exception:
                continue
            if lo <= dt <= hi:
                cands.add(dt)
    return [d.isoformat() for d in sorted(cands)][:4]


def prep_photos(file_storages):
    """업로드 파일을 한 장씩 읽어 처리 후 원본을 즉시 버림(메모리 절약).
    바코드/EXIF는 원본 해상도로, 비전용은 축소본만 보관."""
    photos = []
    for i, fs in enumerate(file_storages):
        try:
            raw = fs.read()
            img = load_image(raw)
        except Exception:
            continue
        try:
            taken_at, gps = read_exif(img)          # 원본에서 촬영시각·GPS
            barcodes = read_barcodes(img)           # 원본 해상도로 바코드
            thumb = make_thumb(img)                 # 작은 썸네일(data URI)
            vimg = img.convert("RGB")
            vimg.thumbnail((1280, 1280))            # 비전용 축소본만 보관
        finally:
            img.close()
            del img, raw                            # 원본 즉시 해제
        photos.append({
            "index": i, "filename": getattr(fs, "filename", str(i)),
            "taken_at": taken_at, "gps": gps,
            "barcodes": barcodes,
            "thumb": thumb,
            "_img": vimg,
        })
    return photos


def _decide_store(group, batch_store):
    for p in group:
        tab, m = store_geo.locate(p.get("gps"))
        if tab:
            return tab, "GPS", (round(m) if m is not None else None)
    if batch_store:
        return batch_store, "수동배치", None
    return None, None, None


def _pick_dates(dates):
    exp, manu, amb = [], [], []
    for d in dates:
        if d.get("ambiguous") or not d.get("iso"):
            if d.get("raw_text"):
                amb.append(d)
            continue
        if d.get("kind") == "manufacture":
            manu.append(d)
        else:  # expiry / unknown
            exp.append(d)
    return exp, manu, amb


def _decide_writes(exp, manu, sheet, tab, row, live):
    """returns (writes, needs) writes=[{col,col_name,letter,value,display}] needs=[사유]"""
    writes, needs = [], []
    if exp:
        # 찍은 날짜들이 '현재 전체' → 빠른 날짜가 G, 그다음 H·I. 남는 날짜칸은 비움(메모는 보존)
        exp_sorted = sorted(exp, key=lambda d: d["iso"])
        for i, col in enumerate(config.EXP_COLS):
            cur = sheet.current_value(tab, row, col)
            if i < len(exp_sorted):
                if cur and not _looks_like_date(cur):      # 메모칸 보호
                    needs.append(f"{COL_NAME_KO[col]}칸에 메모('{cur[:12]}')가 있어 덮지 않음")
                    continue
                iso = exp_sorted[i]["iso"]
                writes.append({"col": col, "col_name": COL_NAME_KO[col],
                               "letter": COL_LETTER[col-1], "value": iso, "display": iso})
            else:
                # 이번에 안 찍은 날짜칸: 기존 날짜/수식만 비움(메모·빈칸은 그대로)
                if cur and _looks_like_date(cur):
                    writes.append({"col": col, "col_name": COL_NAME_KO[col],
                                   "letter": COL_LETTER[col-1], "value": "", "display": "(비움)"})
        return writes, needs
    if manu:
        d = manu[0]
        y, m, dd = d["iso"].split("-")
        N = None
        if live:
            N = _edate_months(sheet.cell_formula(tab, row, config.COL_EXP1))
        if N is None:
            N = d.get("months_rule")
        if N:
            val = f"=EDATE(DATE({int(y)},{int(m)},{int(dd)}),{N})"
            writes.append({"col": config.COL_EXP1, "col_name": "유통기한",
                           "letter": "G", "value": val,
                           "display": f"제조일 {d['iso']} +{N}개월(수식)"})
            return writes, needs
        needs.append(f"제조일({d['iso']})만 읽힘 — 개월수 규칙을 몰라 유통기한 확인 필요")
        return writes, needs
    return writes, needs


def _classify(p, a):
    if p["barcodes"]:
        return "barcode"
    if p.get("vdates"):
        return "date"
    if a.get("type") == "front" or p.get("vname"):
        return "front"
    return a.get("type", "other")


def process_batch(files, batch_store, sheet):
    today = datetime.date.today().isoformat()
    tdate = datetime.date.fromisoformat(today)
    photos = prep_photos(files)

    # 사진별 비전 분석(한 번에): 종류/날짜/제품명
    analyses = vision.analyze_images([p["_img"] for p in photos], today)
    for p, a in zip(photos, analyses):
        p["vdates"] = a.get("dates", []) or []
        p["vname"] = a.get("product_name")
        p["verr"] = a.get("error")
        p["ptype"] = _classify(p, a)

    groups = group_photos(photos)
    live = config.live_mode()
    results = []

    for g in groups:
        gid = uuid.uuid4().hex[:8]
        thumbs = [p["thumb"] for p in g]
        barcode = next((p["barcodes"][0] for p in g if p["barcodes"]), None)

        # 그룹 내 모든 사진의 날짜/제품명 집계(중복 제거)
        dates, seen = [], set()
        for p in g:
            for d in p.get("vdates", []):
                key = d.get("iso") or d.get("raw_text")
                if key and key not in seen:
                    seen.add(key); dates.append(d)
        read_name = next((p["vname"] for p in g if p.get("vname")), None)
        verr = next((p["verr"] for p in g if p.get("verr")), None)

        tab, src, meters = _decide_store(g, batch_store)
        exp, manu, amb = _pick_dates(dates)

        # 저장된 제품별 날짜형식으로 애매한 것 자동해결(확인필요 안 뜨게)
        if barcode and amb:
            still = []
            for d in amb:
                iso = date_prefs.resolve(barcode, d.get("raw_text"))
                if iso:
                    rd = {"kind": "expiry", "iso": iso, "raw_text": d.get("raw_text"),
                          "ambiguous": False, "reason": "저장된 날짜형식 자동적용"}
                    exp.append(rd); dates.append(rd)
                else:
                    still.append(d)
            amb = still

        cand = []
        for d in amb:
            cand += date_candidates(d.get("raw_text"), today=tdate)
        candidates = list(dict.fromkeys(cand))

        item = {
            "id": gid, "thumbs": thumbs, "barcode": barcode,
            "store": tab, "store_source": src, "store_meters": meters,
            "read_name": read_name,
            "dates_read": [{"kind": d.get("kind"), "iso": d.get("iso"),
                            "raw": d.get("raw_text"), "ambiguous": d.get("ambiguous"),
                            "reason": d.get("reason")} for d in dates],
            "writes": [], "reason": "", "name": read_name, "row": None,
            "candidates": candidates,
            "amb_raw": (amb[0].get("raw_text") if amb else ""),
        }
        if verr:
            item["reason"] = f"날짜 인식 오류: {verr}"

        # 1) 바코드 못 읽음
        if not barcode:
            item["status"] = "확인필요"
            item["reason"] = (item["reason"] + " / " if item["reason"] else "") + "바코드를 못 읽었어요"
            results.append(item); continue

        found = sheet.lookup(barcode, tab)
        sug = [d["iso"] for d in exp if d.get("iso")] or [d["iso"] for d in manu if d.get("iso")]

        # 2) 여러 매장에 있는 바코드인데 매장 미확정
        if isinstance(found, dict) and found.get("ambiguous"):
            item["status"] = "확인필요"
            item["reason"] = "여러 매장에 있는 바코드예요 — 매장을 선택해 저장하세요"
            item["ambiguous_stores"] = found["stores"]
            item["suggest_dates"] = sug
            results.append(item); continue

        # 3) 미등록
        if not found:
            item["status"] = "미등록"
            item["reason"] = "시트에 없는 바코드"
            item["suggest_dates"] = sug
            results.append(item); continue

        # 4) 등록 상품
        tab = found["store"]
        item["row"] = found["row"]; item["name"] = found["name"]; item["store"] = tab
        writes, needs = _decide_writes(exp, manu, sheet, tab, found["row"], live)
        item["writes"] = writes

        problems = list(needs)
        if amb and not writes:
            problems.append("날짜 표기가 애매해요: " + ", ".join(d.get("raw_text","") for d in amb))
        if not writes and not exp and not manu and not amb:
            problems.append("날짜를 못 읽었어요")

        if not writes or problems:
            item["status"] = "확인필요"
            item["reason"] = " / ".join(problems) if problems else "확인 필요"
            results.append(item); continue

        # 실제 기입 (라이브) / 미리보기
        if live:
            try:
                for w in writes:
                    sheet.write_cell(tab, found["row"], w["col"], w["value"])
                item["status"] = "기입완료"
            except Exception as e:
                item["status"] = "확인필요"
                item["reason"] = f"시트 쓰기 실패: {e}"
        else:
            item["status"] = "미리보기"
            item["reason"] = "미리보기 모드(구글 연결 전) — 실제 기입은 안 됨"
        results.append(item)

    # PIL 이미지 참조 정리
    for p in photos:
        p.pop("_img", None)
    return results
