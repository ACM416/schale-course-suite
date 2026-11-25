FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SCHALE_DATA_DIR=/app/data \
    SCHALE_HOST=0.0.0.0 \
    UOOC_PORT=51088 \
    ZHS_PORT=51089 \
    CX_PORT=51092

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data

EXPOSE 51088 51089 51092

VOLUME ["/app/data"]

CMD ["python", "server.py"]
