#!/usr/bin/env bash
# 构建 DocMind Demo 前端镜像（在仓库根目录执行）。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

echo "[demo] building docmind-demo-frontend:latest"
docker build -f deploy/demo/Dockerfile.frontend -t docmind-demo-frontend:latest .
echo "[demo] done"
