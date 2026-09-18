# 服务镜像：代码 + 受治理语料；索引与模型缓存放卷，首启无索引时自动入库
# 构建上下文需含 src/ data/ requirements.txt docker/
FROM python:3.12-slim

WORKDIR /app

# 依赖层独立于代码变更；torch 先装 CPU 版（服务侧 embedding 只跑 CPU，CUDA 是死重）
COPY requirements.txt .
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt

# 应用与语料（第三方语料许可见 data/THIRD_PARTY_NOTICES.md，不适用 MIT）
COPY src/ ./src/
COPY data/ ./data/

# 索引/父存储/模型缓存走卷；数据目录覆盖与 config.py 环境变量约定一致
ENV PYTHONPATH=/app/src \
    HF_HOME=/app/.cache/huggingface \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed \
    QDRANT_DB_PATH=/app/qdrant_db \
    PARENT_STORE_PATH=/app/parent_store
VOLUME ["/app/qdrant_db", "/app/parent_store", "/app/.cache"]

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]