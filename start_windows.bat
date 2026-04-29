@echo off
chcp 65001 >nul
echo ========================================
echo    派聪明AI知识库系统 - 启动脚本
echo ========================================
echo.

:: 检查 Kafka 是否运行
echo [1] 检查 Kafka 服务...
curl -s http://localhost:9092 >nul 2>&1
if %errorlevel% neq 0 (
    echo     ⚠️  Kafka 未运行，文档处理将无法工作
    echo     提示: 请先启动 Kafka，或上传文档后手动运行 worker
) else (
    echo     ✅ Kafka 运行正常
)

:: 启动后端
echo.
echo [2] 启动后端 API 服务...
cd /d "%~dp0backend"
start "Backend API" cmd /k "venv\Scripts\activate && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
echo     ✅ 后端启动中... (端口 8000)

:: 启动 Worker (如果 Kafka 可用)
echo.
echo [3] 启动文档处理 Worker...
start "Document Worker" cmd /k "cd /d "%~dp0backend" && venv\Scripts\activate && python -m worker.doc_consumer"
echo     ✅ Worker 启动中...

:: 等待后端启动
echo.
timeout /t 3 /nobreak >nul

:: 启动前端
echo [4] 启动前端开发服务...
cd /d "%~dp0frontend"
start "Frontend Dev" cmd /k "npm run dev"
echo     ✅ 前端启动中... (端口 5173)

echo.
echo ========================================
echo    启动完成！请稍等几秒让服务完全启动
echo ========================================
echo.
echo  访问地址:
echo    前端界面: http://localhost:5173
echo    API 文档: http://localhost:8000/docs
echo.
echo  测试账号: guest / 123456
echo.
echo  按 Ctrl+C 可停止当前脚本，但不会关闭已启动的服务窗口
echo  要停止服务，请直接关闭对应的命令行窗口
echo.
pause