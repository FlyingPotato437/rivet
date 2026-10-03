"""Deployment settings shared by the API, migrations, and worker entrypoint."""

import os
import re
from pathlib import Path
from urllib.parse import urlsplit


def production():
    return os.getenv("RIVET_ENV", "development").lower() == "production"


def normalize_database_url(value):
    # Hosted Postgres services commonly supply either of these aliases. The
    # installed driver is psycopg 3, not SQLAlchemy's default psycopg2 driver.
    for prefix in ("postgres://", "postgresql://"):
        if value.startswith(prefix):
            return "postgresql+psycopg://" + value[len(prefix) :]
    return value


def allowed_origins():
    fallback = (
        ""
        if production()
        else "http://127.0.0.1:5178,http://localhost:5178,http://127.0.0.1:8787,http://localhost:8787"
    )
    return list(
        dict.fromkeys(
            origin.strip().rstrip("/")
            for origin in os.getenv("RIVET_ALLOWED_ORIGINS", fallback).split(",")
            if origin.strip()
        )
    )


def allowed_hosts():
    fallback = "" if production() else "127.0.0.1,localhost,testserver"
    return [
        host.strip()
        for host in os.getenv("RIVET_ALLOWED_HOSTS", fallback).split(",")
        if host.strip()
    ]


def validate_configuration():
    """Refuse a public deployment with development auth or implicit local data."""
    if not production():
        return
    errors = []
    required = (
        "RIVET_DATABASE_URL",
        "RIVET_DATA_DIR",
        "RIVET_ALLOWED_ORIGINS",
        "RIVET_ALLOWED_HOSTS",
        "CLERK_SECRET_KEY",
        "VITE_CLERK_PUBLISHABLE_KEY",
    )
    for name in required:
        if not os.getenv(name, "").strip():
            errors.append(f"{name} must be configured")
    if os.getenv("RIVET_AUTH_MODE") != "clerk":
        errors.append("RIVET_AUTH_MODE must be clerk")
    if os.getenv("RIVET_LOCAL_ONLY") != "false":
        errors.append("RIVET_LOCAL_ONLY must be false")
    if not os.getenv("CLERK_SECRET_KEY", "").startswith("sk_live_"):
        errors.append("CLERK_SECRET_KEY must use the production Clerk instance")
    if not os.getenv("VITE_CLERK_PUBLISHABLE_KEY", "").startswith("pk_live_"):
        errors.append(
            "VITE_CLERK_PUBLISHABLE_KEY must use the production Clerk instance"
        )
    if os.getenv("RIVET_DEMO_ENABLED", "false") != "false":
        errors.append("The development demo must be disabled")
    data_dir = os.getenv("RIVET_DATA_DIR", "")
    if data_dir and not Path(data_dir).is_absolute():
        errors.append("RIVET_DATA_DIR must be an absolute persistent-volume path")
    database = os.getenv("RIVET_DATABASE_URL", "")
    if database and not normalize_database_url(database).startswith(
        "postgresql+psycopg://"
    ):
        errors.append("RIVET_DATABASE_URL must use PostgreSQL")
    if os.getenv("RIVET_COMPOSE_DATABASE") == "true" and not re.fullmatch(
        r"[0-9a-fA-F]{64,}", os.getenv("POSTGRES_PASSWORD", "")
    ):
        errors.append(
            "POSTGRES_PASSWORD must contain at least 64 hexadecimal characters for the bundled Compose deployment"
        )
    if not allowed_origins():
        errors.append("RIVET_ALLOWED_ORIGINS must contain at least one HTTPS origin")
    if not allowed_hosts():
        errors.append("RIVET_ALLOWED_HOSTS must contain at least one hostname")
    for origin in allowed_origins():
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or "*" in origin
        ):
            errors.append(
                "RIVET_ALLOWED_ORIGINS must contain exact HTTPS origins, without paths or wildcards"
            )
            break
    if any("*" in host or "/" in host or ":" in host for host in allowed_hosts()):
        errors.append(
            "RIVET_ALLOWED_HOSTS must contain exact hostnames without schemes, ports, or wildcards"
        )
    if errors:
        # Only setting names and requirements: never echo secret values.
        raise RuntimeError("Invalid production configuration: " + "; ".join(errors))
