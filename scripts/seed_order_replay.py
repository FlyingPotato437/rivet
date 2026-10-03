#!/usr/bin/env python3
"""Create or resume one synthetic order via the public local API. Never reset data."""

import argparse
import hashlib
import json
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples/order-replay"
BASE = "http://127.0.0.1:8787/api"


def request(path, data=None, key=None):
    headers = {}
    if data is not None:
        headers = {
            "Content-Type": "application/json",
            "X-Rivet-Client": "workspace",
            "Idempotency-Key": key or str(uuid.uuid4()),
        }
        data = json.dumps(data).encode()
    with urlopen(
        Request(BASE + path, data=data, headers=headers), timeout=90
    ) as response:
        return json.load(response)


def upload(project_id, file, role, revision):
    raw = file.read_bytes()
    boundary = "rivet" + uuid.uuid4().hex
    parts = []
    for field, value in {
        "kind": {"drawing": "approval_drawing", "markup": "markups"}.get(role, role),
        "revision_label": revision or "",
    }.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n{value}\r\n'.encode()
        )
    parts += [
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file.name}"\r\nContent-Type: application/pdf\r\n\r\n'.encode(),
        raw,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    headers = {
        "Content-Type": "multipart/form-data; boundary=" + boundary,
        "X-Rivet-Client": "workspace",
        "Idempotency-Key": "replay:" + hashlib.sha256(raw).hexdigest(),
    }
    with urlopen(
        Request(
            BASE + f"/projects/{project_id}/documents",
            data=b"".join(parts),
            headers=headers,
        ),
        timeout=30,
    ) as response:
        return json.load(response)


def main():
    args = argparse.ArgumentParser()
    args.add_argument(
        "--revision-c",
        action="store_true",
        help="Also upload the partially corrected Rev C drawing",
    )
    options = args.parse_args()
    expected = json.loads((FIXTURES / "expectations.json").read_text())
    existing = next(
        (
            o
            for o in request("/orders")["orders"]
            if o["number"] == expected["number"] and o["synthetic"]
        ),
        None,
    )
    data = (
        request("/orders/" + existing["id"])
        if existing
        else request(
            "/orders",
            {
                k: expected[k]
                for k in ("title", "customer", "number", "synthetic", "category")
            },
            "larkspur-order-replay-v1",
        )
    )
    order_id, project_id = data["order"]["id"], data["order"]["project_id"]
    for doc in expected["documents"]:
        if doc["revision_label"] == "C" and not options.revision_c:
            continue
        if doc["file"] not in {d["name"] for d in data["documents"]}:
            upload(
                project_id, FIXTURES / doc["file"], doc["role"], doc["revision_label"]
            )
        deadline = time.monotonic() + 90
        while True:
            data = request("/orders/" + order_id)
            if not any(
                d["state"] in ("queued", "processing", "parsing")
                for d in data["documents"]
            ):
                break
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "The worker has not finished reading the replay documents. Start it and rerun this script."
                )
            time.sleep(0.5)
    # Resolve only the fixture's explicit identity/exception setup, leaving all
    # engineering changes and comment responses as decisions for the demo user.
    for _ in range(8):
        action = next(
            (
                a
                for a in data["actions"]
                if a["status"] == "pending"
                and (a["type"] == "alias" or "accept_exception_document" in a["after"])
            ),
            None,
        )
        if not action:
            break
        data = request(
            f"/orders/{order_id}/actions/{action['id']}/accept",
            {
                "expected_version": data["order"]["version"],
                "reason": "Synthetic replay setup: verified the explicit identity or contractual exception in the fictional fixture.",
            },
        )
    print(
        json.dumps(
            {
                "url": "http://127.0.0.1:5178/#order/" + order_id,
                "order": data["order"],
                "documents": len(data["documents"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
