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

If Docker is not installed locally, this step can be skipped — Cloud Build
produces the image remotely during deploy.

## Deploy to Cloud Run

One-time setup:

```bash
gcloud auth login
gcloud projects create midas-backtester-<unique-suffix>
gcloud config set project midas-backtester-<unique-suffix>
# Link a billing account in the GCP console first; required even on free tier.
gcloud services enable run.googleapis.com cloudbuild.googleapis.com containerregistry.googleapis.com
gcloud config set run/region europe-west1
```

Build and deploy (single command, run from the repo root):

```bash
gcloud builds submit --config cloudbuild.yaml
```

The pipeline (defined in `/cloudbuild.yaml`):

1. Builds `backtester/Dockerfile` with the repo root as build context (so
   `COPY engine`, `COPY data/market`, etc. resolve).
2. Pushes the image to `gcr.io/$PROJECT_ID/midas-backtester`.
3. Deploys it to Cloud Run with sensible defaults (1 GiB / 1 vCPU / 5 min
   timeout / scale-to-zero).

Cloud Build prints the deployed service URL near the end of the output —
something like `https://midas-backtester-xxxxxx-ew.a.run.app`. Save it; the
site needs it on Vercel as `PUBLIC_BACKTESTER_URL`.

## Smoke-test the deployed service

```bash
curl -sf $SERVICE_URL/healthz
```

Should return `{"status":"ok"}`.

## Free-tier monitoring

Set a billing alert at $1/month in the GCP console. Cloud Run's free tier
covers 2M requests + 360k vCPU-seconds + 180k GB-seconds per month.
Realistic backtester usage sits well below this. Going over signals either
viral traffic (good problem) or a runaway loop (bug to fix).

## Re-deploys

Each time you want to push new code: `gcloud builds submit --config cloudbuild.yaml`
from the repo root. The image is built fresh and Cloud Run rolls the new
revision in. Old revisions stay around until you prune them.

## Future: automated deploys

Out of scope for v1. A GitHub Actions workflow with Workload Identity
Federation that runs the same `gcloud builds submit` on every push to
`main` is a sensible follow-up.
