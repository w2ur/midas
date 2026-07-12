# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY engine /app/engine
COPY backtester /app/backtester
COPY data/market /app/data/market
COPY data/baselines /app/data/baselines
COPY data/portfolios /app/data/portfolios
COPY data/strategies /app/data/strategies
COPY data/universes /app/data/universes

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn backtester.app:app --host 0.0.0.0 --port ${PORT}"]
