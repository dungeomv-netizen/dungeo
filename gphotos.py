# -*- coding: utf-8 -*-
"""구글 포토 Picker API — 사용자가 고른 사진만 가져옴(전체 사진첩 접근 아님).
흐름: create_session → 사용자가 pickerUri에서 사진 선택 → get_session(mediaItemsSet 확인)
      → list_media → download(고른 것만). 토큰은 브라우저(GIS)에서 받아 넘겨줌.
"""
import requests

_BASE = "https://photospicker.googleapis.com/v1"


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


def create_session(token):
    r = requests.post(f"{_BASE}/sessions", headers=_hdr(token), json={}, timeout=30)
    r.raise_for_status()
    d = r.json()
    return d["id"], d.get("pickerUri", "")


def get_session(token, sid):
    r = requests.get(f"{_BASE}/sessions/{sid}", headers=_hdr(token), timeout=30)
    r.raise_for_status()
    return r.json()


def list_media(token, sid):
    """고른 사진 목록(페이지 전부). 각 항목: {id, createTime, mediaFile:{baseUrl,filename,...}}"""
    items, page = [], None
    while True:
        params = {"sessionId": sid, "pageSize": 100}
        if page:
            params["pageToken"] = page
        r = requests.get(f"{_BASE}/mediaItems", headers=_hdr(token), params=params, timeout=30)
        r.raise_for_status()
        d = r.json()
        items += d.get("mediaItems", [])
        page = d.get("nextPageToken")
        if not page:
            break
    return items


def download(token, base_url):
    """고른 사진 1장을 '원본(=d)'으로 다운로드.
    ★원본이라야 EXIF 촬영시각이 남아 제품 순서·경계가 정확하고 바코드도 선명함.
    (축소본 =w..-h.. 은 EXIF가 지워져 순서가 엉키고 바코드가 흐려짐)"""
    r = requests.get(f"{base_url}=d", headers=_hdr(token), timeout=90)
    r.raise_for_status()
    return r.content


def delete_session(token, sid):
    try:
        requests.delete(f"{_BASE}/sessions/{sid}", headers=_hdr(token), timeout=15)
    except Exception:
        pass
