# Engineering Decisions & Assessment Summary

This document addresses the core design decisions, operational trade-offs, and verification work for the Acdyon Job Ingestion Subsystem.

---

### 1. Source Selection: Public WWR RSS vs. Direct HTML Scraping

We chose We Work Remotely's public RSS feed (`https://weworkremotely.com/remote-jobs.rss`) over direct HTML page scraping for three architectural reasons:

* **Operational Reliability & Stability**: RSS feeds are syndicated data contracts designed specifically for machine consumption. HTML scraping is inherently fragile—minor CSS class renames, dynamic JavaScript re-renders, or DOM reorganizations cause silent ingestion failures.
* **Anti-Bot & Infrastructure Hygiene**: Direct scraping of HTML listing pages triggers anti-bot mechanisms (Cloudflare, CAPTCHA challenges, WAF rate blocks). Public syndication endpoints expect automated aggregators, allowing reliable periodic ingestion without evasive proxy rotation.
* **Network & Payload Efficiency**: A single ~150 KB XML payload provides complete, structured metadata for 100 recent jobs (including title, company, region, employment type, and RFC 2822 timestamps), replacing 100+ separate HTTP document requests and dramatically reducing upstream load.

---

### 2. Time-Limit Trade-Off & 1-Week Roadmap

* **The Explicit Trade-Off**: We implemented a **single-process asynchronous architecture** with process-local concurrency control (`asyncio.Lock`, `asyncio.Semaphore`, module-scoped singleton `RateLimiter`). For assessment evaluation, this eliminates distributed infrastructure dependencies while delivering high async throughput.
* **What Would Be Added With a Full Week**:
  1. **Distributed Queue & Worker Layer**: Introduce Celery or ARQ with Redis for durable background job scheduling, distributed task retries, and multi-node horizontal scaling.
  2. **Distributed Rate Limiting**: Migrate the in-memory token limiter to a shared Redis sliding-window counter to enforce global source pacing across horizontally scaled backend pods.
  3. **Full-Text & Vector Search**: Add PostgreSQL full-text search (`tsvector`) and embedding generation in Supabase for semantic search over job descriptions.
  4. **Automated Schema-Drift Alerting**: Add webhook alerting (Slack / PagerDuty) triggered when the parser encounters unmapped tag structures or widespread validation drops.

---

### 3. AI Usage & Independent Verification / Corrections

AI assistance was utilized for boilerplate scaffolding (Pydantic models, route templates, initial test fixtures). All critical architecture, security boundaries, and runtime behaviors were **personally reviewed, debugged, and corrected**:

* **XML Entity Bomb Vulnerability**: AI initially generated standard `xml.etree.ElementTree` parsing with a soft fallback. I replaced this with mandatory `defusedxml.ElementTree` and removed stdlib fallbacks to guarantee immunity against Billion Laughs and XXE exploits.
* **RateLimiter Scope Defect**: Discovered that FastAPI's dependency injection was creating a new `RateLimiter` per HTTP request, bypassing concurrent request pacing. Refactored the DI layer to use a shared singleton registry (`get_source_adapter_registry()`).
* **Source Health State Machine Correction**: Fixed orchestrator logic where database write failures were incorrectly advancing the upstream `last_success_at` timestamp. Separated transport health from persistence status so persistence errors mark the run `FAILED` while preserving upstream telemetry truth.
* **Static Routing & API 404 Interception**: Resolved a Starlette routing issue where `StaticFiles(html=True)` mounted at `/` swallowed unmapped `/api/*` endpoints and served `index.html` with HTTP 200. Replaced with explicit `StarletteHTTPException` handling returning JSON 404s for API paths and custom HTML 404s for web pages.
* **Deterministic Resilience Suite**: Designed and verified 305 automated tests covering transient/persistent HTTP 429s (with `Retry-After` parsing and jittered backoff), 5xx outages, transport timeouts, malformed XML, and Supabase PostgreSQL persistence.
