# -*- coding: utf-8 -*-
"""바탕화면 바로가기용 런처.
- 서버가 안 떠 있으면 app.py를 새 콘솔로 실행(그 창을 닫으면 종료)
- 서버가 뜰 때까지 기다렸다가 브라우저 자동 오픈
pythonw.exe 로 실행되어 런처 자체는 창이 안 뜸.
"""
import socket, subprocess, sys, time, webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent
PORT = 8800


def up():
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", PORT)) == 0
    except Exception:
        return False
    finally:
        s.close()


if not up():
    exe = Path(sys.executable)
    pyexe = exe.with_name("python.exe")   # 서버는 콘솔 보이게
    if not pyexe.exists():
        pyexe = exe
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    try:
        subprocess.Popen([str(pyexe), str(BASE / "app.py")],
                         cwd=str(BASE), creationflags=flags)
    except Exception:
        subprocess.Popen([str(pyexe), str(BASE / "app.py")], cwd=str(BASE))
    for _ in range(90):        # 최대 ~36초 대기
        if up():
            break
        time.sleep(0.4)

webbrowser.open(f"http://localhost:{PORT}")
