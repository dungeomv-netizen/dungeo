# -*- coding: utf-8 -*-
"""전역 설정. .env 를 읽어 상수로 노출."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

# --- 비밀/외부 ---
GEMINI_API_KEY      = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL        = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
GEMINI_FALLBACK     = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3-flash-preview").strip()
SHEET_ID            = os.getenv("SHEET_ID", "").strip()
PORT                = int(os.getenv("PORT", "8800"))

# 로그인/보안 (클라우드 공개용)
APP_PASSWORD        = os.getenv("APP_PASSWORD", "").strip()      # 비어있으면 로컬=로그인 없음
SECRET_KEY          = os.getenv("SECRET_KEY", "local-dev-secret")
# 서비스계정 키: 클라우드는 환경변수(JSON 문자열)로, 로컬은 파일로
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()
ALERT_DAYS          = [int(x) for x in os.getenv("ALERT_DAYS", "14,7,4").split(",") if x.strip()]
EXPIRED_WINDOW_DAYS = int(os.getenv("EXPIRED_WINDOW_DAYS", "30"))  # 이미 지난 것: 최근 N일까지만 표시

# --- 경로 ---
DATA_DIR      = BASE / "data"
UPLOAD_DIR    = BASE / "uploads"      # 원본 임시 저장
THUMB_DIR     = BASE / "static" / "thumbs"
STORES_JSON   = DATA_DIR / "stores.json"          # 매장 GPS 기준좌표
CREDS_JSON    = DATA_DIR / "credentials.json"      # 구글 서비스계정(쓰기용). 없으면 미리보기 모드
for d in (DATA_DIR, UPLOAD_DIR, THUMB_DIR):
    d.mkdir(parents=True, exist_ok=True)

# --- 시트 컬럼(1-base 열번호) ---
COL_CATEGORY = 1   # A 분류
COL_MENUCODE = 2   # B 메뉴코드
COL_BARCODE  = 3   # C 바코드번호 (매칭 키)
COL_BARCODE2 = 4   # D =C 수식
COL_NAME     = 5   # E 관리메뉴명
COL_PRICE    = 6   # F 판매가격
COL_EXP1     = 7   # G 유통기한
COL_EXP2     = 8   # H 유통기한2
COL_EXP3     = 9   # I 유통기한3
EXP_COLS     = [COL_EXP1, COL_EXP2, COL_EXP3]

# --- 그룹핑 ---
GROUP_GAP_SEC = 90     # 촬영시각 간격이 이보다 크면 다른 제품으로 분리

# --- 매장 판별 ---
STORE_MATCH_METERS = 400   # 사진 GPS가 매장 기준좌표에서 이 반경 안이면 그 매장

def live_mode() -> bool:
    """서비스계정 자격증명이 있으면 실제 시트 쓰기 가능(라이브), 없으면 미리보기."""
    return bool(GOOGLE_CREDENTIALS_JSON) or CREDS_JSON.exists()


def get_credentials(scopes):
    """환경변수 JSON 우선, 없으면 파일에서 서비스계정 자격증명 로드."""
    from google.oauth2.service_account import Credentials
    if GOOGLE_CREDENTIALS_JSON:
        import json
        info = json.loads(GOOGLE_CREDENTIALS_JSON)
        return Credentials.from_service_account_info(info, scopes=scopes)
    return Credentials.from_service_account_file(str(CREDS_JSON), scopes=scopes)
