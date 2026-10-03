"""Production boundaries; uses fake configuration and never external credentials."""

import base64
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.config import (
    allowed_hosts,
    allowed_origins,
    normalize_database_url,
    validate_configuration,
)


@pytest.fixture
def production_settings(monkeypatch, tmp_path):
    values = {
        "RIVET_ENV": "production",
        "RIVET_AUTH_MODE": "clerk",
        "RIVET_LOCAL_ONLY": "false",
        "RIVET_DEMO_ENABLED": "false",
        "RIVET_COMPOSE_DATABASE": "false",
        "RIVET_DATABASE_URL": "postgresql://test:test@localhost/rivet_test",
        "RIVET_DATA_DIR": str(tmp_path),
        "RIVET_ALLOWED_ORIGINS": "https://app.example.com",
        "RIVET_ALLOWED_HOSTS": "api.example.com,127.0.0.1",
        "CLERK_SECRET_KEY": "sk_live_production_test_placeholder",
        "VITE_CLERK_PUBLISHABLE_KEY": "pk_live_"
        + base64.b64encode(b"clerk.example.com$").decode(),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def test_production_requires_explicit_auth_origins_and_persistent_storage(
    production_settings,
):
    validate_configuration()
    assert allowed_origins() == ["https://app.example.com"]
    assert allowed_hosts() == ["api.example.com", "127.0.0.1"]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("RIVET_AUTH_MODE", "local"),
        ("RIVET_LOCAL_ONLY", "true"),
        ("RIVET_DEMO_ENABLED", "true"),
        ("CLERK_SECRET_KEY", "sk_test_must_not_be_exposed"),
        ("VITE_CLERK_PUBLISHABLE_KEY", "pk_test_not_production"),
        ("RIVET_DATABASE_URL", "sqlite:///temporary.db"),
        ("RIVET_DATABASE_URL", ""),
        ("RIVET_DATA_DIR", ".data"),
        ("RIVET_DATA_DIR", ""),
        ("RIVET_ALLOWED_ORIGINS", ""),
        ("RIVET_ALLOWED_ORIGINS", " , , "),
        ("RIVET_ALLOWED_ORIGINS", "*"),
        ("RIVET_ALLOWED_ORIGINS", "http://app.example.com"),
        ("RIVET_ALLOWED_ORIGINS", "https://app.example.com/orders"),
        ("RIVET_ALLOWED_ORIGINS", "https://app.example.com?token=private"),
        ("RIVET_ALLOWED_ORIGINS", "https://user:private@app.example.com"),
        ("RIVET_ALLOWED_HOSTS", ""),
        ("RIVET_ALLOWED_HOSTS", " , , "),
        ("RIVET_ALLOWED_HOSTS", "*.example.com"),
        ("RIVET_ALLOWED_HOSTS", "https://api.example.com"),
        ("RIVET_ALLOWED_HOSTS", "api.example.com:8787"),
    ],
)
def test_unsafe_production_configuration_fails_closed(
    production_settings, monkeypatch, key, value
):
    monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError) as error:
        validate_configuration()
    assert ("development demo" if key == "RIVET_DEMO_ENABLED" else key) in str(
        error.value
    )
    assert "must_not_be_exposed" not in str(error.value)
    assert "token=private" not in str(error.value)
    assert "user:private" not in str(error.value)


def test_hosted_database_aliases_preserve_driver_and_connection_options():
    for prefix in ("postgres://", "postgresql://", "postgresql+psycopg://"):
        suffix = "user:encoded%40password@db.example.com:5432/rivet?sslmode=require"
        assert (
            normalize_database_url(prefix + suffix) == "postgresql+psycopg://" + suffix
        )


def test_development_does_not_require_production_credentials(monkeypatch):
    monkeypatch.setenv("RIVET_ENV", "development")
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    validate_configuration()


@pytest.mark.parametrize(
    "password", ["", "abcd", "unsafe@password/with#url:punctuation", "x" * 64]
)
def test_compose_password_must_be_url_safe(production_settings, monkeypatch, password):
    monkeypatch.setenv("RIVET_COMPOSE_DATABASE", "true")
    monkeypatch.setenv("POSTGRES_PASSWORD", password)
    with pytest.raises(RuntimeError, match="POSTGRES_PASSWORD") as error:
        validate_configuration()
    if password:
        assert password not in str(error.value)


def test_compose_accepts_a_generated_hex_password(production_settings, monkeypatch):
    monkeypatch.setenv("RIVET_COMPOSE_DATABASE", "true")
    monkeypatch.setenv("POSTGRES_PASSWORD", "a1" * 32)
    validate_configuration()


def test_production_http_boundary_allows_exact_cors_without_opening_private_routes(
    production_settings,
):
    # A fresh process applies production middleware at import time. It uses only
    # public/config/unauthenticated routes, without contacting Clerk or a DB.
    script = r"""
from fastapi.testclient import TestClient
from backend.api.main import app

origin = "https://app.example.com"
with TestClient(app, base_url="https://api.example.com") as client:
    response = client.options("/api/records", headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type,x-rivet-client,idempotency-key",
    })
    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in response.headers

    response = client.get("/api/records", headers={"Origin": origin})
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["cache-control"] == "no-store"
    exposed = response.headers["access-control-expose-headers"].lower()
    assert "content-disposition" in exposed and "content-range" in exposed

    response = client.options("/api/records", headers={
        "Origin": "https://attacker.example",
        "Access-Control-Request-Method": "POST",
    })
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
    response = client.post("/api/orders", headers={
        "Origin": "https://attacker.example", "X-Rivet-Client": "workspace",
    }, json={})
    assert response.status_code == 403

    response = client.get("/api/auth/config", headers={"Origin": origin})
    assert response.status_code == 200
    config = response.json()
    assert config["mode"] == "clerk"
    assert "sk_live" not in response.text
    assert config["demo"] is None
    assert client.get("/api/auth/config", headers={"Host": "attacker.example"}).status_code == 400
    assert app.docs_url is None and app.openapi_url is None
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, **production_settings},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
