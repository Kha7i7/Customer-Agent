FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 依赖单独一层，代码改动不会触发重装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 不用 root 跑服务
RUN useradd -m -u 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["sh", "/app/deploy/start.sh"]
