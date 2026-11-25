#!/bin/sh
# 容器启动入口：等数据库 → 建表灌演示数据 → 尝试灌知识库 → 起服务
set -e

echo "[deploy] 等待 PostgreSQL 就绪"
python - <<'PY'
import sys
import time

from sqlalchemy import create_engine, text

from config import settings

for attempt in range(1, 61):
    try:
        engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("[deploy] PostgreSQL 已就绪")
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        print(f"[deploy] 等待中 {attempt}/60：{str(exc).splitlines()[0][:120]}")
        time.sleep(2)

print("[deploy] PostgreSQL 连接超时")
sys.exit(1)
PY

echo "[deploy] 初始化表结构与演示数据"
python scripts/init_db.py

# Milvus 启动较慢或未配置 API Key 时，失败不阻塞商城主流程
echo "[deploy] 尝试灌入知识库（失败则降级运行）"
if python -m rag.ingest; then
    echo "[deploy] 知识库就绪"
else
    echo "[deploy] 知识库未就绪，可稍后重试：docker compose -f docker-compose.prod.yml exec app python -m rag.ingest"
fi

echo "[deploy] 启动 Web 服务"
exec uvicorn app:app \
    --host 0.0.0.0 \
    --port 8000 \
    --proxy-headers \
    --forwarded-allow-ips '*'
