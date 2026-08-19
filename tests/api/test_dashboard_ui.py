"""Tests for Static Dashboard UI Serving and Routing Hardening."""

from __future__ import annotations

import os
import pytest
from fastapi.testclient import TestClient

from src.api.app import app, create_app


@pytest.fixture
def client():
    return TestClient(app)


class TestDashboardUIStaticServing:
    def test_root_serves_index_html(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "Acdyon / Job Ingestion Subsystem" in response.text
        assert "Ingest latest jobs" in response.text
        assert "We Work Remotely" in response.text
        assert "text/html" in response.headers.get("content-type", "")

    def test_styles_css_served(self, client):
        response = client.get("/styles.css")
        assert response.status_code == 200
        assert "--bg-app:" in response.text
        assert "text/css" in response.headers.get("content-type", "")

    def test_app_js_served(self, client):
        response = client.get("/app.js")
        assert response.status_code == 200
        assert "triggerIngestion" in response.text
        assert "openJobDetail" in response.text

    def test_config_js_served(self, client):
        response = client.get("/config.js")
        assert response.status_code == 200
        assert "PUBLIC_API_BASE_URL" in response.text

    def test_404_html_direct_asset_served(self, client):
        response = client.get("/404.html")
        assert response.status_code == 200
        assert "Page Not Found" in response.text
        assert "404" in response.text
        assert "text/html" in response.headers.get("content-type", "")

    def test_no_secrets_in_static_files(self, client):
        for path in ["/", "/app.js", "/config.js", "/404.html"]:
            text = client.get(path).text.lower()
            assert "supabase_service_role_key" not in text
            assert "supabase_url" not in text
            assert "secret" not in text
            assert "password" not in text

    def test_cors_middleware_headers(self):
        os.environ["CORS_ORIGINS"] = "https://acdyon.pages.dev"
        try:
            custom_app = create_app()
            custom_client = TestClient(custom_app)
            headers = {
                "Origin": "https://acdyon.pages.dev",
                "Access-Control-Request-Method": "POST",
            }
            # Preflight request
            response = custom_client.options("/api/v1/ingest", headers=headers)
            assert response.headers.get("access-control-allow-origin") == "https://acdyon.pages.dev"
        finally:
            del os.environ["CORS_ORIGINS"]


class TestRoutingAndErrorHardening:
    def test_unknown_website_route_returns_404_html(self, client):
        response = client.get("/random-nonexistent-page")
        assert response.status_code == 404
        assert "text/html" in response.headers.get("content-type", "")
        assert "Page Not Found" in response.text
        assert "Return to Dashboard" in response.text
        # Ensure it does not return dashboard HTML with 200
        assert "Canonical Job Postings" not in response.text

    def test_unknown_api_route_returns_404_json(self, client):
        response = client.get("/api/v1/does-not-exist")
        assert response.status_code == 404
        assert "application/json" in response.headers.get("content-type", "")
        data = response.json()
        assert "detail" in data
        assert "not found" in data["detail"].lower()
        # Must not return HTML
        assert "<!doctype html>" not in response.text.lower()

    def test_missing_job_returns_404_json(self, client):
        response = client.get("/api/v1/jobs/nonexistent_id_999999")
        assert response.status_code == 404
        assert "application/json" in response.headers.get("content-type", "")
        data = response.json()
        assert "detail" in data
        assert "nonexistent_id_999999" in data["detail"]

    def test_missing_source_health_returns_404_json(self, client):
        response = client.get("/api/v1/health/unknown_provider_xyz")
        assert response.status_code == 404
        assert "application/json" in response.headers.get("content-type", "")
        data = response.json()
        assert "detail" in data
        assert "unknown_provider_xyz" in data["detail"]

    def test_missing_run_returns_404_json(self, client):
        response = client.get("/api/v1/runs/run_nonexistent_999999")
        assert response.status_code == 404
        assert "application/json" in response.headers.get("content-type", "")
        data = response.json()
        assert "detail" in data
        assert "run_nonexistent_999999" in data["detail"]
