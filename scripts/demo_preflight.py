#!/usr/bin/env python3
"""Check the local Outlook demo; optionally start only its missing services.

The default is read-only: public health GETs, local configuration inspection and
process discovery. It never syncs mail, imports fixtures, changes a record, grants
permissions or sends an email. --start detaches missing local development services
and saves their logs/PIDs, without replacing a service already using a port.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
JOB = "DEMO-OUTLOOK-001"
MAX_RESPONSE = 32 * 1024 * 1024


def environment():
    """Match backend dotenv precedence; values never enter the report or logs."""
    result = dict(os.environ)
    try:
        from dotenv import dotenv_values
    except ImportError:
        return result
    for filename in ("apps/web/.env.local", "apps/web/.env", ".env"):
        for key, value in dotenv_values(ROOT / filename).items():
            if value is not None:
                result.setdefault(key, value)
    return result


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def local_context():
    context = ssl.create_default_context()
    # Python's CA store may differ from the macOS trust store. Add only the
    # already-installed Office development CA; never disable TLS verification.
    office_ca = Path.home() / ".office-addin-dev-certs/ca.crt"
    if office_ca.is_file():
        context.load_verify_locations(cafile=str(office_ca))
    return context


def fetch(url, timeout, *, context=None, accept="application/json"):
    try:
        request = Request(url, headers={"Accept": accept})
        with urlopen(request, timeout=timeout, context=context) as response:
            content = response.read(MAX_RESPONSE + 1)
            if len(content) > MAX_RESPONSE:
                return response.status, None, "Response exceeds the demo check limit."
            try:
                data = json.loads(content)
            except (ValueError, UnicodeDecodeError):
                data = None
            return response.status, data, ""
    except HTTPError as exc:
        return exc.code, None, f"HTTP {exc.code}"
    except (URLError, TimeoutError, OSError, ssl.SSLError):
        # Exception messages may contain signed URLs; never include them.
        return None, None, "Unreachable or local TLS certificate is not trusted."


def worker_pids():
    """Find this checkout's workers without reading or printing their environment."""
    try:
        listing = subprocess.check_output(
            ["ps", "-axo", "pid=,args="], text=True, stderr=subprocess.DEVNULL
        )
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in listing.splitlines():
        try:
            pid_text, command = line.strip().split(None, 1)
            args = shlex.split(command)
            marker = args.index("-m")
            if args[marker + 1] != "backend.worker":
                continue
            pid = int(pid_text)
            proc_cwd = Path(f"/proc/{pid}/cwd")
            if proc_cwd.exists():
                cwd = proc_cwd.resolve()
            else:
                output = subprocess.check_output(
                    ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
                cwd = next(
                    (Path(part[1:]).resolve() for part in output.splitlines() if part.startswith("n")),
                    None,
                )
            if cwd == ROOT:
                found.append(pid)
        except (ValueError, IndexError, OSError, subprocess.SubprocessError):
            continue
    return found


def read_bindings(path, job):
    try:
        data = json.loads(path.read_text())
        jobs = [
            org.get("jobs", {}).get(job)
            for org in data.get("organizations", {}).values()
            if isinstance(org, dict) and job in org.get("jobs", {})
        ]
        if len(jobs) != 1:
            return [], "Expected one explicitly configured demo job."
        bindings = jobs[0].get("connectors", [])
        if not isinstance(bindings, list) or not all(isinstance(item, dict) for item in bindings):
            return [], "Connected source configuration is invalid."
        if not {"erp", "sharepoint", "vault"}.issubset({item.get("kind") for item in bindings}):
            return [], "The demo requires explicit ERP, SharePoint and vault bindings."
        return bindings, ""
    except (OSError, ValueError, TypeError, AttributeError):
        return [], "Connected source configuration is missing or invalid."


def start_service(name, command, env):
    runtime = ROOT / ".data/runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    log_path = runtime / f"demo-{name}.log"
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    (runtime / f"demo-{name}.pid").write_text(str(process.pid) + "\n")
    return {"service": name, "pid": process.pid, "log": str(log_path.relative_to(ROOT))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="store_true", help="Start missing local services; never restart an existing process.")
    parser.add_argument("--json", action="store_true", help="Print a concise machine-readable report.")
    parser.add_argument("--job", default=JOB)
    parser.add_argument("--sources", type=Path, default=ROOT / ".data/connected-demo")
    parser.add_argument("--config", type=Path, help="Use the existing connected-source binding without modifying it.")
    parser.add_argument("--timeout", type=int, default=5, choices=range(1, 16), metavar="1-15")
    args = parser.parse_args()
    if not args.sources.is_absolute():
        args.sources = ROOT / args.sources
    env = environment()
    config = args.config or Path(env.get("RIVET_CONNECTED_CONTEXT_CONFIG", str(args.sources / "connections.json")))
    if not config.is_absolute():
        config = ROOT / config
    bindings, binding_error = read_bindings(config, args.job)
    python = ROOT / ".venv/bin/python"
    checks = []
    started = []

    def add(name, state, detail):
        checks.append({"check": name, "state": state, "detail": detail})

    if args.start and env.get("RIVET_ENV", "development").lower() == "production":
        parser.error("--start is for the local development demo, not a production deployment.")
    if args.start and binding_error:
        parser.error(binding_error + " Existing source configuration was left unchanged.")

    add("python", "ok" if python.is_file() else "fail", "Project virtual environment found." if python.is_file() else "Run uv sync before starting the demo.")
    if binding_error:
        add("source_bindings", "fail", binding_error + " Do not regenerate over the existing live configuration.")
    else:
        modes = sorted(f"{item.get('kind', 'unknown')}: {item.get('mode', 'live')}" for item in bindings)
        add("source_bindings", "ok", "; ".join(modes) + ". Configuration inspection only; live access is verified in the rehearsal.")
        # Provide the existing path to newly started children. No file is changed.
        env["RIVET_CONNECTED_CONTEXT_CONFIG"] = str(config)

    source_services = []
    for kind, port in (("erp", 8791), ("sharepoint", 8792), ("vault", 8793)):
        active = any(
            item.get("kind") == kind
            and item.get("mode") == "local_demo"
            and item.get("base_url", "").rstrip("/") in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
            for item in bindings
        )
        if active:
            source_services.append((kind, port))
    if args.start and python.is_file():
        if not binding_error and (args.sources / "catalog.json").is_file():
            for kind, port in source_services:
                if not port_open(port):
                    child_env = dict(env, RIVET_DEMO_SOURCE_ROOT=str(args.sources.resolve()), RIVET_DEMO_SOURCE_KIND=kind)
                    started.append(start_service(kind, [str(python), "-m", "uvicorn", "scripts.connected_demo_sources:app", "--host", "127.0.0.1", "--port", str(port)], child_env))
        if not port_open(8787):
            started.append(start_service("api", [str(python), "-m", "uvicorn", "backend.api.main:app", "--host", "127.0.0.1", "--port", "8787"], env))
        if not worker_pids():
            started.append(start_service("worker", [str(python), "-m", "backend.worker"], env))
        if not port_open(5179):
            cert_env = dict(env)
            try:
                from dotenv import dotenv_values
                # Vite owns mode-specific values; inspect only to avoid starting an HTTP fallback.
                for filename in ("apps/web/.env.outlook.local", "apps/web/.env.outlook"):
                    for key, value in dotenv_values(ROOT / filename).items():
                        if value is not None:
                            cert_env.setdefault(key, value)
            except ImportError:
                pass
            certs = [cert_env.get("RIVET_OUTLOOK_HTTPS_KEY", ""), cert_env.get("RIVET_OUTLOOK_HTTPS_CERT", "")]
            if all(value and Path(value).expanduser().is_file() for value in certs) and (ROOT / "node_modules/.bin/vite").exists():
                started.append(start_service("workspace", ["npm", "run", "dev", "--", "--mode", "outlook", "--port", "5179"], env))
            else:
                add("workspace_start", "fail", "Existing HTTPS certificate paths and installed frontend dependencies are required; no trust settings were changed.")
        # Short bounded startup grace. Services stay detached after this script exits.
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and not all(port_open(port) for port in [8787, 5179, *[p for _, p in source_services]]):
            time.sleep(0.3)

    status, health, error = fetch("http://127.0.0.1:8787/api/health", args.timeout)
    api_ok = status == 200 and isinstance(health, dict) and health.get("status") == "ok"
    add("api_database", "ok" if api_ok else "fail", "API and database health passed." if api_ok else error or f"Unexpected health response (HTTP {status}).")
    if api_ok:
        add("reply_model", "ok" if health.get("assistant_configured") else "fail", "Reply model is configured." if health.get("assistant_configured") else "A reply model credential is not configured.")
        add("authentication", "ok" if health.get("mode") == "clerk" else "warn", "Clerk authentication is active." if health.get("mode") == "clerk" else "This API is not using Clerk; use the authenticated demo organization before filming.")
    status, _, error = fetch("https://localhost:5179/", args.timeout, context=local_context(), accept="text/html")
    add("https_workspace", "ok" if status == 200 else "fail", "HTTPS workspace responds; browser sign-in still needs checking." if status == 200 else error or f"Unexpected workspace response (HTTP {status}).")
    workers = worker_pids()
    add("worker", "ok" if len(workers) == 1 else "fail", "One worker process is running for this checkout; mail progress must still be checked in the UI." if len(workers) == 1 else f"Found {len(workers)} workers for this checkout; resolve missing or duplicate workers before rehearsing.")
    for kind, port in source_services:
        status, body, error = fetch(f"http://127.0.0.1:{port}/v1/orders/{quote(args.job, safe='')}/resources", args.timeout)
        valid = status == 200 and isinstance(body, dict) and body.get("kind") == kind and body.get("job_number") == args.job and isinstance(body.get("resources"), list) and bool(body["resources"])
        count = len(body["resources"]) if valid else 0
        add(f"local_{kind}", "ok" if valid else "fail", f"Read-only local demo responds with {count} source file(s)." if valid else error or "Local source did not return this demo job.")
    pdf = ROOT / "output/pdf/DEMO-OUTLOOK-001-E-10-Rev-C-Returned.pdf"
    add("returned_pdf", "ok" if pdf.is_file() and pdf.stat().st_size > 0 else "warn", "Returned drawing PDF is available for the fresh test email." if pdf.is_file() and pdf.stat().st_size > 0 else "Returned drawing PDF is not present in output/pdf yet.")
    passed = not any(item["state"] == "fail" for item in checks)
    result = {
        "passed": passed,
        "mode": "start_missing" if args.start else "read_only",
        "checks": checks,
        "started": started,
        "next": "Open https://localhost:5179/#work in the signed-in founder organization, then run a fresh email rehearsal.",
        "limitations": "Does not verify mailbox permissions, SharePoint access, new mail capture, draft generation, citations or sent-reply learning. No email or record is created by this check.",
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for item in checks:
            print(f"[{item['state'].upper()}] {item['check']}: {item['detail']}")
        for service in started:
            print(f"[STARTED] {service['service']} (PID {service['pid']}), log: {service['log']}")
        print("\n" + result["next"])
        print(result["limitations"])
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
