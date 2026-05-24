@echo off
chcp 65001 >nul
echo 清理 DocMind 开发端口...

for %%P in (8000 5173 5174) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%P " ^| findstr "LISTENING" 2^>nul') do (
        echo   关闭端口 %%P (PID=%%a)
        taskkill /F /PID %%a >nul 2>&1
    )
)

echo 完成。
pause
