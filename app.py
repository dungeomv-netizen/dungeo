# -*- coding: utf-8 -*-
"""유통기한 관리기 — Flask 서버."""
import datetime, traceback, re
from flask import Flask, request, jsonify, render_template, Response, session, redirect
import config, store_geo, date_prefs
from sheets import Sheet, _parse_date
from pipeline import process_batch
from imaging import load_image, read_exif

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024   # 200MB/배치
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
app.secret_key = config.SECRET_KEY
app.permanent_session_lifetime = datetime.timedelta(days=30)


@app.before_request
def _require_login():
    if not config.APP_PASSWORD:      # 로컬(비번 미설정)은 로그인 없음
        return
    p = request.path
    if p == "/login" or p == "/sw.js" or p.startswith("/static/"):
        return
    if session.get("ok"):
        return
    if p.startswith("/api/"):
        return jsonify(ok=False, error="로그인이 필요해요"), 401
    return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def login():
    err = ""
    if request.method == "POST":
        if request.form.get("pw", "") == config.APP_PASSWORD:
            session["ok"] = True
            session.permanent = True
            return redirect("/")
        err = "비밀번호가 틀렸어요"
    return render_template("login.html", err=err)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

SHEET = None
def get_sheet():
    global SHEET
    if SHEET is None:
        SHEET = Sheet().load()
    return SHEET


@app.route("/")
def index():
    s = get_sheet()
    return render_template("index.html",
                           live=config.live_mode(),
                           tabs=s.tabs,
                           stores=store_geo.load_stores())


_SW_JS = """
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => self.clients.claim());
self.addEventListener('fetch', e => { /* 네트워크 우선(패스스루) */ });
"""


@app.route("/sw.js")
def service_worker():
    resp = Response(_SW_JS, mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/api/status")
def status():
    s = get_sheet()
    return jsonify(live=config.live_mode(), tabs=s.tabs,
                   stores=store_geo.load_stores(),
                   product_count=s.product_count)


@app.route("/api/reload", methods=["POST"])
def reload_sheet():
    global SHEET
    SHEET = Sheet().load()
    return jsonify(ok=True, product_count=SHEET.product_count)


@app.route("/api/process", methods=["POST"])
def api_process():
    global SHEET
    try:
        s = get_sheet()
        batch_store = request.form.get("store", "").strip()
        fs_list = [f for f in request.files.getlist("photos") if f.filename]
        if not fs_list:
            return jsonify(ok=False, error="사진이 없어요"), 400
        results = process_batch(fs_list, batch_store, s)
        # 작업 끝나면 기입된 매장을 유통기한 날짜순으로 정렬
        sorted_tabs = []
        if config.live_mode():
            touched = {r.get("store") for r in results
                       if r.get("status") == "기입완료" and r.get("store")}
            for t in touched:
                try:
                    s.sort_by_expiry(t); sorted_tabs.append(t)
                except Exception as ex:
                    traceback.print_exc()
            if sorted_tabs:
                SHEET = Sheet().load()   # 행 순서 바뀜 → 인덱스 갱신
        return jsonify(ok=True, live=config.live_mode(), results=results, sorted_tabs=sorted_tabs)
    except Exception as e:
        traceback.print_exc()
        return jsonify(ok=False, error=str(e)), 500


@app.route("/api/sort", methods=["POST"])
def api_sort():
    global SHEET
    if not config.live_mode():
        return jsonify(ok=False, error="구글 연결 전이라 정렬 불가(미리보기 모드)"), 400
    s = get_sheet()
    done = []
    try:
        for t in s.tabs:
            s.sort_by_expiry(t); done.append(t)
        SHEET = Sheet().load()
        return jsonify(ok=True, sorted=done)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@app.route("/api/register", methods=["POST"])
def api_register():
    if not config.live_mode():
        return jsonify(ok=False, error="구글 연결 전이라 등록 불가(미리보기 모드)"), 400
    d = request.get_json(force=True)
    s = get_sheet()
    try:
        s.append_product(d["store"], d.get("category", ""), d["barcode"],
                         d.get("name", ""), d.get("dates", []))
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@app.route("/api/save", methods=["POST"])
def api_save():
    """확인필요 항목 수동 저장/수정. writes=[{col,value}]"""
    if not config.live_mode():
        return jsonify(ok=False, error="구글 연결 전이라 저장 불가(미리보기 모드)"), 400
    d = request.get_json(force=True)
    s = get_sheet()
    try:
        row = d.get("row")
        if not row:   # ambiguous 항목: 바코드+매장으로 행 찾기
            info = s.lookup(d["barcode"], d["store"])
            if not info or info.get("ambiguous"):
                return jsonify(ok=False, error="그 매장에서 바코드를 못 찾았어요"), 400
            row = info["row"]
        for w in d["writes"]:
            s.write_cell(d["store"], int(row), int(w["col"]), w["value"])
        # 애매했던 날짜를 골라 저장했으면, 이 제품의 날짜형식 기억(다음부터 자동)
        raw = d.get("raw"); learned = None
        val = d["writes"][0]["value"] if d.get("writes") else ""
        if raw and d.get("barcode") and re.match(r"\d{4}-\d{2}-\d{2}$", str(val)):
            learned = date_prefs.learn_from_choice(d["barcode"], raw, val)
        return jsonify(ok=True, learned=learned)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@app.route("/api/clear_date", methods=["POST"])
def api_clear_date():
    """임박알림에서 '삭제' — 그 상품의 해당 유통기한 날짜를 시트에서 지움."""
    if not config.live_mode():
        return jsonify(ok=False, error="구글 연결 전이라 삭제 불가(미리보기 모드)"), 400
    d = request.get_json(force=True)
    s = get_sheet()
    info = s.lookup(d["barcode"], d["store"])
    if not info or info.get("ambiguous"):
        return jsonify(ok=False, error="상품을 못 찾았어요"), 400
    row = info["row"]
    target = _parse_date(d["date"])
    try:
        for col in config.EXP_COLS:
            cur = s.current_value(d["store"], row, col)
            if cur and _parse_date(cur) == target:
                s.write_cell(d["store"], row, col, "")
                # 캐시 갱신(알림 즉시 반영)
                r = s._rows[d["store"]][row - 1]
                if len(r) >= col:
                    r[col - 1] = ""
                return jsonify(ok=True)
        return jsonify(ok=False, error="그 날짜를 시트에서 못 찾았어요"), 404
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@app.route("/api/alerts")
def api_alerts():
    s = get_sheet()
    return jsonify(ok=True, alerts=s.alerts(), today=datetime.date.today().isoformat(),
                   tiers=sorted(config.ALERT_DAYS),
                   expired_window=config.EXPIRED_WINDOW_DAYS)


@app.route("/api/learn_store", methods=["POST"])
def api_learn_store():
    tab = request.form.get("tab", "").strip()
    label = request.form.get("label", "").strip()
    f = request.files.get("photo")
    if not tab or not f:
        return jsonify(ok=False, error="매장과 사진이 필요해요"), 400
    img = load_image(f.read())
    _, gps = read_exif(img)
    if not gps:
        return jsonify(ok=False, error="이 사진엔 위치정보(GPS)가 없어요. 원본 사진(카톡X)으로 다시 시도"), 400
    store_geo.save_store(tab, gps[0], gps[1], label)
    return jsonify(ok=True, lat=gps[0], lng=gps[1])


if __name__ == "__main__":
    print(f"  유통기한 관리기 실행 → http://localhost:{config.PORT}")
    print(f"  모드: {'라이브(시트 기입)' if config.live_mode() else '미리보기(구글 연결 전)'}")
    app.run(host="0.0.0.0", port=config.PORT, debug=False)
