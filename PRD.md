# Product Requirements Document (PRD)

## Project
End-to-End LLM-Powered Python Security Scanner Platform

## Goal
Build a working web application for AppSec engineers to:
- submit public Python GitHub repositories for scanning
- analyze Python source code with an LLM for security vulnerabilities
- triage findings
- compare scan results over time
- track remediation across repeated scans

## Product Intent for v1
v1 should prioritize:
- credible architecture
- clean code
- a useful scanner

This is not just a UI demo. The implementation should feel production-shaped, even if some parts are intentionally simplified.

## Target Users
- Application Security (AppSec) engineers

## Core User Problem
AppSec engineers need a centralized platform to scan Python repositories for vulnerabilities, review findings, compare scans, and triage results in a repeatable workflow.

---

## Locked Product and Technical Decisions

### Product
- API style: REST
- Authentication provider: Clerk
- Repository scope: public repositories only
- Repository host scope for v1: GitHub only
- Accepted repository URL format for v1: GitHub HTTPS URLs only
- Repository scan target: default branch only
- Each scan must store the exact commit SHA analyzed
- Vulnerability types: freeform from the model
- Severity scale: `low | medium | high | critical`
- Comparison UX: scan detail diff
- User model: single-user ownership is sufficient
- UI quality bar: minimal but working
- Triage model for v1: triage is per scan occurrence; each new scan starts fresh
- Scanner quality target for v1: should identify the major intentional vulnerabilities in PyGoat

### Frontend
- Language: TypeScript
- Framework: React
- Build tool: Vite

### Backend / Data
- Language: Python
- API framework: FastAPI
- Database: Postgres
- ORM / data access: SQLAlchemy 2.x
- Migrations: Alembic

### Infrastructure
- Deployment target: Google Cloud
- Infrastructure as code: Terraform
- Queue: Google Cloud Pub/Sub
- Worker delivery model: Pub/Sub push to a dedicated worker HTTP endpoint

### Scanner Architecture
- Repository ingestion method: shallow git clone of the default branch
- Scan execution model: asynchronous job record with a separate scanner worker service
- Scan scope for v1: `.py` files only
- Default exclusions for v1:
  - `tests/`
  - `.venv/`
  - `venv/`
  - `site-packages/`
  - `build/`
  - `dist/`
  - `__pycache__/`
  - `.git/`
- LLM output format: strict JSON schema
- LLM malformed-output handling: retry/repair; if a file still fails parsing after retries, that file fails and the scan continues
- Scan strategy: two-stage analysis
  1. first-pass per-file suspicious / not suspicious classification
  2. second-pass deeper analysis on suspicious files to produce findings and uncover deeper issues in those files
- Concurrency: the system should support concurrent scans, and file analysis within a scan may be parallelized subject to infrastructure capacity

---

## In Scope

### 1. Authenticated Web Application
The product must provide an authenticated web dashboard with:
- signup
- login
- authenticated access to repository, scan, finding, and triage views

Authentication must be implemented with Clerk.
The backend must verify the authenticated user on protected API routes.

### 2. Repository Registration and Submission
Users must be able to:
- submit a public GitHub HTTPS repository URL
- register that repository in the application
- trigger a scan for that repository

The system must:
- resolve the repository default branch
- shallow clone the repository for scanning
- persist the exact commit SHA analyzed for each scan
- allow a repository to be scanned multiple times over time

### 3. Python Security Scanning
The scanner must:
- analyze `.py` files only in v1
- exclude irrelevant/generated paths by default
- identify security vulnerabilities using an OpenAI coding-capable model
- support freeform vulnerability classes rather than a fixed taxonomy

The scanner may use preprocessing, chunking, and multi-pass analysis where needed.
The scanner should be designed to work on realistic repositories, not only toy examples.

### 4. Structured Findings
Each finding occurrence must include:
- severity
- vulnerability type
- file path
- line number
- description
- LLM-generated explanation

Where possible, findings should be anchored to code spans or snippets so line references can be checked and findings can be compared across scans.

### 5. Scan Status Tracking
The system must track and expose these scan statuses:
- `queued`
- `running`
- `complete`
- `failed`

Scans must be stored as persistent job records.
The frontend must be able to poll the backend for scan status.

### 6. Triage Workflow
Users must be able to mark finding occurrences as:
- `open`
- `false_positive`
- `resolved`

Users must also be able to attach optional notes.
For v1, triage is per scan occurrence and does not automatically carry across scans.

### 7. Scan History and Comparison
For each repository, the product must provide:
- scan history
- scan detail pages
- diff comparison between scans

Comparison must identify:
- new findings
- fixed findings
- persisting findings

The UI must support comparing any two scans for the same repository.
The default comparison flow may be latest scan vs previous scan.

### 8. Finding Identity and Deduplication
The system must support:
- deduplication within a scan
- stable identity across scans for comparison purposes

The implementation must assign a stable fingerprint to each logical finding so minor wording changes do not necessarily create a new logical issue.
For v1, this identity is used for comparison and deduplication, not for carrying triage state forward.

### 9. API / Frontend Separation
The system must expose a REST API with clean separation from the frontend.
For v1 this means:
- separate frontend and backend application layers
- backend-owned business logic
- frontend interacting only through REST endpoints
- no requirement for separate repositories

### 10. README Deliverable
The repository must include a README covering:
- implementation approach
- architecture overview
- key decisions and tradeoffs
- what would be built next
- known limitations and intentional shortcuts

The README must also specifically describe:
- prompt design
- output parsing approach
- malformed output handling
- token/context window strategy
- handling of large repositories and partial coverage
- finding identity strategy across scans
- queue/job processing design
- why GitHub-only public repo support was chosen for v1

### 11. Deployment and Infrastructure
The project must target Google Cloud and include Terraform for provisioning.
A reasonable v1 deployment architecture is:
- frontend deployed separately
- FastAPI API service on Cloud Run
- scanner worker service on Cloud Run
- Pub/Sub topic + push subscription for scan jobs
- Postgres for persistent storage
- secret/config management for Clerk and OpenAI credentials

---

## Implementation Architecture

### Service Topology
The system consists of three main application components:

1. **Frontend**
   - React + Vite application
   - authenticated with Clerk
   - calls backend REST API
   - polls scan status and fetches findings/diffs

2. **API Service**
   - FastAPI service
   - validates auth
   - manages repositories, scans, findings, triage, and comparisons
   - creates scan job records in Postgres
   - publishes scan messages to Pub/Sub

3. **Scanner Worker Service**
   - separate FastAPI-based or lightweight Python HTTP service
   - receives Pub/Sub push messages on a protected endpoint
   - loads scan job context from Postgres
   - clones repository, performs scan pipeline, stores results
   - updates scan status in Postgres

### Queue Flow
1. User triggers a scan from the UI.
2. Frontend calls backend `POST /repositories/:id/scans`.
3. API creates a scan record with status `queued`.
4. API publishes a Pub/Sub message containing scan metadata, at minimum the scan id.
5. Pub/Sub pushes the message to the worker endpoint.
6. Worker marks scan `running`.
7. Worker executes the scanner pipeline.
8. Worker stores findings, deduped identities, and comparison artifacts.
9. Worker marks scan `complete` or `failed`.
10. Frontend polls scan status and displays results.

### Repo Processing Flow
1. Validate GitHub HTTPS URL format.
2. Resolve repository metadata if needed.
3. Shallow clone default branch.
4. Resolve exact HEAD commit SHA and store it on the scan record.
5. Enumerate candidate `.py` files.
6. Apply exclusion rules.
7. Run first-pass suspicious/not-suspicious analysis.
8. Run second-pass deep analysis on suspicious files.
9. Parse and validate model output.
10. Store findings and deduplicate.
11. Compare against prior scan(s).
12. Finalize scan status.

---

## Scanner Design

### File Selection Rules
Only `.py` files are scanned in v1.
The following directories or paths should be excluded by default when encountered:
- `tests/`
- `.venv/`
- `venv/`
- `site-packages/`
- `build/`
- `dist/`
- `__pycache__/`
- `.git/`

Implementation may also exclude obviously generated or vendored Python code when detected.

### Two-Stage Analysis

#### Stage 1: Suspicion Classifier
Each candidate `.py` file is sent through a lightweight prompt that classifies it as:
- suspicious
- not suspicious

Stage 1 should be optimized for broad recall, not final accuracy.
Its output may include minimal machine-readable rationale, but the required product behavior is only the suspicious/not suspicious decision.

#### Stage 2: Deep Security Analysis
Each suspicious file goes through a deeper analysis prompt that:
- identifies concrete vulnerabilities
- finds additional deeper issues within the file
- assigns severity
- generates structured explanations
- returns strict JSON conforming to the backend schema

### Prompting Expectations
The implementation must use prompt(s) that instruct the model to:
- act as a Python AppSec reviewer
- focus on real exploitable security risks
- avoid low-signal style commentary
- output only schema-compliant JSON
- cite file path and line number for each finding

### Parsing and Validation
The backend must:
- validate model output against a strict schema
- attempt retry or repair when output is malformed
- treat a file as failed if it still cannot be parsed after retries
- continue the scan when individual files fail

A single file failure should not fail the entire scan unless the implementation deliberately applies an additional failure threshold later.

### Token / Context Strategy
The implementation must account for larger repositories and files using practical strategies such as:
- file-by-file processing
- chunking large files if needed
- excluding irrelevant directories
- limiting prompt context to the current file and minimal surrounding metadata

If the scanner cannot fully cover a repository, the UI and stored scan record must reflect partial coverage rather than implying a complete scan.

### Benchmark Quality Target
v1 should be capable of identifying the major intentional vulnerabilities in:
- `https://github.com/adeyosemanputra/pygoat`

This is a quality target, not a guarantee of perfect recall on all Python code.

---

## Data Model

### Core Tables

#### `users`
Represents authenticated users mapped from Clerk.
Suggested fields:
- `id`
- `clerk_user_id` (unique)
- `email`
- `created_at`

#### `repositories`
Represents a registered GitHub repository.
Suggested fields:
- `id`
- `user_id`
- `url`
- `host`
- `owner`
- `name`
- `default_branch`
- `created_at`

#### `scans`
Represents a scan job and result envelope.
Suggested fields:
- `id`
- `repository_id`
- `status` (`queued`, `running`, `complete`, `failed`)
- `commit_sha`
- `started_at`
- `completed_at`
- `error_message` (nullable)
- `created_at`

#### `scan_files`
Tracks file-level processing results for a scan.
Suggested fields:
- `id`
- `scan_id`
- `file_path`
- `stage1_result` (`suspicious`, `not_suspicious`, `failed`)
- `stage2_attempted` (boolean)
- `processing_status` (`complete`, `failed`, `skipped`)
- `error_message` (nullable)

#### `finding_identities`
Canonical issue identities used for cross-scan deduplication and diffing.
Suggested fields:
- `id`
- `repository_id`
- `fingerprint` (unique within repository)
- `canonical_vulnerability_type`
- `canonical_file_path`
- `created_at`

#### `finding_occurrences`
Actual finding instances produced by a specific scan.
Suggested fields:
- `id`
- `scan_id`
- `finding_identity_id`
- `file_path`
- `line_number`
- `severity`
- `vulnerability_type`
- `description`
- `explanation`
- `code_snippet` (nullable)
- `created_at`

#### `finding_triage`
Per-scan triage state for finding occurrences.
Suggested fields:
- `id`
- `finding_occurrence_id`
- `status` (`open`, `false_positive`, `resolved`)
- `note` (nullable)
- `updated_at`

#### `scan_comparisons` (optional persisted artifact)
May store precomputed comparison summaries.
Suggested fields:
- `id`
- `base_scan_id`
- `target_scan_id`
- `created_at`

A simpler v1 may compute comparisons dynamically instead of persisting this table.

---

## Finding Identity Strategy

Finding identity is required for:
- deduplication within a scan
- comparison across scans

For v1, use a stable fingerprint derived from some combination of:
- normalized file path
- vulnerability family / type normalization
- line number or nearby code span
- code snippet fingerprint
- nearby symbol or function context

Important v1 assumption:
- identity only needs to be good enough for repositories whose code is relatively stable between scans
- it does not need to perfectly survive major refactors

Triage does **not** carry across scans in v1.
Identity exists primarily for deduplication and scan diffing.

---

## API Specification

### Auth
All protected routes require authenticated Clerk user context verified by the backend.

### Repository Endpoints
- `POST /api/repositories`
  - register a repository
- `GET /api/repositories`
  - list repositories for current user
- `GET /api/repositories/{repository_id}`
  - fetch repository detail

### Scan Endpoints
- `POST /api/repositories/{repository_id}/scans`
  - create a scan job and enqueue it
- `GET /api/repositories/{repository_id}/scans`
  - list scan history
- `GET /api/scans/{scan_id}`
  - fetch scan detail and status
- `GET /api/scans/{scan_id}/files`
  - fetch file-level processing results
- `GET /api/scans/{scan_id}/findings`
  - fetch finding occurrences for a scan

### Comparison Endpoints
- `GET /api/repositories/{repository_id}/compare?base_scan_id=...&target_scan_id=...`
  - return new, fixed, and persisting findings

### Triage Endpoints
- `PATCH /api/finding-occurrences/{finding_occurrence_id}/triage`
  - update triage status and note for a finding occurrence

### Response Expectations
Responses should be JSON and shaped for frontend consumption.
The backend should own comparison logic, deduplication logic, and scan state transitions.

---

## Frontend Specification

### Required Screens
1. **Auth screens**
   - signup
   - login

2. **Repository list screen**
   - list registered repositories
   - option to add a repository

3. **Add repository screen / form**
   - GitHub HTTPS URL input
   - validation feedback

4. **Repository detail screen**
   - repository metadata
   - scan history
   - trigger new scan button

5. **Scan detail screen**
   - scan status
   - commit SHA
   - findings list
   - triage controls
   - file-level failures if any

6. **Scan comparison view**
   - new findings
   - fixed findings
   - persisting findings

### UI Notes
The UI may be minimal, but must be functional and coherent.
The user should be able to move from repository registration to scan kickoff to findings review without ambiguity.

---

## Failure Handling

### File-Level Failure
If a file cannot be processed successfully after malformed-output retries or other recoverable scanner failures:
- mark the file as failed
- continue the scan
- expose the failure in scan details

### Scan-Level Failure
A scan should be marked `failed` if the overall job cannot complete, such as when:
- repository clone fails
- worker crashes before completion
- repository cannot be processed at all
- a critical unrecoverable infrastructure error occurs

A scan should be marked `complete` even if some individual files failed, provided the overall job completed and partial results were stored.

---

## Deployment Specification

### Google Cloud Resources
At minimum, Terraform should provision or define configuration for:
- Cloud Run service for API
- Cloud Run service for worker
- Pub/Sub topic
- Pub/Sub push subscription to worker endpoint
- Postgres instance or managed Postgres service configuration
- service accounts / IAM bindings as needed
- environment variable / secret wiring

### Secrets / Config
The system must securely provide:
- Clerk configuration
- OpenAI API key
- database connection string
- Pub/Sub-related configuration

---

## Non-Requirements / Intentional Omissions for v1
- private repository support
- non-GitHub repository hosts
- multi-user collaboration or RBAC
- notifications or integrations
- remediation code generation
- advanced search / filtering / pagination beyond what is necessary
- perfect finding identity across major refactors
- guaranteed perfect recall across all Python codebases

---

## Acceptance Criteria
A submission satisfies the project requirements if it includes all of the following:
1. A working authenticated React + Vite web app with Clerk signup and login.
2. A FastAPI backend with a clean REST API separated from the frontend.
3. A Postgres-backed data model implemented through SQLAlchemy with Alembic migrations.
4. A user can register a public GitHub HTTPS repository and trigger a scan.
5. Each scan stores the exact commit SHA analyzed.
6. Scan execution is modeled as a persistent job and enqueued through Pub/Sub.
7. A separate worker service receives Pub/Sub push messages and executes scans.
8. The scanner analyzes `.py` files only and excludes default irrelevant paths including tests.
9. The scanner uses a two-stage LLM flow: suspicious/not suspicious first pass, then deep analysis on suspicious files.
10. Findings are stored with severity, vulnerability type, file path, line number, description, and explanation.
11. Severity uses the scale `low | medium | high | critical`.
12. Vulnerability type remains freeform.
13. Malformed model output is retried/repaired, and if still invalid the file fails while the overall scan continues.
14. Scan status is exposed as `queued`, `running`, `complete`, or `failed`.
15. The UI shows scan history, scan details, findings, and file failures.
16. Users can triage finding occurrences as `open`, `false_positive`, or `resolved` with optional notes.
17. Triage is per scan occurrence and each scan starts fresh.
18. The system supports finding deduplication and stable enough finding identity for cross-scan comparison.
19. The system provides scan diff comparison showing new, fixed, and persisting findings.
20. The implementation supports concurrent scans.
21. The project targets Google Cloud and includes Terraform.
22. The README covers architecture, prompts, parsing, context handling, tradeoffs, limitations, and next steps.
23. The scanner is capable of identifying the major intentional vulnerabilities in PyGoat.

---

## Remaining Open Implementation Details
The following still require engineering decisions during implementation, but no longer block the spec:
- exact prompt wording
- exact JSON schema details for stage 1 and stage 2 responses
- exact fingerprint construction logic
- exact concurrency limits for scans and files
- exact frontend styling and component choices
- exact Postgres hosting choice on Google Cloud
