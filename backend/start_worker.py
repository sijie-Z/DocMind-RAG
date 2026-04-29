#!/usr/bin/env python
"""Worker启动脚本 - 用于Docker容器内启动Kafka消费者"""
import sys
import asyncio

sys.path.insert(0, '/app')

from app.worker.consumer import consume

if __name__ == "__main__":
    asyncio.run(consume())
