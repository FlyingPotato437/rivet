"""Check the release boundary without contacting a cloud service."""

import pytest

from scripts.prepare_pilot_deployment import (
    hosting_config,
    public_origin,
    runtime_paths,
    validate_build,
)


@pytest.mark.parametrize("value", [
    "http://app.example.com", "https://localhost", "https://127.0.0.1",
    "https://10.1.0.2", "https://app.internal", "https://app.example.com/path",
    "https://user:secret@app.example.com", "https://app.example.com?token=secret",
    "https://app.example.com#secret", "https://*.example.com",
])
def test_release_cannot_target_local_or_credential_bearing_origins(value):
    with pytest.raises(ValueError):
        public_origin(value)


def test_outlook_proxies_api_requests_without_caching_private_responses():
    config = hosting_config("outlook", "https://api.example.com")
    assert config["rewrites"] == [{
        "source": "/api/:path*", "destination": "https://api.example.com/api/:path*",
    }]
    assert any(h["source"] == "/api/(.*)" and h["headers"] == [
        {"key": "Cache-Control", "value": "private, no-store"},
    ] for h in config["headers"])
    assert "outlook.cloud.microsoft" in str(config["headers"])
    assert "installCommand" not in config


def test_runtime_release_excludes_customer_documents_credentials_and_caches(tmp_path):
    from scripts.prepare_pilot_deployment import RUNTIME_FILES

    for name in (*RUNTIME_FILES, "backend/api/main.py", "backend/.env", "backend/__pycache__/main.pyc",
                 ".env", ".data/private.pdf", "launch-video/private.mp4", "apps/web/.env.local"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test")
    included = {p.relative_to(tmp_path).as_posix() for p in runtime_paths(tmp_path)}
    assert included == {*RUNTIME_FILES, "backend/api/main.py"}


@pytest.mark.parametrize("key", ["pk_test_development", "sk_test_private", "sk_live_private"])
def test_release_rejects_development_instances_and_bundled_secrets(tmp_path, key):
    (tmp_path / "app.js").write_text(f'const key="{key}";')
    with pytest.raises(ValueError, match="production public key"):
        validate_build(tmp_path)


def test_release_allows_the_production_public_key(tmp_path):
    (tmp_path / "app.js").write_text('const key="pk_live_public";')
    validate_build(tmp_path)
