from __future__ import annotations

from loanflow.collateral import Collateral
from loanflow.income import DebtObligation, IncomeSource
from loanflow.primitives import Money
from loanflow.risk_profile import build_risk_profile
from loanflow.underwriting_rules import RuleOutcome, max_dti_rule, max_ltv_rule, min_credit_score_rule


def _profile(default_thresholds, *, monthly_income, monthly_debt, loan_amount, appraised_value, credit_score):
    return build_risk_profile(
        income_sources=[IncomeSource(label="salary", monthly_amount=Money.from_dollars(monthly_income))],
        debt_obligations=[DebtObligation(label="debt", monthly_payment=Money.from_dollars(monthly_debt))],
        loan_amount=Money.from_dollars(loan_amount),
        collateral=Collateral(description="home", appraised_value=Money.from_dollars(appraised_value)),
        credit_score=credit_score,
        thresholds=default_thresholds,
    )


def test_max_dti_rule_passes_for_low_dti(default_thresholds) -> None:
    profile = _profile(default_thresholds, monthly_income=10_000, monthly_debt=500,
                        loan_amount=150_000, appraised_value=300_000, credit_score=780)
    assert max_dti_rule(profile, default_thresholds).outcome is RuleOutcome.PASS


def test_max_dti_rule_is_borderline_for_high_band_dti(default_thresholds) -> None:
    profile = _profile(default_thresholds, monthly_income=10_000, monthly_debt=4_400,
                        loan_amount=150_000, appraised_value=300_000, credit_score=780)
    assert max_dti_rule(profile, default_thresholds).outcome is RuleOutcome.BORDERLINE


def test_max_dti_rule_hard_fails_above_the_hard_fail_limit(default_thresholds) -> None:
    profile = _profile(default_thresholds, monthly_income=10_000, monthly_debt=5_100,
                        loan_amount=150_000, appraised_value=300_000, credit_score=780)
    assert max_dti_rule(profile, default_thresholds).outcome is RuleOutcome.HARD_FAIL


def test_max_ltv_rule_passes_for_low_ltv(default_thresholds) -> None:
    profile = _profile(default_thresholds, monthly_income=10_000, monthly_debt=500,
                        loan_amount=150_000, appraised_value=300_000, credit_score=780)
    assert max_ltv_rule(profile, default_thresholds).outcome is RuleOutcome.PASS


def test_max_ltv_rule_hard_fails_above_the_hard_fail_limit(default_thresholds) -> None:
    profile = _profile(default_thresholds, monthly_income=10_000, monthly_debt=500,
                        loan_amount=310_000, appraised_value=300_000, credit_score=780)
    assert max_ltv_rule(profile, default_thresholds).outcome is RuleOutcome.HARD_FAIL


def test_min_credit_score_rule_passes_for_good_credit(default_thresholds) -> None:
    profile = _profile(default_thresholds, monthly_income=10_000, monthly_debt=500,
                        loan_amount=150_000, appraised_value=300_000, credit_score=780)
    assert min_credit_score_rule(profile, default_thresholds).outcome is RuleOutcome.PASS


def test_min_credit_score_rule_is_borderline_for_fair_bucket(default_thresholds) -> None:
    profile = _profile(default_thresholds, monthly_income=10_000, monthly_debt=500,
                        loan_amount=150_000, appraised_value=300_000, credit_score=600)
    assert min_credit_score_rule(profile, default_thresholds).outcome is RuleOutcome.BORDERLINE


def test_min_credit_score_rule_hard_fails_below_the_minimum(default_thresholds) -> None:
    profile = _profile(default_thresholds, monthly_income=10_000, monthly_debt=500,
                        loan_amount=150_000, appraised_value=300_000, credit_score=500)
    assert min_credit_score_rule(profile, default_thresholds).outcome is RuleOutcome.HARD_FAIL
