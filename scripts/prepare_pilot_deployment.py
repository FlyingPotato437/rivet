"""Stage only built public assets and runtime source for a private pilot release.

No accounts, network requests, credentials, customer documents or local databases
are read. Existing releases are never overwritten. Build both frontends first;
the Outlook build must use VITE_API_URL='' for its same-origin session cookie.
"""

import argparse
import hashlib
import ipaddress
import json
import re
import shutil
import tarfile
from pathlib import Path
from urllib.parse import urlsplit

from scripts.build_outlook_manifest import manifest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_FILES = (
    "Dockerfile", ".dockerignore", "pyproject.toml", "uv.lock", "alembic.ini",
    "compose.production.yml", "scripts/container_entrypoint.py", "deploy/Caddyfile",
)
RUNTIME_DIRS = ("backend", "migrations")


def public_origin(value):
    url = urlsplit(value)
    host = (url.hostname or "").lower()
    if (
        url.scheme != "https" or not host or url.username or url.password
        or url.path not in ("", "/") or url.query or url.fragment
        or url.port not in (None, 443) or "*" in host
        or host in {"localhost", "localhost.localdomain"}
        or host.endswith((".localhost", ".local", ".internal"))
    ):
        raise ValueError("Use a public HTTPS origin without a path or credentials.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise ValueError("Use a public hostname.") from None
    else:
        if not address.is_global:
            raise ValueError("Private and loopback origins cannot be deployed.")
    return "https://" + url.netloc.lower().removesuffix(":443")


def hosting_config(surface, api_origin):
    config = json.loads((ROOT / f"apps/web/vercel.{surface}.json").read_text())
    # These folders contain finished static assets, not a repository build.
    for key in ("installCommand", "buildCommand", "outputDirectory"):
        config.pop(key, None)
    config["framework"] = None
    config["buildCommand"] = ""
    if surface == "outlook":
        config["rewrites"] = [{
            "source": "/api/:path*", "destination": api_origin + "/api/:path*",
        }]
    return config


def validate_build(source):
    # Vite can load an ignored development .env.local even in a production build.
    # Refuse to publish that instance or any accidentally bundled server secret.
    for path in source.rglob("*.js"):
        data = path.read_text(errors="replace")
        if re.search(r"(?:pk|sk)_test_[A-Za-z0-9]+|sk_live_[A-Za-z0-9]+", data):
            raise ValueError("Rebuild with the production public key; development keys and server secrets cannot be published.")


def runtime_paths(root):
    for name in RUNTIME_FILES:
        yield root / name
    for name in RUNTIME_DIRS:
        for path in sorted((root / name).rglob("*")):
            if (
                path.is_file() and not path.is_symlink()
                and "__pycache__" not in path.parts
                and not any(part.startswith(".") for part in path.relative_to(root).parts)
                and path.suffix not in {".pyc", ".pyo"}
            ):
                yield path


def prepare(output, workspace_origin, api_origin, outlook_origin):
    workspace_origin, api_origin, outlook_origin = map(
        public_origin, (workspace_origin, api_origin, outlook_origin)
    )
    if len({workspace_origin, api_origin, outlook_origin}) != 3:
        raise ValueError("Workspace, API and Outlook require separate origins.")
    builds = {surface: ROOT / f"apps/web/dist-{surface}" for surface in ("workspace", "outlook")}
    for surface, source in builds.items():
        entry = source / ("index.html" if surface == "workspace" else "outlook/taskpane.html")
        if not entry.is_file():
            raise ValueError(f"Build the {surface} frontend before staging a release.")
    output = Path(output)
    if output.resolve().is_relative_to(ROOT):
        raise ValueError("Stage outside the checkout so hosting cannot inherit the public site's monorepo configuration.")
    for source in builds.values():
        validate_build(source)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    for surface, source in builds.items():
        destination = output / surface
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".env*", "*.map", ".vercel"))
        (destination / "vercel.json").write_text(
            json.dumps(hosting_config(surface, api_origin), indent=2) + "\n"
        )
    # Never alter the development manifest used by the running local demo.
    for surface in builds:
        target = output / surface / "outlook/manifest.xml"
        if surface == "outlook" or target.exists():
            target.write_text(manifest(outlook_origin))
    template = (ROOT / "deploy/.env.example").read_text()
    template = template.replace("api.your-domain.com", urlsplit(api_origin).netloc)
    template = template.replace("https://app.your-domain.com", workspace_origin + "," + outlook_origin)
    template = template.replace("RIVET_WORKSPACE_URL=\n", "RIVET_WORKSPACE_URL=" + workspace_origin + "\n")
    (output / "server.env.template").write_text(template)
    (output / "server.env.template").chmod(0o600)
    source_hashes = {}
    with tarfile.open(output / "backend-source.tar.gz", "w:gz") as archive:
        for path in runtime_paths(ROOT):
            name = path.relative_to(ROOT).as_posix()
            if path.is_symlink():
                raise ValueError("Symlinks are not allowed in a runtime release.")
            archive.add(path, arcname=name, recursive=False)
            source_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    info = {
        "workspace_origin": workspace_origin, "api_origin": api_origin,
        "outlook_origin": outlook_origin, "runtime_sha256": source_hashes,
        "workspace_enabled": False, "automation_enabled": False,
        "customer_data_included": False,
        "outlook_build_requirement": "VITE_API_URL='' and VITE_WORKSPACE_URL=" + workspace_origin,
        "status": "staged; hosting, production credentials and acceptance tests still required",
    }
    (output / "release.json").write_text(json.dumps(info, indent=2) + "\n")
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-origin", required=True)
    parser.add_argument("--api-origin", required=True)
    parser.add_argument("--outlook-origin", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(args.output, args.workspace_origin, args.api_origin, args.outlook_origin)
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + "\n")
    print(f"Private pilot release staged at {args.output}; no credentials or customer documents included.")


if __name__ == "__main__":
    main()
