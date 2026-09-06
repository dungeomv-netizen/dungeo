# -*- coding: utf-8 -*-
"""바탕화면 바로가기용 런처.
- ★기존에 돌던 옛 서버(8800 포트)를 먼저 종료하고 → 항상 최신 코드로 새로 시작
  (안 그러면 코드를 고쳐도 옛 서버가 계속 살아있어 반영이 안 됨)
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


def _pids_on_port(port):
    """해당 포트를 LISTEN 중인 PID 목록(netstat). 8800만 골라서 다른 파이썬 작업은 안 건드림."""
    try:
        out = subprocess.check_output(["netstat", "-ano"], text=True, errors="ignore")
    except Exception:
        return []
    pids = set()
    for line in out.splitlines():
        if (f":{port} " in line or f":{port}\t" in line) and "LISTEN" in line.upper():
            parts = line.split()
            if parts and parts[-1].isdigit():
                pids.add(parts[-1])
    return list(pids)


# 1) 옛 서버 종료(있으면) — 항상 최신 코드로 재시작하려고
for pid in _pids_on_port(PORT):
    try:
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
    except Exception:
        pass
if _pids_on_port(PORT):
    time.sleep(1.0)

# 2) 새 서버 시작(콘솔 보이게)
exe = Path(sys.executable)
pyexe = exe.with_name("python.exe")
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
