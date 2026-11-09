FROM python:3.12-slim
# Needed for exact Europe/Berlin scheduling including daylight saving time.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY server.py categories.py ./
COPY web/ ./web/
ENV HOST=0.0.0.0 PORT=8765 DATA_DIR=/app/data PYTHONUNBUFFERED=1
EXPOSE 8765
CMD ["python", "server.py"]
