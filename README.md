# ZeroPath – LLM-Powered Python Security Scanner

An end-to-end web application for AppSec engineers to scan public Python GitHub repositories for security vulnerabilities using LLM-powered two-stage analysis, triage findings, and compare scan results over time.

## Architecture Overview

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Frontend   │────▶│  Backend API │────▶│   Pub/Sub    │
│  React/Vite  │     │   FastAPI    │     │ (scan-jobs)  │
│  + Clerk     │     │ + Postgres   │     └──────┬──────┘
└─────────────┘     └─────────────┘            │ push
                                                ▼
                                         ┌─────────────┐
                                         │   Worker     │
                                         │  Scanner     │
                                         │  + OpenAI    │
                                         └─────────────┘
```

### Services

| Service | Directory | Port | Description |
|---------|-----------|------|-------------|
| Frontend | `frontend/` | 5173 | React + Vite + TypeScript with Clerk auth |
| Backend API | `backend/` | 8000 | FastAPI REST API, SQLAlchemy + Alembic |
| Scanner Worker | `worker/` | 8001 | Pub/Sub push consumer, LLM scanner pipeline |

### Key Design Decisions

**Three-service architecture.** The API and worker are separate Cloud Run services. The API handles user requests and enqueues scan jobs via Pub/Sub. The worker receives push messages and runs scans asynchronously. This separation means long-running scans don't block API requests, and each service can scale independently.

**Pub/Sub push delivery.** Rather than having the worker poll for messages, Google Cloud Pub/Sub pushes scan job messages directly to the worker's `/scan` endpoint. This eliminates the need for the worker to maintain a persistent connection to Pub/Sub, and Cloud Run's autoscaling handles concurrency naturally.

**Postgres as the shared data store.** Both the API and worker connect to the same Cloud SQL Postgres instance. The worker writes scan results directly rather than reporting back through the API. This simplifies the architecture and avoids a secondary communication channel, at the cost of tighter coupling to the database schema.

**GitHub-only public repos for v1.** Limiting to public GitHub HTTPS URLs eliminates the need for credential storage, SSH key management, and OAuth token flows. The scanner uses `git clone` with `--depth 1` for speed. Supporting private repos or other hosts would require a credential management subsystem.

---

## Scanner Design

### Two-Stage LLM Analysis

The scanner uses a two-stage pipeline to balance cost, speed, and accuracy:

**Stage 1 – Suspicion Classifier.** Each `.py` file gets a lightweight classification prompt asking whether the file is security-relevant. Files clearly devoid of security logic (constants, type stubs, empty `__init__.py`) are classified `not_suspicious` and skipped. The classifier is tuned for **high recall** — when in doubt, it marks files suspicious. This stage reduces the volume of expensive deep analysis calls.

**Stage 2 – Deep Security Analysis.** Suspicious files go through a thorough security review prompt. The model identifies concrete, exploitable vulnerabilities with structured output: vulnerability type (freeform), severity (`low | medium | high | critical`), line number, description, explanation, and code snippet.

Both stages run through the same `call_llm_json()` client that enforces JSON output and handles retry/repair.

### Prompt Design

Prompts are in `worker/app/scanner/prompts.py`. Key design choices:

- **Role framing:** The model is cast as an "expert Python AppSec reviewer." This biases output toward security-specific analysis rather than general code review.
- **Negative constraints:** The Stage 2 prompt explicitly says *"not stylistic issues, not best-practice suggestions, and not theoretical risks without a realistic attack vector."* This reduces noise from low-signal findings.
- **Freeform vulnerability types:** Rather than constraining the model to a fixed taxonomy (CWE IDs, OWASP categories), vulnerability types are freeform strings like "SQL Injection", "Command Injection", "Insecure Deserialization". This lets the model use natural language and avoids misclassification into rigid categories.
- **Schema-first output:** Both prompts demand JSON-only output matching an exact schema. The system prompt ends with the schema specification and a no-prose instruction.
- **Recall over precision in Stage 1:** The classifier prompt says *"When in doubt, classify as suspicious."* False negatives at this stage are unrecoverable — they cause findings to be missed entirely.

### Output Parsing and Malformed Output Handling

The LLM client (`worker/app/scanner/llm_client.py`) handles parsing:

1. **JSON extraction:** Strips markdown code fences if present, then parses with `json.loads()`.
2. **Retry with repair:** If parsing fails, the original response is fed back to the model with a repair prompt describing the parse error. The model gets up to `max_file_retries` (default: 2) additional attempts.
3. **Graceful degradation:** If all attempts fail, the file is marked `failed` with an error message. The scan continues — a single file failure does not fail the entire scan.
4. **Validation:** Stage 2 output is validated field-by-field. Invalid severities default to `medium`. Invalid line numbers default to `1`. Missing optional fields get sensible defaults. This prevents a single malformed finding from discarding the entire file's results.

### Token / Context Window Strategy

The scanner is designed to work within model context limits:

- **File-by-file processing.** Each file is analyzed independently. The model sees only one file at a time plus the system prompt. This keeps prompts well under context limits for typical source files.
- **Truncation at 100K characters.** Files exceeding `MAX_FILE_CHARS` (100,000 characters, roughly 25K tokens) are truncated with a `# ... [truncated for analysis]` marker. This is a hard safety valve — most Python source files are well under this limit.
- **No cross-file context.** The scanner does not attempt to trace data flows across files or modules. Each file is evaluated in isolation. This is a significant limitation (see [Known Limitations](#known-limitations)) but keeps the context window manageable and the architecture simple.
- **Shallow clone with depth=1.** Only the latest commit is cloned, minimizing disk usage and clone time.

### Handling Large Repositories and Partial Coverage

- **Default exclusions.** The scanner skips `tests/`, `.venv/`, `venv/`, `site-packages/`, `build/`, `dist/`, `__pycache__/`, and `.git/`. These directories typically contain test code, dependencies, or build artifacts that add noise without security value.
- **Two-stage filtering.** Stage 1 acts as a fast filter. In a large repo with hundreds of `.py` files, only the security-relevant subset proceeds to the expensive Stage 2 analysis.
- **Concurrent processing.** File analysis is parallelized with a configurable concurrency limit (`max_concurrent_files`, default: 5). Files are processed via `asyncio` with a semaphore to bound concurrent OpenAI API calls.
- **Partial results.** If some files fail (parsing errors, API failures), the scan still completes with results from successful files. The scan status is `complete` (not `failed`) with individual file failures visible in scan details.
- **File-level failure tracking.** The `scan_files` table records per-file status (`complete`, `failed`, `skipped`), stage 1 result, whether stage 2 was attempted, and error messages. This gives visibility into coverage gaps.

---

## Finding Identity and Cross-Scan Comparison

### Fingerprint Strategy

Each finding gets a stable fingerprint derived from `sha256(lower(file_path) + "::" + lower(vulnerability_type))`. This fingerprint:

- **Survives line-number drift.** If a vulnerability moves from line 42 to line 47 between commits, it retains the same identity because the fingerprint doesn't include line numbers.
- **Normalizes LLM output variance.** Case-insensitive matching means "SQL Injection" and "sql injection" produce the same fingerprint.
- **Distinguishes vulnerability types in the same file.** A file with both a SQL injection and a path traversal gets two different fingerprints.

**Limitation:** This fingerprint strategy is optimized for stable repositories between scans. It doesn't survive file renames, and it conflates multiple instances of the same vulnerability type in the same file into one identity. A production system would incorporate code-span hashing or AST-level anchoring for more robust identity.

### Deduplication

Within a single scan, if two findings produce the same fingerprint (e.g., the model reports the same SQL injection twice), only the first is kept. This happens during the persistence phase in `finding_persistence.py`.

### Cross-Scan Comparison

The comparison endpoint (`GET /api/repositories/{id}/compare?base_scan_id=...&target_scan_id=...`) computes:

- **New findings:** present in the target scan but not the base scan (by `finding_identity_id`)
- **Fixed findings:** present in the base scan but not the target scan
- **Persisting findings:** present in both scans

The comparison is computed dynamically from `finding_occurrence` records — there's no precomputed comparison table.

### Triage Model

Triage is per scan occurrence. Each new scan starts fresh with all findings in `open` status. Triage state does **not** carry across scans in v1. This is intentional — carrying triage forward requires confidence in finding identity that the current fingerprint strategy doesn't guarantee. Users can compare scans to see that a previously-triaged finding persists, but they need to re-triage it.

---

## Queue and Job Processing Design

### Scan Lifecycle

1. User triggers a scan via the frontend → `POST /api/repositories/{id}/scans`
2. Backend creates a `scan` record with status `queued` and publishes `{"scan_id": "<uuid>"}` to the `scan-jobs` Pub/Sub topic
3. Pub/Sub pushes the message to the worker's `/scan` endpoint
4. Worker decodes the Pub/Sub envelope, deduplicates against in-flight scans, and dispatches to a thread pool
5. Worker transitions scan to `running`, clones the repo, discovers files, runs the LLM pipeline, persists findings, and transitions to `complete` (or `failed`)
6. Frontend polls `GET /api/scans/{id}` to detect status changes

### Concurrency

- **Multiple concurrent scans** are supported. The worker uses a `ThreadPoolExecutor` (4 workers) and tracks in-flight scan IDs to reject duplicate Pub/Sub deliveries.
- **File-level parallelism** within a scan is handled by `asyncio.Semaphore` with a configurable concurrency limit.
- **Cloud Run autoscaling** can spin up multiple worker instances for burst load, though each instance manages its own in-flight dedup set.

### Failure Handling

| Failure | Behavior |
|---------|----------|
| Clone fails | Scan marked `failed` with error message |
| Individual file can't be read | File marked `failed`, scan continues |
| Stage 1 parse failure after retries | File marked `failed`, scan continues |
| Stage 2 parse failure after retries | File marked `failed`, scan continues |
| Worker crashes mid-scan | Scan stays in `running` — requires manual intervention or a reaper job |
| Pub/Sub redelivery | Deduplicated by in-flight tracking; acknowledged immediately |

---

## PyGoat Benchmark Analysis

[PyGoat](https://github.com/adeyosemanputra/pygoat) is an intentionally vulnerable Python web application covering OWASP Top 10 categories. The scanner is designed to identify its major intentional vulnerabilities.

### Expected Detection Capabilities

| Vulnerability (PyGoat) | Expected Detection | Scanner Behavior |
|---|---|---|
| **SQL Injection** (raw SQL with string formatting in views) | ✅ High confidence | Stage 1 flags view files as suspicious; Stage 2 identifies `cursor.execute()` with unsanitized input |
| **Command Injection** (`os.system()` / `subprocess` with user input) | ✅ High confidence | Clear pattern of user-controlled data flowing into shell commands |
| **XSS** (unescaped user input rendered in templates) | ⚠️ Partial | Detectable when the vulnerability is in Python code (e.g., `mark_safe()`); limited for template-only XSS since `.html` files aren't scanned |
| **Insecure Deserialization** (`pickle.loads()` on user data) | ✅ High confidence | Well-known dangerous pattern the model reliably flags |
| **SSRF** (user-controlled URLs passed to `requests.get()`) | ✅ High confidence | Direct pattern match in Python code |
| **Broken Authentication** (weak password checks, hardcoded secrets) | ✅ Moderate confidence | Model identifies missing/weak auth checks and hardcoded credentials |
| **Security Misconfiguration** (`DEBUG=True`, permissive CORS) | ⚠️ Moderate | Detected in settings files if flagged suspicious in Stage 1 |
| **XXE** (XML parsing without disabling external entities) | ✅ Moderate confidence | Standard `lxml`/`xml.etree` patterns are well-known to the model |
| **Path Traversal** (user-controlled file paths) | ✅ High confidence | Clear exploitable pattern |
| **Cryptographic failures** (weak hashing, hardcoded keys) | ✅ Moderate confidence | Model flags `md5`, `sha1` for passwords, hardcoded secrets |

### Known Gaps Against PyGoat

- **Template-level vulnerabilities** (XSS in `.html` files) are not detected because the scanner only processes `.py` files in v1.
- **Cross-file data flow** is not traced. If user input enters in `views.py` and is used unsafely in a utility function imported from another module, the scanner may miss the vulnerability or report it with incomplete context.
- **Django-specific patterns** like `@csrf_exempt` or misconfigured middleware may be missed if the model doesn't associate them with security risk in isolation.

### Quality Assessment

The scanner should reliably catch the **majority of PyGoat's intentional vulnerabilities** that manifest in Python source code, particularly SQL injection, command injection, insecure deserialization, SSRF, and path traversal. These are well-understood vulnerability classes where the exploitable pattern is visible within a single file. The primary detection gaps are in template-based XSS and cross-module data flow analysis.

---

## Data Model

The database uses PostgreSQL with SQLAlchemy 2.x and Alembic migrations:

| Table | Purpose |
|-------|---------|
| `users` | Clerk-mapped user accounts |
| `repositories` | Registered GitHub repositories |
| `scans` | Scan job records with status lifecycle |
| `scan_files` | Per-file processing results (stage 1/2 status, errors) |
| `finding_identities` | Canonical issue identities for dedup/comparison (fingerprint-based) |
| `finding_occurrences` | Concrete findings with severity, line number, description, snippet |
| `finding_triage` | Per-occurrence triage state (`open`, `false_positive`, `resolved`) |

---

## API Endpoints

### Repositories
- `POST /api/repositories` – Register a GitHub repository
- `GET /api/repositories` – List user's repositories
- `GET /api/repositories/{id}` – Repository detail

### Scans
- `POST /api/repositories/{id}/scans` – Trigger a scan
- `GET /api/repositories/{id}/scans` – Scan history
- `GET /api/scans/{id}` – Scan status and detail
- `GET /api/scans/{id}/files` – File-level processing results
- `GET /api/scans/{id}/findings` – Finding occurrences

### Comparison
- `GET /api/repositories/{id}/compare?base_scan_id=...&target_scan_id=...` – Diff two scans

### Triage
- `PATCH /api/finding-occurrences/{id}/triage` – Update triage status and notes

All protected routes require Clerk authentication.

---

## Frontend

Built with React, Vite, and TypeScript. Key screens:

- **Auth:** Clerk-managed signup and login
- **Repository List:** View registered repos, add new ones
- **Repository Detail:** Scan history, trigger new scans
- **Scan Detail:** Status, commit SHA, findings list with severity badges, triage controls, file-level failures
- **Scan Comparison:** Side-by-side diff showing new, fixed, and persisting findings

The frontend polls scan status and uses the backend REST API exclusively.

---

## Deployment

### Google Cloud Infrastructure (Terraform)

Terraform modules in `infra/` provision:

- **Cloud Run** services for frontend, API, and worker
- **Cloud SQL** Postgres instance with VPC private networking
- **Pub/Sub** topic and push subscription
- **Secret Manager** for database password, Clerk key, and OpenAI key
- **Artifact Registry** for Docker images
- **IAM** bindings and service accounts
- **VPC Connector** for private Cloud SQL access

See [DEPLOYMENT.md](DEPLOYMENT.md) for step-by-step deployment instructions.

### Local Development

#### Prerequisites

- **Python 3.12+** (with `python3` and `pip`)
- **Node.js 20+** (with `npm`)
- **PostgreSQL 14+** (running locally)
- **Git** (for cloning scanned repositories)
- A [Clerk](https://clerk.com) account (free tier works) for authentication
- An [OpenAI API key](https://platform.openai.com) for the LLM scanner

#### 1. Install Dependencies

```bash
make install
```

This creates Python virtual environments in `backend/.venv` and `worker/.venv`, installs Python packages, and runs `npm install` for the frontend.

#### 2. Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env` with your real values:

| Variable | Where to get it |
|----------|----------------|
| `DATABASE_URL` | Your local Postgres connection string, e.g. `postgresql://<your-username>@localhost:5432/zeropath` (Homebrew Postgres typically uses your OS username with no password) |
| `CLERK_SECRET_KEY` | Clerk dashboard → API Keys |
| `CLERK_PUBLISHABLE_KEY` | Clerk dashboard → API Keys |
| `CLERK_JWKS_URL` | `https://<your-clerk-domain>/.well-known/jwks.json` (visible in Clerk dashboard) |
| `VITE_CLERK_PUBLISHABLE_KEY` | Same as `CLERK_PUBLISHABLE_KEY` |
| `VITE_API_BASE_URL` | `http://localhost:8000` |
| `OPENAI_API_KEY` | OpenAI platform → API Keys |
| `OPENAI_MODEL` | `gpt-4o` (or another model) |
| `GCP_PROJECT_ID` | Only needed for production Pub/Sub; can leave as-is for local dev |
| `ENVIRONMENT` | Set to `development` — this makes the backend call the worker directly instead of going through Pub/Sub |

> **Note:** The `.env` file is shared across all three services via symlinks. If symlinks aren't present, create them:
> ```bash
> ln -sf ../.env backend/.env
> ln -sf ../.env worker/.env
> ln -sf ../.env frontend/.env
> ```

#### 3. Create the Database and Run Migrations

```bash
createdb zeropath
make db-migrate
```

This applies Alembic migrations and creates all required tables (`users`, `repositories`, `scans`, `scan_files`, `finding_identities`, `finding_occurrences`, `finding_triage`).

#### 4. Start All Services

The easiest way is to run each service in a **tmux session** so they persist in the background:

```bash
# Start backend API on :8000
tmux new-session -d -s zp-backend -c backend \
  "source .venv/bin/activate && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"

# Start scanner worker on :8001
tmux new-session -d -s zp-worker -c worker \
  "source .venv/bin/activate && uvicorn app.main:app --reload --host 0.0.0.0 --port 8001"

# Start frontend on :5173
tmux new-session -d -s zp-frontend -c frontend \
  "npm run dev"
```

Or run them in separate terminals:

```bash
make dev-backend     # Terminal 1 – API on :8000
make dev-worker      # Terminal 2 – Worker on :8001
make dev-frontend    # Terminal 3 – Frontend on :5173
```

#### 5. Verify Everything Is Running

```bash
curl http://localhost:8000/    # → {"service":"zeropath-api","status":"ok"}
curl http://localhost:8001/    # → {"service":"zeropath-worker","status":"ok"}
open http://localhost:5173     # → Frontend loads in browser
```

API docs (Swagger UI) are available at http://localhost:8000/docs.

#### 6. Using the App

1. Open http://localhost:5173 and sign up / sign in via Clerk
2. **Add a repository** — paste any public GitHub HTTPS URL (e.g. `https://github.com/adeyosemanputra/pygoat`)
3. **Trigger a scan** — the backend dispatches it directly to the worker in development mode
4. **Watch progress** — the frontend polls for status updates as the worker clones the repo and runs LLM analysis
5. **Review findings** — see vulnerabilities with severity, line numbers, code snippets, and descriptions
6. **Triage** — mark findings as `open`, `false_positive`, or `resolved`
7. **Compare scans** — run a second scan and compare to see new, fixed, and persisting findings

#### Managing tmux Sessions

```bash
tmux ls                          # List sessions
tmux attach -t zp-backend       # View backend logs (Ctrl+B, D to detach)
tmux attach -t zp-worker        # View worker logs
tmux attach -t zp-frontend      # View frontend logs
tmux kill-session -t zp-backend  # Stop a service
```

#### Troubleshooting

| Issue | Fix |
|-------|-----|
| `role "postgres" does not exist` | Homebrew Postgres uses your OS username. Set `DATABASE_URL=postgresql://<your-username>@localhost:5432/zeropath` |
| `Extra inputs are not permitted` from pydantic | The shared `.env` has variables not in every service's config. Ensure `"extra": "ignore"` is in `model_config` in both `backend/app/config.py` and `worker/app/config.py` |
| Scans stay in `queued` status | Check that `ENVIRONMENT=development` is set in `.env` (enables direct worker dispatch) and that the worker is running on port 8001 |
| Frontend can't reach backend | Ensure `VITE_API_BASE_URL=http://localhost:8000` is set and the backend is running |

---

## What Was Not Built (and Why)

| Omission | Rationale |
|----------|-----------|
| Private repository support | Requires credential storage, OAuth flows, and security considerations that add significant complexity without changing the core scanner architecture |
| Non-GitHub hosts | Same scanning pipeline would work; URL validation and clone logic would need extension |
| Cross-file data flow analysis | Would require building an AST-level call graph — a project in itself. Per-file analysis covers the majority of single-file vulnerability patterns. |
| Template scanning (`.html`, `.jinja2`) | Would catch more XSS but requires a different analysis approach; `.py`-only keeps scope manageable |
| Triage carryover across scans | Requires higher confidence in finding identity than the current fingerprint provides. False carryover is worse than no carryover. |
| Advanced pagination / filtering | The API returns full result sets. Workable for v1 scale but would need pagination for production use. |
| Notification integrations | Slack/email alerts for new findings are valuable but orthogonal to the core scanning platform |
| Remediation code generation | The model could suggest fixes, but presenting them responsibly requires more UX work than v1 scope allows |

---

## What I'd Build Next

**Given another week, in priority order:**

1. **Cross-file data flow analysis.** Use AST parsing to build a lightweight call graph per repository, then pass calling context to Stage 2 so the model can trace tainted data across module boundaries. This would significantly improve recall for injection vulnerabilities.

2. **Triage carryover.** With better finding identity (code-span fingerprinting using AST node hashes), triage state could carry forward across scans. This is the most-requested feature in any triage workflow.

3. **Template / configuration scanning.** Extend the scanner to `.html`, `.yaml`, `.json`, and `.toml` files. Django/Flask templates are a major source of XSS that the current Python-only scope misses.

4. **Incremental scanning.** Only re-scan files that changed between commits. The `commit_sha` is already stored per scan — `git diff` could identify changed files and skip unchanged ones.

5. **Webhook-triggered scans.** GitHub webhooks on push events to automatically scan on every commit. Removes the manual "trigger scan" step.

6. **Finding grouping and prioritization.** Group related findings by vulnerability class or affected component. Rank by exploitability using model confidence and code context.

7. **SARIF export.** Output findings in SARIF format for integration with GitHub Code Scanning, IDE plugins, and other security tooling.

---

## Known Limitations

- **No cross-file analysis.** Vulnerabilities that span multiple files (e.g., input validated in one module, used unsafely in another) may be missed or reported with incomplete context.
- **Python only.** The scanner doesn't analyze templates, configuration files, JavaScript, SQL files, or other non-`.py` artifacts.
- **LLM non-determinism.** Running the same scan twice may produce slightly different findings due to model temperature (set to 0.2 but not zero). Fingerprinting normalizes most of this.
- **No rate limiting on OpenAI API.** Large repositories with many suspicious files could hit API rate limits. The concurrency semaphore provides some natural throttling.
- **Stale scan recovery.** If the worker crashes mid-scan, the scan stays in `running` status indefinitely. A production system needs a reaper job or timeout-based recovery.
- **Single-user model.** No multi-tenant isolation beyond Clerk user IDs. No RBAC or team features.
- **Finding identity is coarse.** The `file_path + vulnerability_type` fingerprint conflates multiple instances of the same vulnerability type within a single file.
- **No test exclusion override.** The `tests/` directory is always excluded. Some teams may want to scan test code for hardcoded credentials or insecure test fixtures.

---

## Project Structure

```
zeropath/
├── frontend/              # React + Vite + TypeScript
│   └── src/
│       ├── pages/         # RepositoriesPage, ScanDetailPage, ComparisonPage
│       ├── components/    # FindingCard, SeverityBadge, StatusBadge, Layout
│       └── api.ts         # Backend REST client
├── backend/               # FastAPI API service
│   ├── app/
│   │   ├── routers/       # repositories, scans, findings, comparison
│   │   ├── models/        # SQLAlchemy ORM models
│   │   ├── schemas/       # Pydantic request/response schemas
│   │   ├── services/      # Business logic
│   │   ├── auth.py        # Clerk JWT verification
│   │   └── database.py    # Session management
│   └── alembic/           # Database migrations
├── worker/                # Scanner worker service
│   └── app/
│       ├── scanner/       # Two-stage LLM pipeline
│       │   ├── prompts.py # System prompts and JSON schemas
│       │   ├── pipeline.py # Stage 1/2 execution with concurrency
│       │   └── llm_client.py # OpenAI client with retry/repair
│       └── services/      # Scan lifecycle, fingerprinting, persistence
├── infra/                 # Terraform (Cloud Run, SQL, Pub/Sub, IAM)
├── DEPLOYMENT.md          # Cloud deployment guide
├── PRD.md                 # Product requirements document
├── Makefile               # Dev commands
└── .env.example           # Environment variable template
```
