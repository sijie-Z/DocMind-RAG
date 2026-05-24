@echo off
chcp 65001 >nul
title DocMind 启动器

echo ============================================
echo   DocMind 企业级 RAG 知识库系统
echo ============================================
echo.

:: ============================================================
:: 核心：启动前强制清端口，解决僵尸进程阻塞问题
:: Windows 关闭终端时 uvicorn --reload 的子进程不会退出，
:: 导致端口被占，下次启动绑定失败。
:: ============================================================

set BACKEND_PORT=8000
set FRONTEND_PORT=5173

echo [1/4] 检查并清理端口...

for %%P in (%BACKEND_PORT% %FRONTEND_PORT%) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%P " ^| findstr "LISTENING" 2^>nul') do (
        echo   关闭占用端口 %%P 的进程 PID=%%a
        taskkill /F /PID %%a >nul 2>&1
    )
)

:: 等待端口释放
timeout /t 2 /nobreak >nul

:: ============================================================
echo.
echo [2/4] 启动后端服务 (FastAPI :%BACKEND_PORT%)...

cd /d "%~dp0backend"
if not exist "venv\Scripts\activate.bat" (
    echo   [错误] 未找到虚拟环境，请先创建: python -m venv venv
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
start "DocMind-Backend" cmd /c "cd /d %~dp0backend && venv\Scripts\activate.bat && python -m uvicorn app.main:app --host 0.0.0.0 --port %BACKEND_PORT% --reload && pause"

:: 等待后端启动
echo   等待后端就绪...
:wait_backend
timeout /t 2 /nobreak >nul
curl -s http://localhost:%BACKEND_PORT%/health >nul 2>&1
if %errorlevel% neq 0 goto wait_backend
echo   后端已就绪 ^(http://localhost:%BACKEND_PORT%^)

:: ============================================================
echo.
echo [3/4] 启动前端服务 (Vite :%FRONTEND_PORT%)...

cd /d "%~dp0frontend"
start "DocMind-Frontend" cmd /c "cd /d %~dp0frontend && npm run dev && pause"

:: 等待前端启动
echo   等待前端就绪...
:wait_frontend
timeout /t 2 /nobreak >nul
curl -s http://localhost:%FRONTEND_PORT% >nul 2>&1
if %errorlevel% neq 0 goto wait_frontend
echo   前端已就绪 ^(http://localhost:%FRONTEND_PORT%^

:: ============================================================
echo.
echo [4/4] 启动完成！
echo.
echo   前端:   http://localhost:%FRONTEND_PORT%
echo   API 文档: http://localhost:%BACKEND_PORT%/docs
echo   健康检查: http://localhost:%BACKEND_PORT%/health
echo   演示账号: guest / 123456
echo.
echo   按任意键打开浏览器...
pause >nul
start http://localhost:%FRONTEND_PORT%

echo.
echo ============================================
echo   所有服务运行中。关闭此窗口不会停止服务。
echo   如需停止服务，请关闭 "DocMind-Backend"
echo   和 "DocMind-Frontend" 两个终端窗口。
echo ============================================
pause
