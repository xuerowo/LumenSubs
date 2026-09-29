@echo off
chcp 65001 >nul
cd /d "%~dp0"
title LumenSubs
set PYTHONIOENCODING=utf-8
set "PY="

rem 1) existing virtual environment
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys" >nul 2>nul && set "PY=.venv\Scripts\python.exe"
)

rem 2) Python launcher, preferred versions first
if not defined PY for %%V in (3.12 3.11 3.13 3.10) do (
  if not defined PY ( py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V" )
)

rem 3) python on PATH (skip the Microsoft Store alias / unsupported versions)
if not defined PY (
  python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)" >nul 2>nul && set "PY=python"
)

if not defined PY goto nopython

%PY% bootstrap.py %*
if errorlevel 1 (
  echo.
  echo 啟動失敗，請查看上方訊息。
  pause
)
goto :eof

:nopython
echo.
echo 找不到可用的 Python（需要 3.10 ~ 3.13，建議 3.12）。
echo 注意：python.org 首頁最上方的最新版（3.14 以上）目前還不能用。
echo.
where winget >nul 2>nul && (
  echo 可以在「命令提示字元」輸入下列指令自動安裝 Python 3.12：
  echo     winget install -e --id Python.Python.3.12
  echo.
)
echo 或從下方開啟的網頁，下載「Windows installer ^(64-bit^)」安裝，
echo 安裝時請勾選「Add python.exe to PATH」，完成後再執行一次 start.bat。
echo.
start "" "https://www.python.org/downloads/release/python-31210/"
pause
