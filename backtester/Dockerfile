# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# The service's OWN closure, not the monorepo's. The root requirements.txt
# covers 6 workflows, local dev, the sandbox and the agents; installing it here
# put pyarrow, ccxt, plotly, streamlit, pydeck, lxml and pytest in an image
# whose cold start is dominated by pulling itself. Versions are copied verbatim
# from the root lockfile, so this narrows the package set without ever
# resolving a different version than CI tests. See backtester/requirements.txt.
COPY backtester/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY engine /app/engine
COPY backtester /app/backtester
COPY roster.yaml /app/roster.yaml
COPY data/market /app/data/market
COPY data/baselines /app/data/baselines
COPY data/portfolios /app/data/portfolios
COPY data/strategies /app/data/strategies
COPY data/universes /app/data/universes

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn backtester.app:app --host 0.0.0.0 --port ${PORT}"]
