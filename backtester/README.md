# Midas Backtester Service

FastAPI service that runs strategy backtests on demand. Wraps the existing
`engine/` codebase. Deployed to Google Cloud Run; consumed by the
`/simulate` page on `midas.revah.paris`.

## Local dev

```bash
pip install -r requirements.txt
uvicorn backtester.app:app --reload --port 8080
curl http://localhost:8080/healthz
```

Run tests:
```bash
pytest backtester/tests/ -v
```

## Container build (optional, requires Docker)

```bash
docker build -f backtester/Dockerfile -t midas-backtester:dev .
docker run --rm -p 8080:8080 midas-backtester:dev
```

If Docker is not installed locally, this step can be skipped — Cloud Run
builds the image via Cloud Build during deploy.

## Deploy to Cloud Run

One-time setup:
```bash
gcloud auth login
gcloud projects create midas-backtester-<unique-suffix>
gcloud config set project midas-backtester-<unique-suffix>
gcloud services enable run.googleapis.com cloudbuild.googleapis.com
gcloud config set run/region europe-west1
```

Build and deploy from the repo root:
```bash
gcloud run deploy midas-backtester \
  --source . \
  --dockerfile backtester/Dockerfile \
  --region europe-west1 \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --timeout 300 \
  --concurrency 4 \
  --min-instances 0 \
  --max-instances 5
```

Cloud Run prints a service URL like
`https://midas-backtester-xxxxxx-ew.a.run.app`. Save it — the site needs it
in `site/.env.production` as `PUBLIC_BACKTESTER_URL`.

## Smoke-test the deployed service

```bash
curl -sf $SERVICE_URL/healthz
```

Should return `{"status":"ok"}`.

## Free-tier monitoring

Set a billing alert at $1/month. Cloud Run's free tier covers 2M requests +
360k vCPU-seconds + 180k GB-seconds per month. Realistic backtester usage
sits well below this. Going over signals either viral traffic (good
problem) or a runaway loop (bug to fix).

## Future: automated deploys

Out of scope for v1. The current process is a manual `gcloud run deploy`
after every change. A GitHub Actions workflow with Workload Identity
Federation is a follow-up.
