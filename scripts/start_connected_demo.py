#!/usr/bin/env python3
"""Run three loopback source simulators. The API/UI/worker run separately."""

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", default=".data/connected-demo")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source_root = Path(args.sources).resolve()
    if not (source_root / "catalog.json").is_file():
        parser.error("Generate sources with scripts/build_connected_demo.py first.")
    # Fail before starting any child if another demo already owns a port.
    for port in (8791, 8792, 8793):
        with socket.socket() as check:
            try:
                check.bind(("127.0.0.1", port))
            except OSError:
                parser.error(
                    f"Port {port} is in use. Keep the existing source services or stop them first."
                )
    children = []

    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        for kind, port in (("erp", 8791), ("sharepoint", 8792), ("vault", 8793)):
            env = dict(
                os.environ,
                RIVET_DEMO_SOURCE_ROOT=str(source_root),
                RIVET_DEMO_SOURCE_KIND=kind,
            )
            children.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "scripts.connected_demo_sources:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                    ],
                    cwd=root,
                    env=env,
                )
            )
        print(
            "Fictional source services running on 127.0.0.1:8791–8793. Ctrl-C stops them.",
            flush=True,
        )
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    sys.exit(main())
