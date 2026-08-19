# Architectural Decisions — Acdyon Job Ingestion Subsystem

This document summarizes the core engineering and design decisions applied to the Acdyon Job Ingestion Subsystem.

---

### ADR 1: Layered Decoupled Architecture
* **Status**: Approved
* **Context**: The legacy script coupled XML fetching, HTML parsing, database writes, and terminal logs into a single script.
* **Decision**: We decoupled the application into five clean architectural layers:
  1. **Domain (`src/domain/`)**: Holds immutable, verified validation schemas (`JobRecord`, `SourceInfo`, etc.).
  2. **Infrastructure (`src/infrastructure/`)**: Manages HTTP clients, rate limiting, and backoff retries.
  3. **Adapters (`src/adapters/`)**: Normalizes external feed data structures (e.g. WWR RSS).
  4. **Persistence (`src/storage/`)**: Integrates clean repository contracts for Supabase/PostgreSQL database writing.
  5. **Presentation (`src/api/` & `src/static/`)**: Exposes REST endpoints and serves a clean Web Dashboard UI.

---

### ADR 2: Secure Parsing with Mandatory `defusedxml`
* **Status**: Approved
* **Context**: Parsing XML using standard Python libraries exposes the server to external entity resolution (XXE) and recursive expansion attacks (Billion Laughs / XML bombs).
* **Decision**: We enforced mandatory `defusedxml.ElementTree` parsing, completely removing silent fallback options to the standard library to guarantee transport safety.

---

### ADR 3: Singleton Registry and Global Rate Limiting
* **Status**: Approved
* **Context**: The web application factory instantiates handlers for each API call, which initially led to `RateLimiter` instances being reconstructed per request.
* **Decision**: We implemented a shared singleton registry (`get_source_adapter_registry()`) in the dependency injection container, preserving a single stateful rate limiter across all concurrent incoming requests to prevent blocking from We Work Remotely.

---

### ADR 4: Precise Status Separation in Health Telemetry
* **Status**: Approved
* **Context**: Persistence errors (e.g., database connection drops) could mask or advance the successful-ingestion timestamp of a source.
* **Decision**: We strictly separated network liveness from database transaction status. If a source fetch succeeds but database writing fails, the run status is marked `FAILED` while preserving the last valid `last_success_at` timestamp.
