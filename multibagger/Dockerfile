FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MB_BACKEND=gcp
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agents.yaml ./
COPY mb ./mb
COPY portal ./portal
# The portal and guard services run this; agent jobs override it with `python -m mb.runner <agent>`.
CMD exec uvicorn portal.app:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'
