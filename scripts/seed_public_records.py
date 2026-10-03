"""Import public QA excerpts through the running app; preserve existing orders.

Run prepare_public_documents.py first. Rerunning does not duplicate imports.
"""

import json
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1] / ".data" / "validation"


def main():
    with httpx.Client(
        base_url="http://127.0.0.1:8787/api",
        headers={"X-Rivet-Client": "workspace"},
        timeout=40,
    ) as client:
        response = client.get("/records")
        if response.status_code == 401:
            raise SystemExit(
                "Clerk is enabled. Use: uv run python -m scripts.setup_demo"
            )
        response.raise_for_status()
        existing = response.json()["orders"]
        for package in json.loads((ROOT / "manifest.json").read_text()):
            short = package["name"].split("-")[0]
            number = "PUBLIC-" + short.upper()
            order = next((o for o in existing if o["number"] == number), None)
            if not order:
                response = client.post(
                    "/orders",
                    headers={"Idempotency-Key": "public-qa:" + short},
                    json={
                        "title": (
                            "Alachua · public drawing review"
                            if short == "alachua"
                            else "AVTA · public scanned review"
                        ),
                        "customer": "Public-document validation",
                        "number": number,
                        "category": "Switchgear · public-source excerpt",
                        "synthetic": True,
                    },
                )
                response.raise_for_status()
                order = response.json()["order"]
            name = (
                short.upper()
                + " - original PDF pages "
                + ",".join(map(str, package["pages"]))
                + ".pdf"
            )
            record = client.get(f"/orders/{order['id']}/record").json()
            if name not in {d["name"] for d in record["documents"]}:
                response = client.post(
                    f"/projects/{order['project_id']}/documents",
                    headers={"Idempotency-Key": str(uuid4())},
                    files={
                        "file": (
                            name,
                            (ROOT / package["excerpt"]).read_bytes(),
                            "application/pdf",
                        )
                    },
                    data={"kind": "markups", "revision_label": "Public-source excerpt"},
                )
                response.raise_for_status()
            for _ in range(60):
                record = client.get(f"/orders/{order['id']}/record").json()
                if all(
                    d["state"] not in {"queued", "parsing", "processing"}
                    for d in record["documents"]
                ):
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Import did not finish; check the worker")
            if any(d["state"] == "failed" for d in record["documents"]):
                raise RuntimeError("Document import failed")
            print(
                json.dumps(
                    {
                        "number": number,
                        "id": order["id"],
                        "comments": len(record["comments"]),
                        "url": "http://127.0.0.1:5178/#order/" + order["id"],
                    }
                )
            )


if __name__ == "__main__":
    main()
