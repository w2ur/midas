# Midas Backtester Service

FastAPI service that runs strategy backtests on demand. Wraps the existing
`engine/` codebase. Deployed to Google Cloud Run as a standalone service,
consumed by the backtester tool on `william.revah.paris` via a Netlify
proxy.

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
gcloud services enable run.googleapis.com cloudbuild.googleapis.com containerregistry.googleapis.com secretmanager.googleapis.com
gcloud config set run/region europe-west1
```

One-time secret setup — the pipeline injects `BACKTESTER_SECRET` from Secret
Manager, so the secret must exist before the first deploy:

```bash
printf '%s' "$(openssl rand -hex 32)" | gcloud secrets create backtester-secret --data-file=-
# Grant the Cloud Run runtime service account access if not already granted:
gcloud secrets add-iam-policy-binding backtester-secret \
  --member="serviceAccount:<PROJECT_NUMBER>-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

Build and deploy (single command, run from the repo root):

```bash
gcloud builds submit --config cloudbuild.yaml
```

The pipeline (defined in `/cloudbuild.yaml`):

1. Builds `backtester/Dockerfile` with the repo root as build context (so
   `COPY engine`, `COPY data/market`, etc. resolve).
2. Pushes the image to `gcr.io/$PROJECT_ID/midas-backtester`.
3. Deploys it to Cloud Run reachable over IAM (`--allow-unauthenticated`) —
   required so the Netlify proxy, which is not a GCP principal, can reach it
   without a long-lived service-account key — with `BACKTESTER_SECRET`
   injected from Secret Manager (`backtester-secret`) as the real lock:
   `/run` and `/catalog` require a matching `X-Backtester-Secret` header,
   alongside sensible resource defaults (1 GiB / 1 vCPU / 5 min timeout /
   scale-to-zero, `--max-instances=3` capping abuse). `/healthz` needs no
   secret and is reachable directly.

Cloud Build prints the deployed service URL near the end of the output —
something like `https://midas-backtester-xxxxxx-ew.a.run.app`. Save it; the
site needs it on Vercel as `PUBLIC_BACKTESTER_URL`.

`/run` and `/catalog` answer only requests carrying `X-Backtester-Secret:
$BACKTESTER_SECRET`; the `william.revah.paris` Netlify proxy is the sole
holder of that secret.

## Smoke-test the deployed service

```bash
curl -sf $SERVICE_URL/healthz
```

Should return `{"status":"ok"}`. No secret header needed — the service is
IAM-reachable and `/healthz` is unauthenticated at the app layer too.

## Free-tier monitoring

Set a billing alert at $1/month in the GCP console. Cloud Run's free tier
covers 2M requests + 360k vCPU-seconds + 180k GB-seconds per month.
Realistic backtester usage sits well below this. Going over signals either
viral traffic (good problem) or a runaway loop (bug to fix).

## Re-deploys

Each time you want to push new code: `gcloud builds submit --config cloudbuild.yaml`
from the repo root. The image is built fresh and Cloud Run rolls the new
revision in — same IAM-open + app-secret posture, same `BACKTESTER_SECRET`
pulled fresh from Secret Manager, unchanged across redeploys. Old revisions
stay around until you prune them.

## Future: automated deploys

Out of scope for v1. A GitHub Actions workflow with Workload Identity
Federation that runs the same `gcloud builds submit` on every push to
`main` is a sensible follow-up.
