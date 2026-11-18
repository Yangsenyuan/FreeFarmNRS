#!/bin/sh
# 容器启动：若挂载了持久磁盘（NMASS_DATA_DIR 不是镜像内目录），首次启动时复制种子数据
set -e

SEED_DIR=/app/data
TARGET="${NMASS_DATA_DIR:-$SEED_DIR}"

if [ "$TARGET" != "$SEED_DIR" ] && [ ! -f "$TARGET/.seeded" ]; then
    echo "[entrypoint] 初始化数据目录 $TARGET（复制示例项目与模型）…"
    mkdir -p "$TARGET"
    cp -a "$SEED_DIR/." "$TARGET/"
    touch "$TARGET/.seeded"
    echo "[entrypoint] 完成"
fi

exec uvicorn server.main:app --host 0.0.0.0 --port "${PORT:-8765}"
