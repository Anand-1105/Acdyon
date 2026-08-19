"""Tests for Static Dashboard UI Serving."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import app


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

    def test_styles_css_served(self, client):
        response = client.get("/styles.css")
        assert response.status_code == 200
        assert "--bg-app:" in response.text

    def test_app_js_served(self, client):
        response = client.get("/app.js")
        assert response.status_code == 200
        assert "triggerIngestion" in response.text

    def test_config_js_served(self, client):
        response = client.get("/config.js")
        assert response.status_code == 200
        assert "PUBLIC_API_BASE_URL" in response.text

    def test_no_secrets_in_static_files(self, client):
        for path in ["/", "/app.js", "/config.js"]:
            text = client.get(path).text.lower()
            assert "supabase_service_role_key" not in text
            assert "supabase_url" not in text
            assert "secret" not in text
            assert "password" not in text

    def test_cors_middleware_headers(self):
        # Verify custom origins can be configured and are accepted
        import os
        from src.api.app import create_app
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

