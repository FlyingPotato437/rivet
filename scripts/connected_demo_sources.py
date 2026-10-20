"""Independent read-only local source services. No Rivet database access.

Run one instance for each system using RIVET_DEMO_SOURCE_KIND and
RIVET_DEMO_SOURCE_ROOT. Edit catalog.json to exercise revisions or missing data.
"""

import base64
import hashlib
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

app = FastAPI(title="Rivet local source simulator — fictional records")


def root():
    return Path(os.environ["RIVET_DEMO_SOURCE_ROOT"]).resolve()


def catalog():
    return json.loads((root() / "catalog.json").read_text())


@app.get("/health")
def health():
    return {"mode": "local_demo", "kind": os.environ["RIVET_DEMO_SOURCE_KIND"]}


@app.get("/v1/orders/{job_number}/resources")
def resources(job_number: str):
    kind = os.environ["RIVET_DEMO_SOURCE_KIND"]
    items = catalog().get("jobs", {}).get(job_number, {}).get(kind)
    if items is None:
        raise HTTPException(404, "No matching order")
    result = []
    for item in items:
        path = (root() / item["file"]).resolve()
        if not path.is_relative_to(root()) or not path.is_file():
            raise HTTPException(503, "Source original unavailable")
        payload = path.read_bytes()
        result.append(
            {k: v for k, v in item.items() if k != "file"}
            | {
                "job_number": job_number,
                "content_base64": base64.b64encode(payload).decode(),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return {
        "job_number": job_number,
        "kind": kind,
        "resources": result,
        "mode": "local_demo",
    }


@app.get("/originals/{filename}")
def original(filename: str):
    kind = os.environ["RIVET_DEMO_SOURCE_KIND"]
    available = {
        item["file"]
        for job in catalog().get("jobs", {}).values()
        for item in job.get(kind, [])
    }
    if filename not in available:
        raise HTTPException(404)
    return FileResponse(root() / filename)
