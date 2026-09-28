#!/usr/bin/env python3
"""Launch the local API, worker, and UI. Ctrl-C stops every child process."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
python = ROOT / ".venv/bin/python"
if not python.exists():
    print("Run uv sync and npm install first. See README.md.")
    sys.exit(1)
processes = []
try:
    commands = [
        [
            str(python),
            "-m",
            "uvicorn",
            "backend.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8787",
        ],
        [str(python), "-m", "backend.worker"],
        ["npm", "run", "dev"],
    ]
    for args in commands:
        processes.append(subprocess.Popen(args, cwd=ROOT, start_new_session=True))
    print("\nRivet: http://127.0.0.1:5178\n", flush=True)
    while all(p.poll() is None for p in processes):
        time.sleep(1)
except KeyboardInterrupt:
    print("\nStopping Rivet…", flush=True)
finally:
    for p in processes:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for p in processes:
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
