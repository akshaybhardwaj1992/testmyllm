FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    RAGAS_DO_NOT_TRACK=true \
    DEEPEVAL_TELEMETRY_OPT_OUT=YES \
    DEEPTEAM_TELEMETRY_OPT_OUT=YES \
    HOME=/home/app

WORKDIR /app
# DejaVu fonts: Unicode-capable font for PDF report exports
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN useradd --create-home app && mkdir -p /data/exports && chown -R app /app /data
USER app
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
