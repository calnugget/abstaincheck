FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY abstaincheck ./abstaincheck
COPY web ./web
ENV PORT=8080 ABSTAINCHECK_STORE=firestore
CMD exec uvicorn abstaincheck.app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'
