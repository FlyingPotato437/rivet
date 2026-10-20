"""Read one real SAP sandbox order and save separate, credential-free evidence.

No database writes, company-order binding changes, or SAP write operations.
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.connected_context.providers import (
    ConnectorError,
    read_sap,
    sap_sandbox_binding,
)


def verify(sales_order, output, organization_id=None, transport=None):
    if organization_id:
        organization_id = str(UUID(organization_id))
    binding = sap_sandbox_binding(sales_order)
    job_number = "SAP-SANDBOX-" + str(sales_order)
    started = time.monotonic()
    [resource] = read_sap(binding, job_number, transport=transport)
    elapsed = round(time.monotonic() - started, 3)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / resource.name).write_bytes(resource.content())
    proof = {
        "checked_at": datetime.now(UTC).isoformat(),
        "status": "verified",
        "label": binding["label"],
        "api": "API_SALES_ORDER_SRV",
        "host": "sandbox.api.sap.com",
        "method": "GET",
        "sales_order": str(sales_order),
        "job_number": job_number,
        "elapsed_seconds": elapsed,
        "source_file": resource.name,
        "sha256": resource.sha256,
        "engineering_evidence": False,
        "boundary": (
            "Generic SAP sample data. Not DEMO-OUTLOOK-001 scope, not a customer "
            "ERP deployment, and not evidence of engineering approval."
        ),
    }
    (output / "verification.json").write_text(json.dumps(proof, indent=2) + "\n")
    if organization_id:
        configuration = {
            "organizations": {
                organization_id: {
                    "jobs": {
                        job_number: {
                            "auto_prepare_reply": False,
                            "connectors": [binding],
                        }
                    }
                }
            }
        }
        (output / "standalone-binding.example.json").write_text(
            json.dumps(configuration, indent=2) + "\n"
        )
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sales-order", default="1")
    parser.add_argument("--output", type=Path, default=ROOT / ".data/sap-sandbox")
    parser.add_argument(
        "--organization-id",
        help="Optionally save a standalone binding example; existing config stays unchanged.",
    )
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=False)
    try:
        if args.organization_id:
            UUID(args.organization_id)
        result = verify(args.sales_order, args.output, args.organization_id)
    except ConnectorError as exc:
        # Connector errors contain no provider body or credential.
        print(json.dumps({"status": "not_verified", "error": str(exc)}))
        return 1
    except (httpx.HTTPError, ValueError, TypeError, AttributeError, OverflowError):
        print(
            json.dumps(
                {
                    "status": "not_verified",
                    "error": "SAP could not be reached, its response was invalid, or the organization ID was invalid.",
                }
            )
        )
        return 1
    except OSError:
        print(
            json.dumps(
                {
                    "status": "not_verified",
                    "error": "The verification evidence could not be saved. Check the output path.",
                }
            )
        )
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
