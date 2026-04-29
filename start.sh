#!/bin/bash

echo "🏢 启动企业级AI知识库系统..."
echo ""

# 检查Python环境
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到Python环境，请先安装Python 3.8+"
    exit 1
fi

# 启动系统
echo "🚀 正在启动系统..."
python3 enterprise_ai_system.py --load-sample-data

if [ $? -eq 0 ]; then
    echo "✅ 系统启动成功！"
else
    echo "❌ 系统启动失败，请检查错误日志"
fi

echo ""
echo "🌐 访问地址："
echo "   前端界面: http://localhost:5173"
echo "   API文档: http://localhost:8000/docs"
echo "   监控面板: http://localhost:8000/dashboard"
