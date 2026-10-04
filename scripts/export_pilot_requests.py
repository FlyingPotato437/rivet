"""Export pilot inquiries for the operator. Requires server/database access."""

import argparse
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select

from backend.storage.db import Session
from backend.storage.models import PilotRequest


def spreadsheet_text(value):
    text = str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    # Do not overwrite existing files, and keep the contact list operator-only.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", newline="", encoding="utf-8-sig") as output:
        writer = csv.writer(output)
        writer.writerow(
            [
                "Received (UTC)",
                "Name",
                "Email",
                "Company",
                "Workflow",
                "Contact consent",
            ]
        )
        with Session() as session:
            for inquiry in session.scalars(
                select(PilotRequest).order_by(PilotRequest.created_at)
            ):
                writer.writerow(
                    [
                        spreadsheet_text(value)
                        for value in (
                            inquiry.created_at.isoformat(),
                            inquiry.name,
                            inquiry.email,
                            inquiry.company,
                            inquiry.workflow,
                            "Yes" if inquiry.consent else "No",
                        )
                    ]
                )
    print(f"Saved pilot requests to {args.output}")


if __name__ == "__main__":
    main()
