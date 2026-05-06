#!/bin/bash
set -e

echo "========================================"
echo "   DocMind RAG 知识库系统 - 启动脚本"
echo "========================================"
echo ""

# 检查 Kafka
echo "[1] 检查 Kafka 服务..."
if curl -s http://localhost:9092 > /dev/null 2>&1; then
    echo "    ✅ Kafka 运行正常"
else
    echo "    ⚠️  Kafka 未运行，文档处理将无法工作"
fi

# 启动后端
echo "[2] 启动后端 API 服务..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/backend"
source venv/bin/activate 2>/dev/null || true
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
echo "    ✅ 后端启动中... (端口 8000)"

# 启动 Worker
echo "[3] 启动文档处理 Worker..."
python -m worker.doc_consumer &
echo "    ✅ Worker 启动中..."

# 等待后端启动
sleep 3

# 启动前端
echo "[4] 启动前端开发服务..."
cd "$SCRIPT_DIR/frontend"
npm run dev &
echo "    ✅ 前端启动中... (端口 5173)"

echo ""
echo "========================================"
echo "   启动完成！请稍等几秒让服务完全启动"
echo "========================================"
echo ""
echo " 访问地址:"
echo "   前端界面: http://localhost:5173"
echo "   API 文档: http://localhost:8000/docs"
echo ""
echo " 测试账号: guest / 123456"
echo ""
