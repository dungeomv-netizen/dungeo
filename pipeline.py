# -*- coding: utf-8 -*-
"""업로드 배치 처리: 사진 → 그룹핑 → 바코드/날짜 인식 → 매장판별 → 시트 기입 결정."""
import re, uuid, datetime
import config, vision, store_geo, date_prefs
from imaging import load_image, read_exif, make_thumb
from barcode_read import read_barcodes, to_ean13
from grouping import group_photos

COL_LETTER = "ABCDEFGHI"
COL_NAME_KO = {config.COL_EXP1: "유통기한", config.COL_EXP2: "유통기한2", config.COL_EXP3: "유통기한3"}


def _looks_like_date(s):
    s = str(s or "").strip()
    return bool(re.match(r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}", s))


def _edate_months(formula):
    m = re.search(r"edate\s*\(.*,\s*(\d+)\s*\)", str(formula or ""), re.I)
    return int(m.group(1)) if m else None


def date_candidates(raw, today=None):
    """애매한 날짜문자열에서 '말이 되는' 후보 날짜들을 뽑음(월/일 순서 등).
    예: '06-08-27' -> ['2027-06-08','2027-08-06'] (탭으로 고르게)"""
    today = today or datetime.date.today()
    nums = re.findall(r"\d+", str(raw or ""))
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


def prep_photos(file_storages, client_gps=None, client_barcodes=None, client_ts=None):
    """업로드 파일을 한 장씩 읽어 처리 후 원본을 즉시 버림(메모리 절약).
    바코드/EXIF는 원본 해상도로, 비전용은 축소본만 보관.
    client_gps/client_barcodes/client_ts: 폰·구글포토에서 미리 읽어 보낸 사진별 값(없으면 None)."""
    client_gps = client_gps or []
    client_barcodes = client_barcodes or []
    client_ts = client_ts or []
    photos = []
    for i, fs in enumerate(file_storages):
        try:
            raw = fs.read()
            img = load_image(raw)
        except Exception:
            continue
        try:
            taken_at, gps = read_exif(img)          # 원본에서 촬영시각·GPS
            if taken_at is None and i < len(client_ts) and client_ts[i] is not None:
                taken_at = client_ts[i]             # 구글포토 createTime 등(축소본엔 EXIF 없음)
            if not gps and i < len(client_gps) and client_gps[i]:
                try:
                    cg = client_gps[i]
                    gps = (float(cg[0]), float(cg[1]))   # 폰이 보낸 GPS 사용(축소본엔 EXIF 없음)
                except Exception:
                    pass
            # 바코드: 폰이 원본에서 읽은 값 우선, 없으면 서버가 이미지에서 시도
            cb = client_barcodes[i] if i < len(client_barcodes) else None
            barcodes = [str(cb).strip()] if cb else read_barcodes(img)
            thumb = make_thumb(img)                 # 작은 썸네일(data URI)
            vimg = img.convert("RGB")
            vimg.thumbnail((900, 900))              # 비전용 축소본만 보관(메모리 절약)
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
    # 사용자가 고른 매장을 무조건 우선(선택). GPS 자동판별은 폐기(신뢰불가).
    if batch_store:
        return batch_store, "선택", None
    for p in group:                       # 매장 미선택시에만(구버전 호환) GPS 폴백
        tab, m = store_geo.locate(p.get("gps"))
        if tab:
            return tab, "GPS", (round(m) if m is not None else None)
    return None, None, None


def _pick_dates(dates):
    exp, manu, amb, uncertain = [], [], [], []
    for d in dates:
        if d.get("ambiguous") or not d.get("iso"):
            if d.get("raw_text"):
                amb.append(d)
            continue
        if d.get("uncertain"):            # 읽긴 했으나 확신 낮음(점자/흐림) → 확인받기(자동기입X)
            uncertain.append(d)
            continue
        if d.get("kind") == "manufacture":
            manu.append(d)
        else:  # expiry / unknown
            exp.append(d)
    return exp, manu, amb, uncertain


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
    t = a.get("type")
    if p["barcodes"]:
        return "barcode"                      # 실제 디코딩된 바코드 → 무조건 바코드(경계 앵커)
    if t == "barcode":
        return "barcode"                      # 비전이 '바코드 사진'으로 봄(숫자는 못 읽음) → 제품 경계로
    if t == "front":
        return "front"                        # ★비전이 '앞면'이라 하면 앞면 — 포장에 날짜가 보여도 무시
    if t == "date" or p.get("vdates"):
        return "date"
    if p.get("vname"):
        return "front"                        # 이름만 준 경우도 앞면(상표) 취급
    return t or "other"


def process_batch(files, batch_store, sheet, client_gps=None, client_barcodes=None,
                  client_groups=None, client_ts=None):
    today = datetime.date.today().isoformat()
    tdate = datetime.date.fromisoformat(today)
    photos = prep_photos(files, client_gps=client_gps, client_barcodes=client_barcodes, client_ts=client_ts)

    # 사진별 비전 분석(한 번에): 종류/날짜/제품명
    analyses = vision.analyze_images([p["_img"] for p in photos], today)
    for p, a in zip(photos, analyses):
        vds = a.get("dates", []) or []
        for _d in vds:                     # 비전이 숫자로 준 값 방지 → 문자열로 통일
            if isinstance(_d, dict):
                for _k in ("raw_text", "iso", "kind", "reason"):
                    if _d.get(_k) is not None:
                        _d[_k] = str(_d[_k])
        p["vdates"] = [d for d in vds if isinstance(d, dict)]
        p["vname"] = a.get("product_name")
        p["verr"] = a.get("error")
        # ★이중검증: 막대 해독값(zxing/폰) vs 밑에 인쇄된 숫자(비전 OCR)를 대조.
        #  - 둘 다 있고 같음 → 확신(그대로 사용, 자동기입 OK)
        #  - 둘 다 있는데 다름 → 막대 왜곡 오독 의심 → 인쇄숫자 우선 + 자동기입 막고 '확인필요'
        #  - 막대만 → 그대로 / 숫자만(체크섬통과) → 숫자 사용 / 숫자만(체크섬실패) → 제안
        # ★앞면(상표) 사진 판별: 비전이 front 라 했거나 제품명을 준 사진.
        #  앞면은 '이름 채우기'용일 뿐 — 포장의 작은 바코드/날짜로 그룹·기입을 망치지 않게 격리.
        is_front = (a.get("type") == "front") or bool(a.get("product_name"))
        bar = p["barcodes"][0] if p.get("barcodes") else None
        vb = "".join(ch for ch in str(a.get("barcode_number") or "") if ch.isdigit())
        ocr = to_ean13(vb) if vb else None
        if bar and ocr and bar != ocr:
            p["barcodes"] = [ocr]; p["_conflict"] = (bar, ocr)   # 인쇄숫자 우선, 확인받기
        elif not bar and ocr and not is_front:
            p["barcodes"] = [ocr]; p["_ocr_bc"] = True           # 막대 안읽힘 → 인쇄숫자 사용(앞면 제외)
        elif not bar and vb and 8 <= len(vb) <= 14 and not is_front:
            p["_vbarcode"] = vb                                   # 체크섬 실패 → 제안만(앞면 제외)
        p["ptype"] = _classify(p, a)

    # ★그룹핑: 클라이언트가 촬영시각 클러스터로 정한 그룹번호가 있으면 그대로 묶음(순서 안 타서 제일 튼튼).
    #  없으면(구버전 호환) 서버가 바코드-앵커 방식으로 묶음.
    if client_groups and len(client_groups) >= len(photos):
        from collections import OrderedDict
        gmap = OrderedDict()
        for p in photos:
            gi = client_groups[p["index"]] if p["index"] < len(client_groups) else f"_{p['index']}"
            gmap.setdefault(gi, []).append(p)
        groups = list(gmap.values())
    else:
        groups = group_photos(photos)
    live = config.live_mode()
    results = []

    for g in groups:
        gid = uuid.uuid4().hex[:8]
        thumbs = [p["thumb"] for p in g]
        # 그룹 안의 '실제 디코딩된' 바코드들(체크섬 통과 → 신뢰). OCR 숫자는 여기 없음.
        decoded = list(dict.fromkeys(p["barcodes"][0] for p in g if p.get("barcodes")))
        barcode = decoded[0] if decoded else None
        mixed_barcodes = len(decoded) >= 2   # 한 묶음에 다른 상품이 섞임 → 자동기입 금지
        ocr_bc = any(p.get("_ocr_bc") for p in g if p.get("barcodes"))  # 숫자인식으로 얻은 바코드
        conflict = next((p["_conflict"] for p in g if p.get("_conflict")), None)  # 막대≠인쇄숫자
        suggest_barcode = next((p["_vbarcode"] for p in g if p.get("_vbarcode")), None)

        # 그룹 내 모든 사진의 날짜/제품명 집계(중복 제거)
        dates, seen = [], set()
        for p in g:
            if p.get("ptype") == "front":
                continue                     # ★앞면(상표) 포장에 찍힌 날짜는 무시 — 날짜는 전용 날짜사진에서만
            for d in p.get("vdates", []):
                key = d.get("iso") or d.get("raw_text")
                if key and key not in seen:
                    seen.add(key); dates.append(d)
        read_name = next((p["vname"] for p in g if p.get("vname")), None)
        verr = next((p["verr"] for p in g if p.get("verr")), None)

        tab, src, meters = _decide_store(g, batch_store)
        exp, manu, amb, uncertain = _pick_dates(dates)

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
            "suggest_barcode": suggest_barcode,
            "barcode_from_ocr": ocr_bc,
        }
        if verr:
            item["reason"] = f"날짜 인식 오류: {verr}"
        if conflict:
            item["reason"] = (item["reason"] + " / " if item["reason"] else "") + \
                f"막대바코드({conflict[0]})와 인쇄숫자({conflict[1]})가 달라요 — 인쇄숫자 기준, 상품 숫자 확인"

        # 1) 바코드 못 읽음
        if not barcode:
            item["status"] = "확인필요"
            item["reason"] = (item["reason"] + " / " if item["reason"] else "") + "바코드를 못 읽었어요"
            results.append(item); continue

        # 1.5) 한 묶음에 서로 다른 실제 바코드가 섞임 → 자동기입 금지(확인필요)
        if mixed_barcodes:
            item["status"] = "확인필요"
            item["reason"] = "한 묶음에 여러 상품 바코드가 섞였어요 — 상품별로 다시 확인하세요"
            item["mixed"] = list(decoded)
            results.append(item); continue

        found = sheet.lookup(barcode, tab)
        # 확인필요 카드 미리채움: 유통기한(exp) 우선 → 흐린날짜 → (마지막)제조일
        sug = ([d["iso"] for d in exp if d.get("iso")]
               or [d["iso"] for d in uncertain if d.get("iso")]   # 확신낮은 날짜도 후보로(확인칸 미리채움)
               or [d["iso"] for d in manu if d.get("iso")])
        item["suggest_dates"] = sug        # 모든 확인필요 카드가 유통기한 우선으로 미리채워지게

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
        if conflict:      # 막대≠인쇄숫자 → 자동기입 금지, 사람이 확인
            problems.append("막대 바코드와 인쇄된 숫자가 달라 확인 필요")
        if uncertain:     # 점자/흐린 날짜 → 자동기입 금지, 읽은 값 확인받기
            problems.append("날짜가 흐릿해요(캔·병 점자날짜 등) — 읽은 값 맞는지 확인 후 저장: "
                            + ", ".join(d.get("iso", "") for d in uncertain))
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

    # PIL 이미지 참조 정리 + 메모리 즉시 회수(묶음 사이 메모리 안정)
    for p in photos:
        img = p.pop("_img", None)
        if img is not None:
            try:
                img.close()
            except Exception:
                pass
    import gc
    gc.collect()
    return results
