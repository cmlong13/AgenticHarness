from __future__ import annotations

from loanflow.collateral import Collateral
from loanflow.income import DebtObligation, IncomeSource
from loanflow.primitives import Money
from loanflow.risk_profile import build_risk_profile
from loanflow.underwriting_engine import UnderwritingEngine
from loanflow.underwriting_rules import RuleOutcome, RuleResult


def _good_profile(default_thresholds):
    return build_risk_profile(
        income_sources=[IncomeSource(label="salary", monthly_amount=Money.from_dollars(10_000))],
        debt_obligations=[DebtObligation(label="debt", monthly_payment=Money.from_dollars(500))],
        loan_amount=Money.from_dollars(150_000),
        collateral=Collateral(description="home", appraised_value=Money.from_dollars(300_000)),
        credit_score=780,
        thresholds=default_thresholds,
    )


def test_evaluate_runs_every_registered_rule(default_thresholds) -> None:
    engine = UnderwritingEngine()
    results = engine.evaluate(_good_profile(default_thresholds), default_thresholds)
    assert {r.rule_name for r in results} == {"max_dti_rule", "max_ltv_rule", "min_credit_score_rule"}
    assert all(r.outcome is RuleOutcome.PASS for r in results)


def test_evaluate_uses_a_custom_rule_set_when_supplied(default_thresholds) -> None:
    def always_borderline(profile, thresholds):
        return RuleResult("always_borderline", RuleOutcome.BORDERLINE, "forced for test")

    engine = UnderwritingEngine(rules=(always_borderline,))
    results = engine.evaluate(_good_profile(default_thresholds), default_thresholds)
    assert len(results) == 1
    assert results[0].rule_name == "always_borderline"
