# -*- coding: utf-8 -*-
"""구글시트 '_앱설정' 탭을 이용한 영구 저장(key/value JSON).
클라우드는 파일이 재시작 때 날아가므로, 매장위치·형식기억을 시트에 보관.
메모리 캐시로 반복 읽기 최소화."""
import json
import gspread
import config

TAB = "_앱설정"
_client = None
_cache = {}


def _sh():
    global _client
    if _client is None:
        creds = config.get_credentials(["https://www.googleapis.com/auth/spreadsheets"])
        _client = gspread.authorize(creds).open_by_key(config.SHEET_ID)
    return _client


def _ws():
    sh = _sh()
    try:
        return sh.worksheet(TAB)
    except Exception:
        ws = sh.add_worksheet(title=TAB, rows=50, cols=2)
        ws.update(range_name="A1", values=[["key", "value"]])
        return ws


def get(key, default=None):
    if key in _cache:
        return _cache[key]
    try:
        ws = _ws()
        cell = ws.find(key, in_column=1)
        raw = ws.cell(cell.row, 2).value if cell else None
        val = json.loads(raw) if raw else default
    except Exception:
        val = default
    _cache[key] = val
    return val


def set(key, value):
    ws = _ws()
    raw = json.dumps(value, ensure_ascii=False)
    try:
        cell = ws.find(key, in_column=1)
    except Exception:
        cell = None
    if cell:
        ws.update_cell(cell.row, 2, raw)
    else:
        ws.append_row([key, raw], value_input_option="RAW")
    _cache[key] = value
