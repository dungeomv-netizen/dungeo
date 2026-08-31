# -*- coding: utf-8 -*-
"""제미나이 비전:
- analyze_images(): 사진들을 한 번에 넣어 사진별로 [종류·제품명·날짜] 추출
  종류 = front(앞면) / date(날짜) / other  (바코드는 zxing이 담당)
- 나라별 날짜표기 자동 구별, 애매하면 ambiguous=true
- 503/과부하 재시도 + 폴백 모델
"""
import base64, io, json, time
import requests
import config

_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_PROMPT = """You are given {n} photos of Korean retail products (there may be several different products).
For EACH image, in the SAME order, return one object. Return STRICT JSON only:
{"images":[
  {"type":"front|date|other",
   "product_name":"Korean product name if the image shows the product front, else null",
   "dates":[{"kind":"expiry|manufacture|unknown","raw_text":"chars seen",
             "iso":"YYYY-MM-DD or null","months_rule":null,"ambiguous":true/false,"reason":"short"}]}
]}
Classify type: "front" = mainly the product front/name, no printed date; "date" = a printed expiry/manufacture date is visible; "other" = anything else (e.g. a barcode close-up).
Read labels for kind: 유통기한/소비기한/EXP/BEST BEFORE/까지 => expiry ; 제조일자/제조일/MFG => manufacture. "제조일로부터 9개월" => manufacture + months_rule=9.
Date rules: number>31 is the YEAR; number 13..31 is the DAY (remaining <=12 is month); 2-digit year => 20YY;
expiry dates are normally today or FUTURE (if a parse gives a past expiry, try swapping day/month);
Korean products default Y.M.D, European is D.M.Y;
if day and month are BOTH <=12 with no clear cue, set ambiguous=true and iso=null (do NOT guess).
Include every distinct date visible. If no date, "dates":[]. Return EXACTLY {n} objects, same order. Today is {today}."""

_CHUNK = 6


def _img_part(img):
    im = img.convert("RGB")
    im.thumbnail((1100, 1100))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return {"inline_data": {"mime_type": "image/jpeg",
                            "data": base64.b64encode(buf.getvalue()).decode()}}


def _generate(parts):
    body = {"contents": [{"parts": parts}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
    last = None
    for model in (config.GEMINI_MODEL, config.GEMINI_FALLBACK):
        for attempt in range(3):
            try:
                r = requests.post(_URL.format(model=model),
                                  params={"key": config.GEMINI_API_KEY}, json=body, timeout=120)
            except Exception as e:
                last = f"요청실패: {e}"; time.sleep(1.5 * (attempt + 1)); continue
            if r.status_code == 200:
                try:
                    return json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
                except Exception as e:
                    return {"error": f"응답파싱실패: {e}"}
            if r.status_code in (429, 500, 503):
                last = f"HTTP {r.status_code}"; time.sleep(1.5 * (attempt + 1)); continue
            return {"error": f"HTTP {r.status_code}: {r.text[:150]}"}
    return {"error": last or "알수없는 오류"}


def analyze_images(images, today):
    """images 순서대로 [{type,product_name,dates,error?}] 반환."""
    out = [None] * len(images)
    for start in range(0, len(images), _CHUNK):
        chunk = images[start:start + _CHUNK]
        prompt = _PROMPT.replace("{n}", str(len(chunk))).replace("{today}", today)
        parts = [{"text": prompt}] + [_img_part(im) for im in chunk]
        data = _generate(parts)
        arr = data.get("images") if isinstance(data, dict) else None
        err = data.get("error") if isinstance(data, dict) else "no result"
        for i in range(len(chunk)):
            if arr and i < len(arr) and isinstance(arr[i], dict):
                o = arr[i]
                o.setdefault("type", "other"); o.setdefault("product_name", None); o.setdefault("dates", [])
                out[start + i] = o
            else:
                out[start + i] = {"type": "other", "product_name": None, "dates": [], "error": err}
    return out
