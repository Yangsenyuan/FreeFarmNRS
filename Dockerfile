# 农田氮素遥感监测与反演平台 —— Render / Docker 部署镜像
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    NMASS_HOST=0.0.0.0

# GDAL 由 rasterio wheel 自带；这里只补中文字体（PNG/PDF 导出需要）与运行库
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-wqy-microhei \
        fonts-droid-fallback \
        libexpat1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-render.txt ./
RUN pip install --no-cache-dir -r requirements-render.txt

COPY . .
RUN chmod +x deploy/entrypoint.sh

# 默认直接使用镜像内数据（容器内可写，重启后重置）
# 挂载持久磁盘时，由 entrypoint.sh 把种子数据复制到 NMASS_DATA_DIR
ENV NMASS_DATA_DIR=/app/data

EXPOSE 8765

ENTRYPOINT ["/app/deploy/entrypoint.sh"]
