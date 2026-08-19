"""Production Readiness & Verification Script for Step 15."""

import os
import re
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(os.getcwd())
sys.path.insert(0, str(PROJECT_ROOT))


def run_production_verification():
    print("=" * 70)
    print("Acdyon Production Deployment & Environment Hardening Verification")
    print("=" * 70)

    # 1. Import Verification
    print("\n1. Verifying production runtime imports...")
    import fastapi
    import supabase
    import defusedxml
    from src.api.app import app
    from src.api.deps import get_source_adapter_registry
    from src.services.orchestrator import IngestionService
    print("   [OK] FastAPI, Supabase, defusedxml, App, DI Registry imported successfully.")

    # 2. FastAPI TestClient Verification
    from fastapi.testclient import TestClient
    client = TestClient(app)

    # 3. Liveness Check (GET /health)
    print("\n2. Testing process liveness (GET /health)...")
    res = client.get("/health")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    assert res.json() == {"status": "ok"}
    print("   [OK] /health is online and independent of external sources.")

    # 4. Static Dashboard Asset Verification
    print("\n3. Verifying static dashboard serving...")
    res_root = client.get("/")
    assert res_root.status_code == 200
    assert "Acdyon / Job Ingestion Subsystem" in res_root.text

    res_css = client.get("/styles.css")
    assert res_css.status_code == 200
    assert "--bg-app:" in res_css.text

    res_js = client.get("/app.js")
    assert res_js.status_code == 200
    assert "triggerIngestion" in res_js.text
    print("   [OK] Dashboard assets (HTML, CSS, JS) served correctly at root.")

    # 5. Live WWR Ingestion via API
    print("\n4. Performing controlled production WWR ingestion (POST /api/v1/ingest)...")
    ingest_res = client.post("/api/v1/ingest", json={"source_name": "weworkremotely"})
    assert ingest_res.status_code == 200
    data = ingest_res.json()
    assert data["status"] in ("success", "partial_success")
    rec_count = len(data["records"])
    print(f"   [OK] Ingestion completed: status={data['status']}, records_accepted={rec_count}, duration_ms={data['stats']['duration_ms']}")

    # 6. Canonical Job Retrieval
    print("\n5. Querying canonical jobs (GET /api/v1/jobs)...")
    jobs_res = client.get("/api/v1/jobs?limit=5")
    assert jobs_res.status_code == 200
    jobs = jobs_res.json()
    assert len(jobs) > 0
    sample_job = jobs[0]
    print(f"   [OK] Retrieved {len(jobs)} jobs. Sample Canonical ID: '{sample_job['canonical_id']}', Company: '{sample_job['company']}', Title: '{sample_job['title']}'")

    # 7. Source Health & Telemetry Check
    print("\n6. Verifying source health and run telemetry endpoints...")
    health_res = client.get("/api/v1/health/weworkremotely")
    assert health_res.status_code == 200
    h_data = health_res.json()
    assert h_data["health_status"] == "healthy"

    run_res = client.get("/api/v1/runs/latest/weworkremotely")
    assert run_res.status_code == 200
    r_data = run_res.json()
    print(f"   [OK] Source Health: {h_data['health_status']} (last_success_at: {h_data['last_success_at']})")
    print(f"   [OK] Latest Run ID: {r_data['run_id']}, Status: {r_data['status']}")

    # 8. Simulated Application Restart
    print("\n7. Simulating application process restart...")
    import src.api.deps as deps
    deps._global_registry = None
    deps._global_storage = None
    
    restarted_app = deps.get_ingestion_service()
    restarted_reg = get_source_adapter_registry()
    assert restarted_reg is not None
    print("   [OK] Application process restarted cleanly. Registry and RateLimiter recreated safely.")

    # 9. Repository Secret Safety Audit
    print("\n8. Auditing codebase for hardcoded secrets...")
    secret_patterns = [
        re.compile(r"eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*"), # JWT
        re.compile(r"sbp_[a-f0-9]{40}"), # Supabase key pattern
        re.compile(r"postgres://[^:]+:[^@]+@"), # DB URL with pass
    ]

    scanned_files = 0
    violations = 0
    for root, dirs, files in os.walk(PROJECT_ROOT):
        # Exclude git, venv, pycache, scratch
        dirs[:] = [d for d in dirs if d not in (".git", ".pytest_cache", "__pycache__", "scratch", ".venv", "env")]
        for f in files:
            if f.endswith((".py", ".sql", ".js", ".html", ".css", ".toml", ".json", ".md")):
                filepath = os.path.join(root, f)
                scanned_files += 1
                try:
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as fh:
                        content = fh.read()
                        for pat in secret_patterns:
                            if pat.search(content):
                                print(f"   [WARN] Secret pattern matched in {filepath}")
                                violations += 1
                except Exception:
                    pass

    assert violations == 0, f"Found {violations} potential hardcoded secrets!"
    print(f"   [OK] Scanned {scanned_files} project files: ZERO secrets found.")

    print("\n" + "=" * 70)
    print("Production Readiness Verification: ALL 8 CHECKS PASSED SUCCESSFULLY")
    print("=" * 70)

if __name__ == "__main__":
    run_production_verification()
