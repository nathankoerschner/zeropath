# Deployment Guide

This document describes how to deploy Zeropath to Google Cloud.

## Prerequisites

- [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) (`gcloud`)
- [Terraform](https://developer.hashicorp.com/terraform/downloads) >= 1.5
- [Docker](https://docs.docker.com/get-docker/)
- A GCP project with billing enabled
- Clerk account with API keys
- OpenAI API key

## 1. Initial GCP Setup

```bash
# Authenticate
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

# Enable Docker auth for Artifact Registry
gcloud auth configure-docker us-central1-docker.pkg.dev
```

## 2. Provision Infrastructure

```bash
# Copy and fill in the variable values
cp infra/terraform.tfvars.example infra/terraform.tfvars
# Edit infra/terraform.tfvars with your values

# Initialize and apply Terraform
make tf-init
make tf-plan   # Review the plan
make tf-apply  # Apply (requires confirmation)
```

This creates:
- **Cloud SQL** Postgres instance with private networking
- **Pub/Sub** topic (`scan-jobs`) and push subscription to the worker
- **Cloud Run** services for API, worker, and frontend
- **Secret Manager** secrets for DB password, Clerk key, and OpenAI key
- **VPC** with connector for private Cloud SQL access
- **Artifact Registry** repository for Docker images
- **IAM** bindings for service accounts

## 3. Build and Push Docker Images

```bash
export GCP_PROJECT=your-project-id

# Build all images
make docker-build

# Push to Artifact Registry
make docker-push
```

To build with a specific tag:

```bash
make docker-build TAG=v1.0.0
make docker-push TAG=v1.0.0
```

## 4. Run Database Migrations

After the first deployment, run Alembic migrations. The backend container runs
migrations on startup automatically (`alembic upgrade head`).

For manual migration via Cloud SQL proxy:

```bash
# Install and start the proxy
cloud-sql-proxy YOUR_INSTANCE_CONNECTION_NAME

# In another terminal
cd backend && . .venv/bin/activate
DATABASE_URL="postgresql://zeropath:PASSWORD@localhost:5432/zeropath" alembic upgrade head
```

## 5. Configure Clerk

1. In Clerk dashboard, set the allowed origins to include your frontend Cloud Run URL
2. Set the redirect URLs for sign-in/sign-up flows
3. Ensure the JWKS URL in `terraform.tfvars` matches your Clerk instance

## 6. Frontend Environment

The frontend build needs the Clerk publishable key and API URL. Set these as
build args or environment variables before building:

```bash
# In frontend/.env.production
VITE_CLERK_PUBLISHABLE_KEY=pk_live_...
VITE_API_URL=https://zeropath-api-HASH-uc.a.run.app
```

Then rebuild and redeploy the frontend image.

## 7. Verify Deployment

After deployment, check service health:

```bash
# Get service URLs
cd infra && terraform output

# Test API health
curl https://YOUR_BACKEND_URL/health

# Check Cloud Run logs
gcloud run services logs read zeropath-api --region us-central1
gcloud run services logs read zeropath-worker --region us-central1
```

## Architecture Overview

```
                    ┌──────────────┐
                    │   Frontend   │
                    │  (Cloud Run) │
                    └──────┬───────┘
                           │ REST
                    ┌──────▼───────┐        ┌─────────────┐
                    │   Backend    │───────► │  Cloud SQL  │
                    │  (Cloud Run) │        │  (Postgres)  │
                    └──────┬───────┘        └──────▲───────┘
                           │ publish               │
                    ┌──────▼───────┐               │
                    │   Pub/Sub    │               │
                    │  scan-jobs   │               │
                    └──────┬───────┘               │
                           │ push                  │
                    ┌──────▼───────┐               │
                    │   Worker     │───────────────┘
                    │  (Cloud Run) │
                    └──────────────┘
```

## Environment Variables

### Backend (API)
| Variable | Description |
|---|---|
| `DATABASE_URL` | Postgres connection string (from Secret Manager) |
| `CLERK_SECRET_KEY` | Clerk backend key (from Secret Manager) |
| `CLERK_PUBLISHABLE_KEY` | Clerk frontend key |
| `CLERK_JWKS_URL` | Clerk JWKS endpoint |
| `GCP_PROJECT_ID` | GCP project for Pub/Sub |
| `PUBSUB_TOPIC_ID` | Pub/Sub topic name |
| `ENVIRONMENT` | `production` / `staging` |

### Worker
| Variable | Description |
|---|---|
| `DATABASE_URL` | Postgres connection string (from Secret Manager) |
| `OPENAI_API_KEY` | OpenAI key (from Secret Manager) |
| `ENVIRONMENT` | `production` / `staging` |

## Updating Services

To deploy a new version manually:

```bash
# Build and push with new tag
make docker-build TAG=v1.1.0
make docker-push TAG=v1.1.0

# Update terraform.tfvars with new image tags
# Then apply
make tf-apply
```

## GitHub Actions push-to-deploy

A workflow is included at `.github/workflows/deploy.yml`.

It deploys on:

- push to `master`
- manual workflow dispatch

The workflow:

1. Authenticates to GCP
2. Builds backend and worker images with Cloud Build
3. Builds the frontend image with Cloud Build using the deployed backend URL and Clerk publishable key
4. Runs `terraform apply` with image tags and secret values passed through `TF_VAR_*`

### Required GitHub repository secrets

- `GCP_PROJECT_ID`
- `GCP_REGION`
- `GCP_SA_KEY`
- `CLERK_SECRET_KEY`
- `CLERK_PUBLISHABLE_KEY`
- `CLERK_JWKS_URL`
- `OPENAI_API_KEY`

### Notes

- `GCP_SA_KEY` should be a JSON service account key with permissions for Cloud Build, Cloud Run, Artifact Registry, Secret Manager, Pub/Sub, and Terraform-managed resources.
- A more secure follow-up is to replace `GCP_SA_KEY` with GitHub OIDC + Workload Identity Federation.
- Push-to-deploy will only work once this repo is actually hosted on GitHub and those secrets are configured there.

## Cost Considerations

With the default configuration:
- **Cloud Run**: scales to zero when idle (no cost at rest)
- **Cloud SQL**: `db-f1-micro` is the smallest tier (~$7/month)
- **Pub/Sub**: minimal cost at low message volumes
- **Secret Manager**: negligible cost for a few secrets

For development, the Cloud SQL instance is the primary fixed cost.
