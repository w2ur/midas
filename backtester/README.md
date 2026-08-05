# Midas Backtester Service

FastAPI service that runs strategy backtests on demand. Wraps the existing
`engine/` codebase. Deployed to Google Cloud Run as a standalone service,
consumed by the backtester tool on `william.revah.paris` via a Netlify
proxy.

## Local dev

```bash
pip install -r backtester/requirements.txt   # the service's own closure; the
                                             # root requirements.txt is the
                                             # whole monorepo's and is ~264 MB
                                             # heavier for no benefit here
uvicorn backtester.app:app --reload --port 8080
curl http://localhost:8080/health
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

The build context is the repo root and is filtered by `/.dockerignore`
(local `docker build`) and `/.gcloudignore` (`gcloud builds submit`). Neither
may exclude anything the Dockerfile `COPY`s — `data/market/ohlcv` in
particular, which is gitignored in some checkouts but must reach the build.
There used to be a `backtester/.dockerignore`; Docker never read it (it only
reads the file at the *context root*), and it shipped inside the published
image as a regular file. It is gone.

## Cold start — what the image is made of

The service's cold start is dominated by pulling its own image, so the
Dockerfile is written around that. Measured on the last single-stage revision
(`midas-backtester-00007-hhv`, digest `b6303a303bde`): **455.7 MB compressed /
~1.53 GB uncompressed**, of which

| layer | uncompressed | compressed |
|---|---|---|
| `pip install` | 850.4 MB | 266.7 MB |
| `apt install gcc g++` | 258.9 MB | 93.7 MB |
| `COPY data/market` | 298.2 MB | 51.9 MB |
| Debian + CPython base | 123.3 MB | 43.2 MB |

Uvicorn's own startup, by contrast, is **2 ms**. None of the 130–230 s is
application code.

Three rules follow, and each has already been violated once:

1. **Build tooling never ships.** `gcc`/`g++` live in the builder stage only.
2. **Nothing is installed for a code path that is turned off.** `pyarrow` was
   150 MB of the image, and existed solely for `engine.market_data`'s parquet
   query cache — which this service no longer enables (see the note in
   `runner.py`).
3. **`__pycache__` stays.** It is 143 MB and it is the one big directory that
   must *not* be pruned: dropping it makes every cold start re-compile pandas,
   scipy, numba and sklearn onto a tmpfs.
4. **Vendored test suites stay too, and that one was learned the hard way.**
   Deleting every `*/tests` directory under `site-packages` saves ~110 MB and
   breaks the service: revision `00008-m7v` failed to start with
   `ModuleNotFoundError: No module named 'numpy._core.tests'`. "Nothing imports
   a package's tests at runtime" sounds obvious and is false for numpy. A
   passing `pytest` run does not catch it — only a started container does.

## Deploy to Cloud Run

One-time setup:

```bash
gcloud auth login
gcloud projects create midas-backtester-<unique-suffix>
gcloud config set project midas-backtester-<unique-suffix>
# Link a billing account in the GCP console first; required even on free tier.
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
gcloud config set run/region europe-west1
```

**One-time, and required before the next deploy** — `cloudbuild.yaml` now
pushes to a `europe-west1` Artifact Registry repository instead of `gcr.io`.
The `gcr.io` host resolves to an auto-migrated repository whose location is
`us`, so every cold start was pulling the image from the United States into a
service running in Belgium. Create the same-region repository first, or the
push step will fail:

```bash
gcloud artifacts repositories create backtester \
  --repository-format=docker --location=europe-west1 \
  --description="Midas backtester runtime images"
```

Once a `europe-west1` revision is serving, the old US repository (2.7 GB of
superseded revisions) can be deleted:

```bash
gcloud artifacts repositories delete gcr.io --location=us   # AFTER the cutover
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
   `COPY engine`, `COPY data/market/ohlcv`, etc. resolve).
2. Pushes the image to
   `europe-west1-docker.pkg.dev/$PROJECT_ID/backtester/midas-backtester`.
3. Deploys it to Cloud Run reachable over IAM (`--allow-unauthenticated`) —
   required so the Netlify proxy, which is not a GCP principal, can reach it
   without a long-lived service-account key — with `BACKTESTER_SECRET`
   injected from Secret Manager (`backtester-secret`) as the real lock:
   `/run` and `/catalog` require a matching `X-Backtester-Secret` header,
   alongside sensible resource defaults (1 GiB / 1 vCPU / 5 min timeout /
   scale-to-zero, `--max-instances=3` capping abuse). `/health` needs no
   secret and is reachable directly.

Cloud Build prints the deployed service URL near the end of the output —
something like `https://midas-backtester-xxxxxx-ew.a.run.app`. Save it; the
site needs it on Vercel as `PUBLIC_BACKTESTER_URL`.

`/run` and `/catalog` answer only requests carrying `X-Backtester-Secret:
$BACKTESTER_SECRET`; the `william.revah.paris` Netlify proxy is the sole
holder of that secret.

## Smoke-test the deployed service

```bash
curl -sf $SERVICE_URL/health
```

Note: the endpoint is `/health`, not `/healthz` — Cloud Run's edge reserves
the `/healthz` path and never forwards it to the container. Should return
`{"status":"ok"}`. No secret header needed — the service is IAM-reachable
and `/health` is unauthenticated at the app layer too.

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
