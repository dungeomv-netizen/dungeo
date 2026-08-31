@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=C:\Users\Oner Kim\AppData\Local\Programs\Python\Python311\python.exe"
if not exist "%PY%" set "PY=python"

rem 이미 켜져 있으면 브라우저만 열기
netstat -ano | findstr ":8800" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
  start "" http://localhost:8800
  exit /b
)

rem 서버를 별도 최소화 창으로 실행 (이 창은 켜둬야 함, 끄려면 그 창을 닫기)
start "유통기한관리 서버 (닫지 마세요)" /min "%PY%" app.py

rem 서버가 뜰 때까지 잠깐 대기 후 브라우저 열기
ping -n 6 127.0.0.1 >nul
start "" http://localhost:8800
exit /b
