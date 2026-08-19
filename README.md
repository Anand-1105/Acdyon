# Acdyon Job Ingestion System — Domain Contracts

This repository contains the canonical domain contracts and ingestion subsystem architecture for job ingestion.

---

## Architectural Boundary

The canonical domain contracts establish the strict architectural boundary between external source data (such as We Work Remotely RSS or future public APIs) and internal downstream systems (validation, deduplication, persistence, APIs, and UI).

```
External Source (WWR RSS / API / Sandbox)
                  │
                  ▼
         [ Source Adapter ]
                  │
                  ▼
         [ Source Parser ]
                  │
                  ▼
   ══════════════════════════════
   CANONICAL DOMAIN CONTRACTS
   (JobRecord, SourceInfo, Stats)
   ══════════════════════════════
                  │
                  ▼
      [ Validation Subsystem ]
                  │
                  ▼
    [ Deduplication Subsystem ]
                  │
                  ▼
  [ Persistence / API / Telemetry ]
```

---

## Canonical Models Overview

### 1. `JobRecord` (`src/domain/job.py`)
Normalized representation of an individual job posting across all sources.

| Field | Type | Required | Provenance | Description |
|---|---|---|---|---|
| `canonical_id` | `str` | Yes | System-derived | Deterministic unique ID (`source_name_hash`). |
| `source_name` | `str` | Yes | System-derived | Identifier of origin source (e.g., `'weworkremotely'`). |
| `source_id` | `Optional[str]` | No | Source-derived | Source-native unique ID / GUID. |
| `source_url` | `str` | Yes | Source-derived | Canonical direct URL to job posting (HTTP/HTTPS). |
| `title` | `str` | Yes | Source-derived | Normalized job title (1–512 chars). |
| `company` | `str` | Yes | Source-derived | Normalized hiring company name (1–256 chars). |
| `location` | `str` | Yes | Source-derived | Normalized location/remote policy (default: `'Remote'`). |
| `description` | `str` | Yes | Source-derived | Job description body (1–100,000 chars). |
| `employment_type` | `EmploymentType` | Yes | Source-derived | Categorized enum (`FULL_TIME`, `CONTRACT`, etc.). |
| `salary` | `Optional[SalaryInfo]` | No | Source-derived | Structured compensation (`currency`, `min_amount`, `max_amount`, `interval`, `raw_text`). |
| `requirements` | `List[str]` | Yes | Source-derived | Extracted skill/requirement tags (default: `[]`). |
| `published_at` | `datetime` (UTC) | Yes | Source-derived | When the job was published at the source. |
| `ingested_at` | `datetime` (UTC) | Yes | System-derived | When the job was processed by this system. |
| `status` | `JobStatus` | Yes | System-derived | Operational status (`ACTIVE`, `EXPIRED`, `ARCHIVED`, `UNKNOWN`). |
| `metadata` | `dict[str, Any]` | Yes | Source-derived | Non-lossy source extensions or feed metadata. |

### 2. `SourceInfo` (`src/domain/source.py`)
Metadata describing the external data source.

| Field | Type | Required | Description |
|---|---|---|---|
| `source_name` | `str` | Yes | Unique source slug (e.g., `'weworkremotely'`). |
| `source_type` | `SourceType` | Yes | Feed protocol: `RSS`, `API`, `WEB`, `SANDBOX`. |
| `endpoint` | `str` | Yes | URL / descriptor of source endpoint. |
| `retrieval_timestamp`| `datetime` (UTC) | Yes | UTC timestamp of fetch execution. |
| `attribution` | `Optional[str]` | No | Copyright / source attribution statement. |
| `health_status` | `SourceHealthStatus` | Yes | Source state: `HEALTHY`, `DEGRADED`, `UNREACHABLE`, `UNKNOWN`. |
| `metadata` | `dict[str, Any]` | Yes | Optional feed headers (ETag, Last-Modified, etc.). |

### 3. `IngestionRequest` (`src/domain/ingestion.py`)
Canonical request parameters passed into the ingestion subsystem.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `source_name` | `Optional[str]` | `None` | Specific source target. |
| `search_term` | `Optional[str]` | `None` | Keyword search query. |
| `location` | `Optional[str]` | `None` | Location filter. |
| `category` | `Optional[str]` | `None` | Category / tag filter (e.g. `'programming'`). |
| `limit` | `Optional[int]` | `None` | Result limit (1–1000). |
| `since` | `Optional[datetime]` | `None` | Lower publication timestamp boundary. |
| `metadata` | `dict[str, Any]` | `{}` | Context/runtime options. |

### 4. `IngestionStats` (`src/domain/ingestion.py`)
Execution statistics and operational observability metrics for an ingestion run.

| Field | Type | Description |
|---|---|---|
| `source_name` | `str` | Source targeted by run. |
| `started_at` | `datetime` (UTC) | UTC start timestamp. |
| `completed_at` | `datetime` (UTC) | UTC completion timestamp. |
| `duration_ms` | `int` | Execution duration in milliseconds. |
| `records_received` | `int` | Total raw records retrieved from source. |
| `records_accepted` | `int` | Total records passing schema validation. |
| `records_rejected` | `int` | Total records failing schema validation. |
| `duplicates_detected`| `int` | Total duplicate postings identified. |
| `retries` | `int` | Transient network retry count. |
| `failed_requests` | `int` | Failed network requests count. |
| `status` | `IngestionRunStatus` | Outcome: `SUCCESS`, `PARTIAL_SUCCESS`, `FAILED`. |

### 5. `IngestionError` (`src/domain/errors.py`)
Structured representation of warnings and errors with automated credential/secret redaction.

| Field | Type | Description |
|---|---|---|
| `error_type` | `IngestionErrorType` | Classification (`VALIDATION_ERROR`, `RATE_LIMIT_ERROR`, `NETWORK_TRANSPORT_ERROR`, `TIMEOUT_ERROR`, `SOURCE_SERVER_ERROR`, `MALFORMED_RESPONSE_ERROR`, `INVALID_RECORD_ERROR`, `PERSISTENCE_ERROR`, `INTERNAL_ERROR`). |
| `scope` | `ErrorScope` | Granularity: `RUN`, `REQUEST`, `RECORD`. |
| `message` | `str` | Human-readable sanitized message. |
| `details` | `dict[str, Any]` | Safe diagnostics (credentials, tokens, headers automatically redacted). |
| `record_id` | `Optional[str]` | Associated record ID if scoped to an individual record. |
| `retryable` | `bool` | Indicates whether failure may resolve on retry. |
| `timestamp` | `datetime` (UTC) | Time of error occurrence. |

### 6. `IngestionResult` (`src/domain/ingestion.py`)
Standard outcome object returned by the ingestion subsystem.

| Field | Type | Description |
|---|---|---|
| `status` | `IngestionRunStatus` | `SUCCESS`, `PARTIAL_SUCCESS`, or `FAILED`. |
| `records` | `List[JobRecord]` | List of valid canonical job postings. |
| `stats` | `IngestionStats` | Execution statistics. |
| `errors` | `List[IngestionError]` | Structured errors and warnings. |
| `source_info` | `SourceInfo` | Source metadata. |

---

## Identity & Deduplication Rules (`src/domain/identity.py`)

Deterministic canonical ID calculation uses a strict fallback precedence hierarchy:
1. **Source Record ID**: `source_name + ":" + source_id` (e.g. RSS GUID, API ID).
2. **Canonicalized URL**: `source_name + ":" + canonicalize_url(source_url)` (tracking parameters such as `utm_*`, `ref`, `fbclid` stripped; scheme/host lowercased).
3. **Composite Fallback**: `source_name + ":" + normalized(company) + ":" + normalized(title)`.

The final canonical ID is generated as:
`{source_name}_{sha256(seed)[:16]}`

---

## Status Taxonomy Separation

The architecture explicitly segregates three distinct status concepts:
- **`JobStatus`**: State of the job posting (`ACTIVE`, `EXPIRED`, `ARCHIVED`, `UNKNOWN`).
- **`IngestionRunStatus`**: State of the execution pipeline (`SUCCESS`, `PARTIAL_SUCCESS`, `FAILED`).
- **`SourceHealthStatus`**: State of the external provider (`HEALTHY`, `DEGRADED`, `UNREACHABLE`, `UNKNOWN`).

---

---

## Production Deployment & Operational Guide

### 1. Local Development & Virtual Environment Setup

Install the declared runtime and development dependencies into a Python 3.11+ environment:

```bash
# Create virtual environment
python -m venv .venv

# Activate virtual environment (Windows PowerShell)
.venv\Scripts\Activate.ps1

# Install project metadata & dependencies
pip install -e ".[dev]"
```

### 2. Environment Configuration (`.env`)

Copy `.env.example` to `.env` and set appropriate variables:

```bash
# Server Port
PORT=8000

# CORS Allowed Origins (Leave empty for same-origin serving)
CORS_ORIGINS=http://localhost:3000

# PostgreSQL / Supabase Credentials (Required for DB mode, leave blank for InMemory mode)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-server-side-service-role-key

# Logging Verbosity
LOG_LEVEL=INFO
```

### 3. Database Schema Setup & Migration Order

To initialize a new PostgreSQL / Supabase instance, execute the idempotent migration file in order:

1. **Schema Migration**: Run [`migrations/001_initial_schema.sql`](file:///c:/Users/anand/Desktop/Acdyon/migrations/001_initial_schema.sql) in your PostgreSQL query editor or via `psql`.
   - Creates `jobs`, `ingestion_runs`, `source_health`, and `ingestion_snapshots` tables.
   - Enforces unique index constraints on `canonical_id` and `source_name`.
2. **Environment Wiring**: Set `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` in environment.
3. **Application Launch**: Start the FastAPI service.

### 4. Production Startup Command

Run the single-process FastAPI application server using Uvicorn:

```bash
uvicorn src.api.app:app --host 0.0.0.0 --port $PORT --workers 1
```

### 5. Live Production Render Deployment

* **Live Service URL**: [https://acdyon-backend-72ph.onrender.com](https://acdyon-backend-72ph.onrender.com)
* **Build Command**: `pip install .`
* **Start Command**: `uvicorn src.api.app:app --host 0.0.0.0 --port $PORT`
* **Health Check Path**: `/health`
* **Database Backend**: Supabase PostgreSQL (`https://zigoelrrclmahfeefrhh.supabase.co`)
* **Environment Variables Configured**:
  * `SUPABASE_URL`: Supabase project connection URL
  * `SUPABASE_SERVICE_ROLE_KEY`: Supabase server-side authorization key
  * `LOG_LEVEL`: `INFO`

### 6. Running Automated Tests

Run the complete test suite (**313 tests**):

```bash
pytest tests/ -v
```

---

## Operational Interface Guide: Dashboard vs. Logs

The web interface separates daily ingestion workflows from historical operational debugging:

### 1. Primary Dashboard (`/`)
* **Focus**: Current ingestion workflow, provider health, freshness, manual trigger actions (`Ingest latest jobs`), latest run telemetry, and canonical job discovery with sanitized job detail drawers.
* **Question Answered**: *"What is the current health of We Work Remotely, and what jobs are currently available?"*

### 2. Operational Logs (`/logs` view)
* **Focus**: Chronological ingestion event history and system activity visualization backed directly by persisted database records from the `ingestion_runs` repository (`GET /api/v1/logs`).
* **24-Hour Activity Timeline**: Represents real historical ingestion executions (Green = Success, Yellow = Partial/Degraded, Red = Failed, Gray = No recorded event).
* **Question Answered**: *"What happened previously, what was the duration/throughput of past runs, and what failed if an error occurred?"*
* *Note: The activity timeline visualizes discrete persisted ingestion run events, not synthetic or continuous uptime polling.*
