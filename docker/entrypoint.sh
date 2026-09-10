#!/bin/sh
# 镜像入口：无索引时先入库（需联网下载嵌入模型），再启动 API
set -e

cd /app/src

if [ ! -f "${QDRANT_DB_PATH:-/app/qdrant_db}/snapshot_manifest.json" ]; then
    echo "[entrypoint] 未发现快照，开始构建本地索引（首次需下载嵌入模型）..."
    python ingest_corpus.py
fi

echo "[entrypoint] 启动 API (host=${API_HOST:-0.0.0.0}, port=${API_PORT:-8000})"
exec python -m uvicorn api.main:app --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}"