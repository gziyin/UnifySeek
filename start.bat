@echo off
setlocal
cd /d "%~dp0"

REM ============================================================
REM  AI Dev Researcher launcher
REM  Backend : FastAPI (port from backend\.env APP_PORT, default 8000)
REM  Frontend: Vite :5173
REM  NOTE: keep this file CRLF + ASCII only.
REM        Windows shell parses multi-line blocks by CRLF; LF-only
REM        or non-ASCII comments will crash the script.
REM ============================================================

set "APP_PORT=8000"

REM 1. Backend venv check
if not exist "backend\.venv\Scripts\python.exe" (
    echo [error] backend\.venv not found. Run: cd backend ^&^& uv sync --extra agent --extra rag
    exit /b 1
)

REM 2. Frontend deps check
if not exist "frontend\node_modules" (
    echo [error] frontend\node_modules not found. Run: cd frontend ^&^& npm install
    exit /b 1
)

REM 3. Read APP_PORT from backend\.env (fallback 8000)
if exist "backend\.env" (
    for /f "usebackq tokens=1,* delims==" %%a in ("backend\.env") do (
        if /i "%%a"=="APP_PORT" set "APP_PORT=%%b"
    )
)

REM 4. Start backend.
REM    start resolves the program path against the CURRENT dir,
REM    NOT the /D dir. So use the absolute python path here.
echo [start] Backend : http://127.0.0.1:%APP_PORT%
start "ai-dev-researcher-backend" /D "%~dp0backend" "%~dp0backend\.venv\Scripts\python.exe" -m ai_dev_researcher.main

REM 5. Check frontend tooling.
where node.exe >nul 2>nul
if errorlevel 1 (
    echo [error] node.exe not found in PATH. Install Node.js first.
    pause
    exit /b 1
)
where npm.cmd >nul 2>nul
if errorlevel 1 (
    echo [error] npm.cmd not found in PATH. Install Node.js first.
    pause
    exit /b 1
)

REM 6. Start frontend.
REM    Explicit cd /d and npm.cmd avoid relying on start /D and on a
REM    bare npm that may resolve to the extensionless bash wrapper.
set "VITE_BACKEND_PORT=%APP_PORT%"
set "FRONTEND_DIR=%~dp0frontend"
echo [start] Frontend: http://127.0.0.1:5173
start "ai-dev-researcher-frontend" /D "%FRONTEND_DIR%" "%ComSpec%" /k "cd /d "%FRONTEND_DIR%" && npm.cmd run dev"

REM 7. Wait for the frontend to become ready.
echo [wait] Waiting for Frontend on http://127.0.0.1:5173 ...
for /l %%i in (1,1,10) do (
    curl.exe -fsS --max-time 1 http://127.0.0.1:5173/ >nul 2>nul
    if not errorlevel 1 goto frontend_ready
    ping -n 2 127.0.0.1 >nul
)
echo [error] Frontend did not become ready on http://127.0.0.1:5173
echo [error] Check the ai-dev-researcher-frontend window for npm/vite output.
pause
exit /b 1

:frontend_ready
echo [ok] Frontend ready: http://127.0.0.1:5173

echo.
echo [done] Backend : http://127.0.0.1:%APP_PORT%  (from backend\.env APP_PORT)
echo         Frontend: http://127.0.0.1:5173
echo         Close the backend window or press Ctrl+C to stop.
endlocal
