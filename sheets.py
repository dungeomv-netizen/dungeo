# -*- coding: utf-8 -*-
"""구글시트 읽기/쓰기.
- 읽기: 인증 없이 gviz CSV (미리보기 모드) / 라이브면 gspread
- 쓰기: gspread + 서비스계정 (라이브 모드에서만)
- edate(제조일+N개월) 수식 감지·재작성
- 임박 알림 계산
"""
import csv, io, re, datetime, functools
import requests
import config

_A1 = None
def _a1(row, col):
    global _A1
    if _A1 is None:
        import gspread.utils as gu
        _A1 = gu.rowcol_to_a1
    return _A1(row, col)


def norm_barcode(s):
    s = (s or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except Exception:
            pass
    m = re.match(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", s)
    if m:
        try:
            return datetime.date(int(m[1]), int(m[2]), int(m[3]))
        except Exception:
            return None
    return None


class Sheet:
    def __init__(self):
        self.tabs = []            # 매장(탭) 이름 목록
        self._rows = {}           # tab -> list[list[str]]  (row0=헤더)
        self._store_index = {}    # tab -> {barcode: {store,row,name,category}}
        self._bc_stores = {}      # barcode -> [tab,...]  (어느 매장들에 있나)
        self._gc = None

    # ---------- 읽기 ----------
    def _gviz(self, tab):
        url = f"https://docs.google.com/spreadsheets/d/{config.SHEET_ID}/gviz/tq"
        r = requests.get(url, params={"tqx": "out:csv", "sheet": tab}, timeout=90)
        r.raise_for_status()
        r.encoding = "utf-8"
        return list(csv.reader(io.StringIO(r.text)))

    def _discover_tabs(self):
        """공유링크만으로 탭 이름 목록 확보."""
        meta = f"https://docs.google.com/spreadsheets/d/{config.SHEET_ID}/gviz/tq?tqx=out:json"
        try:
            txt = requests.get(meta, timeout=30).text
        except Exception:
            txt = ""
        # 기본값(설계 확정 탭)
        return ["우성 그린타운 신중동점", "진달래마을 상동대림점"]

    def load(self):
        self.tabs = self._discover_tabs()
        self._rows, self._store_index, self._bc_stores = {}, {}, {}
        if config.live_mode():
            self._load_live()
        else:
            self._load_gviz()
        return self

    def _load_gviz(self):
        for tab in self.tabs:
            rows = [r[:12] for r in self._gviz(tab)]   # A~L만 보관(메모리 절약)
            self._rows[tab] = rows
            self._build_index(tab, rows)

    def _load_live(self):
        for tab in self.tabs:
            ws = self._ws(tab)
            rows = [r[:12] for r in ws.get_all_values()]   # A~L만 보관
            self._rows[tab] = rows
            self._build_index(tab, rows)

    def _build_index(self, tab, rows):
        bi = config.COL_BARCODE - 1
        ni = config.COL_NAME - 1
        ci = config.COL_CATEGORY - 1
        self._store_index.setdefault(tab, {})
        for idx, row in enumerate(rows):
            if idx == 0 or len(row) <= bi:
                continue
            bc = norm_barcode(row[bi])
            if not bc:
                continue
            self._store_index[tab][bc] = {
                "store": tab,
                "row": idx + 1,   # 시트 실제 행번호
                "name": (row[ni].strip() if len(row) > ni else ""),
                "category": (row[ci].strip() if len(row) > ci else ""),
            }
            self._bc_stores.setdefault(bc, [])
            if tab not in self._bc_stores[bc]:
                self._bc_stores[bc].append(tab)

    @property
    def product_count(self):
        return len(self._bc_stores)

    def lookup(self, barcode, store=None):
        """store 지정 시 그 매장에서만 검색.
        미지정 시: 한 매장에만 있으면 그 매장으로 추론, 여러 매장이면 ambiguous."""
        bc = norm_barcode(barcode)
        if store:
            return self._store_index.get(store, {}).get(bc)
        tabs = self._bc_stores.get(bc, [])
        if len(tabs) == 1:
            return self._store_index[tabs[0]][bc]
        if len(tabs) > 1:
            return {"ambiguous": True, "stores": list(tabs)}
        return None

    def current_value(self, tab, row, col):
        """캐시된 현재 셀 값(문자열). 메모칸 덮어쓰기 방지용."""
        rows = self._rows.get(tab)
        if not rows or row - 1 >= len(rows):
            return ""
        r = rows[row - 1]
        return r[col - 1].strip() if len(r) >= col else ""

    # ---------- gspread(라이브) ----------
    def _client(self):
        if self._gc is None:
            import gspread
            creds = config.get_credentials(["https://www.googleapis.com/auth/spreadsheets"])
            self._gc = gspread.authorize(creds).open_by_key(config.SHEET_ID)
        return self._gc

    @functools.lru_cache(maxsize=8)
    def _ws(self, tab):
        return self._client().worksheet(tab)

    def cell_formula(self, tab, row, col):
        """대상 칸의 현재 수식/값 (edate 감지용). 라이브에서만."""
        try:
            v = self._ws(tab).get(_a1(row, col), value_render_option="FORMULA")
            return v[0][0] if v and v[0] else ""
        except Exception:
            return ""

    def write_cell(self, tab, row, col, value):
        self._ws(tab).update(range_name=_a1(row, col), values=[[value]],
                             value_input_option="USER_ENTERED")

    def sort_by_expiry(self, tab):
        """해당 매장 시트를 유통기한(G) 빠른 날짜순 정렬(헤더 제외, 빈 날짜는 맨 아래)."""
        import gspread.utils as gu
        ws = self._ws(tab)
        end = gu.rowcol_to_a1(ws.row_count, ws.col_count)   # 예: AA9030
        ws.sort((config.COL_EXP1, "asc"), range=f"A2:{end}")

    def append_product(self, tab, category, barcode, name, dates):
        """미등록 신규 등록: 한 줄 추가. dates=[문자열...] 최대3개."""
        ws = self._ws(tab)
        rowvals = [""] * config.COL_EXP3
        rowvals[config.COL_CATEGORY - 1] = category or ""
        rowvals[config.COL_BARCODE - 1]  = str(barcode)
        rowvals[config.COL_BARCODE2 - 1] = str(barcode)
        rowvals[config.COL_NAME - 1]     = name or ""
        for i, d in enumerate(dates[:3]):
            rowvals[config.COL_EXP1 - 1 + i] = d
        ws.append_row(rowvals, value_input_option="USER_ENTERED")
        return ws.row_count

    # ---------- 임박 알림 ----------
    def alerts(self, today=None):
        today = today or datetime.date.today()
        tiers = sorted(config.ALERT_DAYS)          # [4,7,14]
        win = config.EXPIRED_WINDOW_DAYS
        out = {}
        bi, ni = config.COL_BARCODE - 1, config.COL_NAME - 1
        exp_idx = [c - 1 for c in config.EXP_COLS]
        for tab, rows in self._rows.items():
            buckets = {str(t): [] for t in tiers}
            expired = []
            for idx, row in enumerate(rows):
                if idx == 0 or len(row) <= bi or not norm_barcode(row[bi]):
                    continue
                dd = []   # [(days, date)]
                for c in exp_idx:
                    if len(row) <= c:
                        continue
                    dt = _parse_date(row[c])
                    if dt:
                        dd.append(((dt - today).days, dt))
                if not dd:
                    continue
                base = {"barcode": norm_barcode(row[bi]),
                        "name": row[ni].strip() if len(row) > ni else ""}
                upcoming = [x for x in dd if x[0] >= 0]
                if upcoming:                          # 가장 임박한 다가오는 날짜
                    days, dt = min(upcoming, key=lambda x: x[0])
                    if days <= tiers[-1]:
                        tier = next(t for t in tiers if days <= t)
                        buckets[str(tier)].append({**base, "exp": dt.isoformat(), "days": days})
                else:                                 # 전부 과거 → 최근 지난 것만
                    days, dt = max(dd, key=lambda x: x[0])
                    if days >= -win:
                        expired.append({**base, "exp": dt.isoformat(), "days": days})
            for t in buckets:
                buckets[t].sort(key=lambda x: x["days"])
            expired.sort(key=lambda x: x["days"], reverse=True)
            buckets["expired"] = expired
            out[tab] = buckets
        return out
