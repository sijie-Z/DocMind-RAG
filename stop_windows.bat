@echo off
chcp 65001 >nul
echo ========================================
echo    派聪明AI知识库系统 - 停止脚本
echo ========================================
echo.

echo 正在停止所有服务...

:: 关闭 uvicorn (后端)
taskkill /f /im python.exe /fi "WINDOWTITLE eq Backend*" 2>nul
if %errorlevel% equ 0 (
    echo ✅ 后端 API 服务已停止
) else (
    echo ⚠️  后端服务未找到或已停止
)

:: 关闭 worker
taskkill /f /im python.exe /fi "WINDOWTITLE eq Document*" 2>nul
if %errorlevel% equ 0 (
    echo ✅ Worker 服务已停止
) else (
    echo ⚠️  Worker 服务未找到或已停止
)

:: 关闭前端
taskkill /f /im node.exe /fi "WINDOWTITLE eq Frontend*" 2>nul
if %errorlevel% equ 0 (
    echo ✅ 前端服务已停止
) else (
    echo ⚠️  前端服务未找到或已停止
)

echo.
echo ========================================
echo    所有服务已停止
echo ========================================
pause