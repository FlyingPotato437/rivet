"""Private paired replay; no training, customer writes or email delivery."""

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.company_context import customers as _customers  # noqa: F401
from backend.company_context import models as _company_models  # noqa: F401
from backend.domain.service import digest
from backend.identity import Identity, identity_scope
from backend.improvement.replay import (
    RUBRIC,
    prepare_cases,
    run_cases,
    score_review,
)
from backend.reply_assistant.service import INSTRUCTIONS, model_reply
from backend.storage.db import Session


def private_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Refuse overwrite, including accidental symlink targets.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(value, handle, indent=2, default=str)
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser(
        "prepare", help="Export frozen, explicitly approved evaluation cases locally"
    )
    prepare.add_argument("--organization-id", type=UUID, required=True)
    prepare.add_argument("--reviewer", required=True)
    prepare.add_argument("--limit", type=int, default=20)
    prepare.add_argument("--output", type=Path, required=True)
    run = sub.add_parser(
        "run", help="Compare memory on/off through the configured AI provider"
    )
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument(
        "--allow-provider-calls",
        action="store_true",
        help="Explicitly allow approved private examples to reach the configured AI provider",
    )
    score = sub.add_parser("score", help="Score human rubric grades; never deploys")
    score.add_argument("--run", type=Path, required=True)
    score.add_argument("--grades", type=Path, required=True)
    score.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        with (
            identity_scope(
                Identity(str(args.organization_id), args.reviewer, "org:admin")
            ),
            Session() as session,
        ):
            plan = prepare_cases(session, limit=args.limit)
        private_write(args.output, plan)
        print(
            f"Saved {len(plan['cases'])} eligible cases; {len(plan['skipped'])} skipped. No provider calls made."
        )
    elif args.command == "run":
        if not args.allow_provider_calls:
            parser.error(
                "Run requires --allow-provider-calls. Preparing and scoring stay local."
            )
        # Refresh consent and freshness from the database immediately before replay.
        old = json.loads(args.plan.read_text())
        with (
            identity_scope(
                Identity(
                    str(UUID(old["organization_id"])),
                    "evaluation-operator",
                    "org:admin",
                )
            ),
            Session() as session,
        ):
            current = prepare_cases(session, limit=100)
        selected = {c["case_id"] for c in old["cases"]}
        current["cases"] = [c for c in current["cases"] if c["case_id"] in selected]
        if {c["case_id"] for c in current["cases"]} != selected or not selected:
            parser.error(
                "Evaluation consent or evidence changed, or no cases exist. Prepare a new plan."
            )
        report = run_cases(
            current,
            model_reply,
            model=os.environ.get("OPENAI_MODEL", ""),
            prompt_hash=digest(INSTRUCTIONS),
        )
        private_write(args.output_dir / "run-private.json", report)
        private_write(args.output_dir / "review-blinded.json", report["review"])
        grades = [
            {
                "case_id": pair["case_id"],
                "variant": label,
                "reviewer": "",
                "reason": "",
                **{criterion: None for criterion in RUBRIC},
            }
            for pair in report["review"]["pairs"]
            for label in ("A", "B")
        ]
        private_write(args.output_dir / "grades.json", grades)
        print(
            "Saved paired outputs and a blank rubric. Human evaluation is required; no improvement is claimed."
        )
    else:
        report = score_review(
            json.loads(args.run.read_text()), json.loads(args.grades.read_text())
        )
        private_write(args.output, report)
        print(report["status"] + ". No model or release was deployed.")


if __name__ == "__main__":
    main()
