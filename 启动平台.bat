@echo off
chcp 65001 >nul
cd /d "%~dp0"
title FreeFarmNRS V1.0

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到运行环境 .venv
    echo 请先在项目目录执行：
    echo     python -m venv --system-site-packages .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo 正在启动 FreeFarmNRS，请稍候…
".venv\Scripts\python.exe" run.py

if errorlevel 1 (
    echo.
    echo 服务异常退出，请检查上方日志。
    pause
)
