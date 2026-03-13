# GCP Setup Summary

This app is deployed in GCP project `koerschner-zeropath`.

## Live URLs

- Frontend: `https://zeropath-frontend-pevinolx4q-uc.a.run.app`
- Backend: `https://zeropath-api-pevinolx4q-uc.a.run.app`
- Worker: `https://zeropath-worker-pevinolx4q-uc.a.run.app`

## What was provisioned

Using Terraform in `infra/`, the following are set up:

- **Cloud Run**
  - `zeropath-frontend`
  - `zeropath-api`
  - `zeropath-worker`
- **Cloud SQL Postgres**
  - instance: `zeropath-production`
  - database: `zeropath`
- **Pub/Sub**
  - topic: `scan-jobs`
  - push subscription: `scan-jobs-worker-push`
- **Artifact Registry**
  - repo: `us-central1-docker.pkg.dev/koerschner-zeropath/zeropath`
- **Secret Manager**
  - Clerk secret key
  - OpenAI API key
  - DB password
  - DB URL
- **IAM / service accounts**
  - backend service account
  - worker service account
  - Pub/Sub invoker service account

## Important deployment decisions

### 1. Cloud Build instead of local Docker
Local Docker was unreliable on this machine, so images were built with **Cloud Build** instead.

Backend and worker were built with:

- `gcloud builds submit backend --tag ...`
- `gcloud builds submit worker --tag ...`

Frontend was built with a custom Cloud Build config:

- `cloudbuild.frontend.yaml`

### 2. Frontend build-time envs
The frontend is a Vite app, so it needs values at **build time**, not runtime.

To support Cloud Build, `frontend/Dockerfile` was updated to accept:

- `VITE_CLERK_PUBLISHABLE_KEY`
- `VITE_API_BASE_URL`

These are passed as Docker build args in `cloudbuild.frontend.yaml`.

### 3. Cloud SQL connection method
The original Terraform used a **Serverless VPC Access connector** for Cloud Run → Cloud SQL.
That connector failed to become healthy in GCP.

The deployment was switched to the more standard Cloud Run Cloud SQL integration:

- backend/worker mount `/cloudsql`
- `DATABASE_URL` uses socket style:
  - `postgresql://USER:PASSWORD@/zeropath?host=/cloudsql/INSTANCE_CONNECTION_NAME`
- backend/worker service accounts were granted:
  - `roles/cloudsql.client`

The failed VPC connector was deleted.

## Files changed

### Added
- `GCP_SETUP.md`
- `cloudbuild.frontend.yaml`

### Updated
- `frontend/Dockerfile`
- `infra/main.tf`
- `infra/database.tf`
- `infra/services.tf`
- `infra/terraform.tfvars`

## Terraform changes made

### `infra/main.tf`
Enabled missing APIs:

- `compute.googleapis.com`
- `vpcaccess.googleapis.com`
- `servicenetworking.googleapis.com`

### `infra/services.tf`
- Increased frontend Cloud Run memory from `256Mi` to `512Mi`
- Added Cloud SQL volume mounts to backend/worker
- Added `cloudsql.client` IAM role bindings
- Changed generated `DATABASE_URL` secret to use `/cloudsql/...`

### `infra/database.tf`
- Enabled Cloud SQL public IP
- Kept private networking in place
- Removed the Serverless VPC connector resource

## Current deploy flow

Current deploys are **manual**, not triggered by git push.

Typical flow:

1. Build backend image with Cloud Build
2. Build worker image with Cloud Build
3. Build frontend image with Cloud Build, passing:
   - Clerk publishable key
   - deployed backend URL
4. Update image tags in `infra/terraform.tfvars`
5. Run `terraform apply`

## Auto-deploy status

A GitHub Actions workflow has been added at:

- `.github/workflows/deploy.yml`

It is configured to deploy on:

- push to `master`
- manual `workflow_dispatch`

### What still needs to be done

This repo currently has **no git remote configured in the local checkout**, so push-to-deploy is not active until the repo is connected to GitHub and the workflow can run there.

In GitHub, add these repository secrets:

- `GCP_PROJECT_ID`
- `GCP_REGION`
- `GCP_SA_KEY`
- `CLERK_SECRET_KEY`
- `CLERK_PUBLISHABLE_KEY`
- `CLERK_JWKS_URL`
- `OPENAI_API_KEY`

After that, a push to `master` will build all three images with Cloud Build and run `terraform apply`.

## Clerk setup still required

If frontend auth is failing, Clerk likely needs the deployed URL added.

Add this in Clerk:

- Allowed origins:
  - `https://zeropath-frontend-pevinolx4q-uc.a.run.app`
- Redirect / callback URLs:
  - `https://zeropath-frontend-pevinolx4q-uc.a.run.app`

## Notes / cleanup recommendations

- `infra/terraform.tfvars` currently contains real secret values; move those to a safer workflow later
- Terraform state is local; consider moving it to a GCS backend
- Set up auto-deploy if you want push-based releases
- Consider adding a custom domain for frontend/backend

## Verification performed

- Backend health check passed:
  - `GET /health` returned healthy
- Frontend Cloud Run service is serving HTML
- Frontend was rebuilt to point at the deployed backend URL
