"""Validate production settings before starting any long-running service."""

import os
import sys

from backend.config import validate_configuration


def main():
    validate_configuration()
    role = sys.argv[1] if len(sys.argv) > 1 else "api"
    commands = {
        "api": [
            sys.executable, "-m", "uvicorn", "backend.api.main:app",
            "--host", "0.0.0.0", "--port", "8787",
            # Caddy is the only published ingress. No client IP is trusted for
            # authentication, and disabling forwarded headers avoids spoofing.
            "--no-proxy-headers",
        ],
        "worker": [sys.executable, "-m", "backend.worker"],
        "migrate": [sys.executable, "-m", "alembic", "upgrade", "head"],
    }
    if role not in commands:
        raise SystemExit("Expected api, worker, or migrate.")
    os.execv(sys.executable, commands[role])


if __name__ == "__main__":
    main()
