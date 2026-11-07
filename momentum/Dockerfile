FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MOM_BACKEND=gcp
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mom ./mom
COPY portal ./portal
# The portal runs this; the weekly job overrides it with `python -m mom.weekly`.
CMD exec uvicorn portal.app:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'
