#!/bin/bash
# DocMind RAG 知识库系统 - 停止脚本

echo "========================================"
echo "   DocMind RAG 知识库系统 - 停止脚本"
echo "========================================"
echo ""

# 停止后端 (uvicorn)
echo "正在停止后端 API 服务..."
pkill -f "uvicorn app.main:app" 2>/dev/null && echo "✅ 后端 API 服务已停止" || echo "⚠️  后端服务未找到"

# 停止 Worker
echo "正在停止文档处理 Worker..."
pkill -f "worker.doc_consumer" 2>/dev/null && echo "✅ Worker 服务已停止" || echo "⚠️  Worker 服务未找到"

# 停止前端 (vite dev server)
echo "正在停止前端服务..."
pkill -f "vite" 2>/dev/null && echo "✅ 前端服务已停止" || echo "⚠️  前端服务未找到"

echo ""
echo "========================================"
echo "   所有服务已停止"
echo "========================================"
