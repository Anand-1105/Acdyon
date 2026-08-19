# Modularity & Responsibility Architecture

This document describes the modular architecture of the Acdyon Job Ingestion Subsystem, detailing how responsibilities are isolated and how the system remains maintainable, testable, and replaceable.

---

## 1. Why the System is Split

A production job-ingestion system experiences changes in multiple dimensions independently. Splitting the system into isolated modules yields concrete advantages:
*   **Upstream Feed Resiliency**: Changes in XML structures or source HTTP protocols do not affect database connections or presentation routing.
*   **Data Integrity & Identity**: Job deduplication and canonical hashing logic remain decoupled from the network transport client.
*   **Infrastructure Replaceability**: We can transition from an in-memory repository to a PostgreSQL/Supabase repository (or any other storage solution) without editing the core orchestration logic.
*   **HTTP Presentation Autonomy**: The FastAPI routes and static dashboard are simple interfaces sitting on top of the ingestion pipeline.

---

## 2. Module Map & Responsibilities

The production subsystem maps the single-responsibility principles of the original fetch/parse/store layout onto a highly testable, decoupled structure:

| Component | Directory / File | Primary Responsibility (One Job) | Why Isolated |
| :--- | :--- | :--- | :--- |
| **Domain Models** | [`src/domain/`](file:///c:/Users/anand/Desktop/Acdyon/src/domain/) | Enforces validation rules and data types for canonical records. | Ensures data integrity remains standard across all source adapters. |
| **Identity & Decoupling** | [`src/domain/identity.py`](file:///c:/Users/anand/Desktop/Acdyon/src/domain/identity.py) | Computes deterministic canonical hashes from raw fields. | Prevents duplicates from polluting database tables. |
| **Outbound Transport** | [`src/infrastructure/http/`](file:///c:/Users/anand/Desktop/Acdyon/src/infrastructure/http/) | Performs async GET requests under strict size and timeout limits. | Isolates connection/protocol layers from XML parsing. |
| **Pacing & Concurrency** | [`src/infrastructure/reliability/`](file:///c:/Users/anand/Desktop/Acdyon/src/infrastructure/reliability/) | Serializes request intervals and retries with randomized jitter. | Prevents DDoS profiles and rate limit blocks. |
| **Source Adapters** | [`src/adapters/`](file:///c:/Users/anand/Desktop/Acdyon/src/adapters/) | Normalizes specific feed endpoints to parsed batches of domain models. | Contains source-specific rules (e.g. WWR RSS vs other APIs). |
| **Persistence Port** | [`src/storage/base.py`](file:///c:/Users/anand/Desktop/Acdyon/src/storage/base.py) | Declares standard repository interfaces. | Decouples storage operations from specific database vendors. |
| **Persistence Adapter** | [`src/storage/postgres.py`](file:///c:/Users/anand/Desktop/Acdyon/src/storage/postgres.py) | Implements Supabase client operations and upserts. | Isolates raw storage schemas and library drivers. |
| **Ingestion Orchestrator** | [`src/services/orchestrator.py`](file:///c:/Users/anand/Desktop/Acdyon/src/services/orchestrator.py) | Coordinates request validation, adapter fetching, and db updates. | Controls the workflow sequence without knowing parser/http details. |
| **API Presentation** | [`src/api/routes/`](file:///c:/Users/anand/Desktop/Acdyon/src/api/routes/) | Exposes REST route mappings and HTTP response schemas. | Ensures HTTP protocol details do not pollute orchestration logic. |
| **Dashboard Interface** | [`src/static/`](file:///c:/Users/anand/Desktop/Acdyon/src/static/) | Renders the operational UI and makes client requests. | Can be rewritten or replaced without modifying Python code. |

---

## 3. Example Change Scenarios

The system allows developers to perform common upgrades by modifying isolated boundaries:

### Scenario A: WWR Feed Layout or Tag Name Changes
*   **Affected Module**: [`src/adapters/wwr/parser.py`](file:///c:/Users/anand/Desktop/Acdyon/src/adapters/wwr/parser.py)
*   **Impact**: Limited entirely to feed parsing. No changes are required in the storage layer, orchestrator, or API routes.

### Scenario B: Database Provider Migration (e.g., Supabase to DynamoDB/Mongo)
*   **Affected Module**: Create a new class in [`src/storage/`](file:///c:/Users/anand/Desktop/Acdyon/src/storage/) implementing [`BaseJobRepository`](file:///c:/Users/anand/Desktop/Acdyon/src/storage/base.py) and update the dependency injection provider in [`src/api/deps.py`](file:///c:/Users/anand/Desktop/Acdyon/src/api/deps.py).
*   **Impact**: Zero changes to the HTTP transport client, scraper adapters, or ingestion service.

### Scenario C: Pacing Delay & Retry Settings Tweaking
*   **Affected Module**: [`src/infrastructure/config.py`](file:///c:/Users/anand/Desktop/Acdyon/src/infrastructure/config.py)
*   **Impact**: Modifying configuration data classes alters pacing behavior without touching actual request logic.

### Scenario D: Adding a New Source (e.g., Remote OK)
*   **Affected Module**: Register a new class implementing [`BaseSourceAdapter`](file:///c:/Users/anand/Desktop/Acdyon/src/adapters/base.py) under [`src/adapters/`](file:///c:/Users/anand/Desktop/Acdyon/src/adapters/) and register it in the registry.
*   **Impact**: Minimal change. Ingestion service remains unmodified since it interacts via the registry port.
