FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app

# 先只拷依赖清单，改代码时不用重装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY eval ./eval
COPY scripts ./scripts
COPY README.md .

# 数据库不入镜像：compose 把本地 data/ 只读挂进来，避免镜像里带 27MB 数据
RUN useradd -m app && mkdir -p /app/logs && chown -R app:app /app
USER app

EXPOSE 8000 8501
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
