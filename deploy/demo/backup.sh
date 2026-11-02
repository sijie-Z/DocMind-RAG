#!/usr/bin/env bash
# 备份 Demo 数据到 deploy/demo/backups/<timestamp>/：
#   - MySQL 逻辑备份：用户/组织/文档元数据/案件/决策（源真）
#   - MinIO 原始文件：重建索引所需（丢了就无法 reprocess）
# 卷名默认 backend_minio-data（compose 项目名默认取 backend/ 目录名）；
# 自定义项目名时用 MINIO_VOLUME=... 覆盖。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

TS="$(date +%Y%m%d-%H%M%S)"
OUT="deploy/demo/backups/$TS"
mkdir -p "$OUT"

COMPOSE=(docker compose --env-file backend/.env.docker
  -f backend/docker-compose.yml -f deploy/demo/docker-compose.demo.yml)

echo "[backup] mysqldump ..."
"${COMPOSE[@]}" exec -T mysql sh -c \
  'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines "$MYSQL_DATABASE"' \
  | gzip > "$OUT/mysql-$TS.sql.gz"

VOLUME="${MINIO_VOLUME:-backend_minio-data}"
echo "[backup] minio volume $VOLUME ..."
docker run --rm -v "$VOLUME":/data:ro -v "$PWD/$OUT":/backup alpine:3.20 \
  sh -c "tar czf /backup/minio-$TS.tar.gz -C /data ."

echo "[backup] done -> $OUT"
