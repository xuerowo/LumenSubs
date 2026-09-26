@echo off
chcp 65001 >nul
cd /d "%~dp0"
title LumenSubs
set PYTHONIOENCODING=utf-8
python run.py
if errorlevel 1 pause
