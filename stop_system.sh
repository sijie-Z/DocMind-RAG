#!/bin/bash
# 派聪明系统停止脚本

set -e

echo "🛑 停止派聪明AI知识库系统..."

# 停止服务的函数
stop_service() {
    local pid_file=$1
    local service_name=$2
    
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if ps -p $pid > /dev/null 2>&1; then
            echo "停止 $service_name (PID: $pid)..."
            kill $pid
            sleep 2
            
            # 如果进程还在，强制停止
            if ps -p $pid > /dev/null 2>&1; then
                echo "强制停止 $service_name..."
                kill -9 $pid
            fi
            
            rm -f "$pid_file"
            echo "✅ $service_name 已停止"
        else
            echo "⚠️  $service_name 未运行"
            rm -f "$pid_file"
        fi
    else
        echo "⚠️  $service_name PID文件不存在"
    fi
}

# 停止各个服务
stop_service logs/backend.pid "后端API服务"
stop_service logs/processor.pid "文件处理服务"
stop_service logs/monitor.pid "监控服务"
stop_service logs/frontend.pid "前端服务"

echo ""
echo "✅ 派聪明系统已完全停止"