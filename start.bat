@echo off
setlocal
cd /d "%~dp0"

REM ============================================================
REM  AI Dev Researcher 一键启动脚本
REM  同时启动后端（FastAPI，端口取 backend\.env 的 APP_PORT）与前端（Vite :5173）
REM ============================================================

echo [start] AI Dev Researcher 启动中...
echo.

REM 0. 从 backend\.env 读取 APP_PORT（默认 8000）
set "APP_PORT=8000"
if exist "backend\.env" (
    for /f "usebackq tokens=1,* delims==" %%a in ("backend\.env") do (
        if /i "%%a"=="APP_PORT" set "APP_PORT=%%b"
    )
)

REM 1. 后端：确认 .venv 存在
if not exist "backend\.venv\Scripts\python.exe" (
    echo [error] 未找到 backend\.venv，请先执行:
    echo     cd backend ^&^& uv sync --extra agent --extra rag
    exit /b 1
)

REM 2. 前端：确认依赖已安装
if not exist "frontend\node_modules" (
    echo [error] 未找到 frontend\node_modules，请先执行:
    echo     cd frontend ^&^& npm install
    exit /b 1
)

REM 3. 校验 .env（提示 WORKSPACE_ROOT 指向 backend）
if not exist "backend\.env" (
    echo [warn] 未找到 backend\.env，从 backend\.env.example 复制并填入 key。
    echo        临时使用默认配置启动（fake 模式）。
)

REM 4. 启动后端（python 用绝对路径，start 不按 /D 目录解析程序路径）
echo [start] 启动后端 http://127.0.0.1:%APP_PORT% ...
start "ai-dev-researcher-backend" /D "%~dp0backend" "%~dp0backend\.venv\Scripts\python.exe" -m ai_dev_researcher.main

REM 5. 启动前端
echo [start] 启动前端 http://127.0.0.1:5173 ...
start "ai-dev-researcher-frontend" /D "%~dp0frontend" cmd /k "npm run dev"

echo.
echo [done] 两个窗口已分别打开。
echo        后端地址: http://127.0.0.1:%APP_PORT%   （由 backend\.env 的 APP_PORT 控制）
echo        前端地址: http://127.0.0.1:5173
echo        关闭后端窗口或按 Ctrl+C 可停止。
endlocal
