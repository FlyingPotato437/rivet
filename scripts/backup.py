"""Create a local, timestamped backup while the API and worker are stopped."""

from pathlib import Path
from datetime import datetime, timezone
import subprocess, tarfile, hashlib, json, os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.storage.db import ROOT, DATABASE_URL, BLOBS

folder = ROOT / ".data/backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
folder.mkdir(parents=True, exist_ok=False)
folder.chmod(0o700)
url = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")
# Pass credentials through environment instead of command-line arguments if configured.
from urllib.parse import urlparse

parsed = urlparse(url)
env = {
    **os.environ,
    "PGHOST": parsed.hostname or "localhost",
    "PGPORT": str(parsed.port or 5432),
    "PGDATABASE": parsed.path.lstrip("/"),
}
if parsed.username:
    env["PGUSER"] = parsed.username
if parsed.password:
    env["PGPASSWORD"] = parsed.password
subprocess.run(
    ["pg_dump", "--format=custom", "--file", str(folder / "database.dump")],
    env=env,
    check=True,
)
with tarfile.open(folder / "blobs.tar.gz", "w:gz") as archive:
    archive.add(BLOBS, arcname="blobs")
manifest = {
    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
    for path in BLOBS.iterdir()
    if path.is_file()
}
(folder / "manifest.json").write_text(json.dumps(manifest, indent=2))
for path in folder.iterdir():
    path.chmod(0o600)
print(folder)
