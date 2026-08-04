"""Minimal argparse CLI: run a single application through the pipeline.

Educational demo only -- not legally compliant, not financially
authoritative, and not for real personal information.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from loanflow.applicants import Applicant
from loanflow.audit import AuditLog
from loanflow.collateral import Collateral
from loanflow.config import DEFAULT_THRESHOLDS
from loanflow.config_loader import load_thresholds
from loanflow.errors import LoanflowError
from loanflow.explanations import explain
from loanflow.income import DebtObligation, IncomeSource
from loanflow.pipeline import run_pipeline
from loanflow.primitives import Money, SystemClock


def _build_pipeline_inputs(payload: dict):
    applicant = Applicant(
        applicant_id=payload["applicant"]["applicant_id"],
        full_name=payload["applicant"]["full_name"],
        years_employed=payload["applicant"].get("years_employed", 0),
    )
    collateral = Collateral(
        description=payload["collateral"]["description"],
        appraised_value=Money.from_dollars(payload["collateral"]["appraised_value"]),
    )
    income_sources = [
        IncomeSource(label=i["label"], monthly_amount=Money.from_dollars(i["monthly_amount"]))
        for i in payload["income_sources"]
    ]
    debt_obligations = [
        DebtObligation(label=d["label"], monthly_payment=Money.from_dollars(d["monthly_payment"]))
        for d in payload["debt_obligations"]
    ]
    requested_amount = Money.from_dollars(payload["requested_amount"])
    credit_score = int(payload["credit_score"])
    return applicant, collateral, income_sources, debt_obligations, requested_amount, credit_score


def _cmd_decide(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.application_file).read_text(encoding="utf-8"))
    (applicant, collateral, income_sources, debt_obligations,
     requested_amount, credit_score) = _build_pipeline_inputs(payload)
    thresholds = load_thresholds(args.config) if args.config else DEFAULT_THRESHOLDS
    audit_log = AuditLog()
    result = run_pipeline(
        applicant=applicant,
        application_id=payload["application_id"],
        requested_amount=requested_amount,
        collateral=collateral,
        income_sources=income_sources,
        debt_obligations=debt_obligations,
        credit_score=credit_score,
        thresholds=thresholds,
        clock=SystemClock(),
        audit_log=audit_log,
    )
    print(explain(result.decision, result.risk_profile.overall_band))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loanflow",
        description=(
            "Educational loan-underwriting demo. Not legally compliant, not "
            "financially authoritative, and not for real personal information."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    decide = subparsers.add_parser("decide", help="Run one application through the pipeline")
    decide.add_argument("application_file", help="Path to a JSON application payload")
    decide.add_argument("--config", help="Path to a thresholds JSON file", default=None)
    decide.set_defaults(func=_cmd_decide)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except LoanflowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"error: invalid application file: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
