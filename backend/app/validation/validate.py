"""
Validation & Confirmation (framework doc Section 10).

A hypothesis is not a conclusion until it's validated. This module
implements three of the doc's five techniques against real checks on the
test bed data, rather than treating hypothesis text as trustworthy on its
own:

  - Counterfactual Test (segment):   does the defect also appear OUTSIDE
                                       the claimed segment? If so, the
                                       segment hypothesis is incomplete.
  - Counterfactual Test (mechanism): if a hypothesis claims a specific
                                       causal fact (e.g. "column X is null
                                       upstream"), check that fact directly
                                       against the data.
  - Reproduce the Defect:            independently re-implement the
                                       claimed mechanism (from Layer 6's
                                       code inspection, NOT by importing
                                       defect_injector.py -- that would
                                       just be checking the ground-truth
                                       generator against itself) and see if
                                       applying it to clean data in the
                                       claimed segment produces the same
                                       failure pattern.
  - Fix and Verify:                  apply the hypothesized fix and check
                                       it resolves the defect.

The other two techniques from the doc (A/B Comparison, Expert Review) are
not implemented as automated checks here -- A/B Comparison is essentially
what Layer 2's onset detection already does, and Expert Review is exactly
what Layer 1's `needs_review` escalation path is for. Re-implementing them
here would be redundant with layers that already exist.

This module was built specifically BECAUSE Layer 6's live LLM run produced
a hypothesis (for the Omission defect) that sounded right but wasn't --
see the counterfactual_mechanism_test below, which is what would have
caught that error immediately.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.layers.defect_presence import defect_present_in, rule_violation_mask_anywhere


@dataclass
class ValidationCheck:
    technique: str
    passed: bool | None  # True = hypothesis holds, False = refuted, None = not applicable
    detail: str


def counterfactual_segment_test(df: pd.DataFrame, fingerprint: dict) -> ValidationCheck:
    """Does the defect also appear OUTSIDE the claimed segment? If so, the segment claim is incomplete."""
    seg_col = fingerprint.get("dominant_segment_column")
    seg_val = fingerprint.get("dominant_segment_value")
    if seg_col is None:
        return ValidationCheck("Counterfactual Test (segment)", None, "No segment hypothesis to test.")

    outside = df[df[seg_col] != seg_val]
    mask = rule_violation_mask_anywhere(outside, fingerprint)
    if mask is None:
        # statistical-only fingerprint -- fall back to the shared presence check on the outside slice
        present_outside = defect_present_in(outside, fingerprint, set())
    else:
        present_outside = bool(mask.any())

    if present_outside:
        return ValidationCheck(
            "Counterfactual Test (segment)", False,
            f"The defect ALSO appears outside {seg_col}={seg_val} -- the segment hypothesis is incomplete "
            "or there's a second, independent cause.",
        )
    return ValidationCheck(
        "Counterfactual Test (segment)", True,
        f"The defect does not appear anywhere outside {seg_col}={seg_val} -- the segment hypothesis holds.",
    )


def counterfactual_mechanism_test(df: pd.DataFrame, fingerprint: dict, claimed_null_column: str) -> ValidationCheck:
    """
    Directly checks a specific causal CLAIM against the data -- e.g. an LLM
    hypothesis that says "this happens because column X is null upstream."
    This is the check that would have caught Layer 6's Omission
    misdiagnosis immediately: it doesn't reason about the code at all, it
    just looks at whether the claimed fact is actually true.
    """
    if claimed_null_column not in df.columns:
        return ValidationCheck(
            "Counterfactual Test (mechanism)", None, f"Claimed column '{claimed_null_column}' not present here.",
        )
    record_uids = set(fingerprint.get("_record_uids", []))
    subset = df[df["record_uid"].isin(record_uids)]
    if subset.empty:
        return ValidationCheck("Counterfactual Test (mechanism)", None, "No matching records at this stage.")

    null_rate = subset[claimed_null_column].isna().mean()
    if null_rate < 0.5:
        return ValidationCheck(
            "Counterfactual Test (mechanism)", False,
            f"REFUTED: the hypothesis claims '{claimed_null_column}' is null for the affected records, but it's "
            f"actually populated {1 - null_rate:.0%} of the time. The claimed mechanism does not hold.",
        )
    return ValidationCheck(
        "Counterfactual Test (mechanism)", True,
        f"'{claimed_null_column}' is indeed null for {null_rate:.0%} of the affected records -- consistent with "
        "the claimed mechanism (though this alone doesn't prove causation).",
    )


def reproduce_humidity_corruption(warehouse_healthy: pd.DataFrame, fingerprint: dict) -> ValidationCheck:
    """
    Reproduce the Defect (doc's strongest validation technique), specific to
    the Corruption/relative_humidity case. Independently re-implements the
    mechanism Layer 6's code inspection identified -- "the *100 fraction-to-
    percentage step gets applied twice for API_v3" -- against CLEAN API_v3
    data, and checks whether that alone reproduces the observed out-of-range
    failure pattern. Deliberately does NOT import defect_injector.py: that
    would just be checking the ground-truth generator against itself, which
    proves nothing about whether the hypothesis is actually correct.
    """
    if fingerprint["defect_type"] != "Corruption" or "relative_humidity" not in fingerprint["affected_fields"]:
        return ValidationCheck("Reproduce the Defect", None, "Not applicable to this fingerprint.")

    clean_api_v3 = warehouse_healthy[
        (warehouse_healthy["source_system"] == "API_v3") & (warehouse_healthy["relative_humidity"] <= 100)
    ]
    if clean_api_v3.empty:
        return ValidationCheck("Reproduce the Defect", None, "No clean API_v3 data available to test against.")

    reproduced = clean_api_v3["relative_humidity"] * 100  # the hypothesized double-multiply
    reproduction_rate = (reproduced > 100).mean()

    if reproduction_rate > 0.95:
        return ValidationCheck(
            "Reproduce the Defect", True,
            f"Applying the hypothesized mechanism (re-multiplying by 100) to {len(clean_api_v3)} clean API_v3 "
            f"records reproduces out-of-range values {reproduction_rate:.0%} of the time -- strong confirmation "
            "the hypothesis is mechanistically sufficient to cause the observed defect.",
        )
    return ValidationCheck(
        "Reproduce the Defect", False,
        f"Applying the hypothesized mechanism only reproduces the failure {reproduction_rate:.0%} of the time -- "
        "insufficient to confirm the hypothesis.",
    )


def fix_and_verify_humidity_corruption(warehouse: pd.DataFrame, fingerprint: dict) -> ValidationCheck:
    """Fix and Verify: apply the hypothesized fix (divide by 100) and confirm it resolves the defect."""
    if fingerprint["defect_type"] != "Corruption" or "relative_humidity" not in fingerprint["affected_fields"]:
        return ValidationCheck("Fix and Verify", None, "Not applicable to this fingerprint.")

    record_uids = set(fingerprint.get("_record_uids", []))
    failing = warehouse[warehouse["record_uid"].isin(record_uids)]
    if failing.empty:
        return ValidationCheck("Fix and Verify", None, "No failing records found.")

    fixed_values = failing["relative_humidity"] / 100
    still_broken = ((fixed_values < 0) | (fixed_values > 100)).mean()

    if still_broken < 0.05:
        return ValidationCheck(
            "Fix and Verify", True,
            f"Applying the hypothesized fix (divide by 100) resolves {1 - still_broken:.0%} of the "
            f"{len(failing)} failing records back into the valid [0, 100] range -- fix confirmed.",
        )
    return ValidationCheck(
        "Fix and Verify", False,
        f"The hypothesized fix leaves {still_broken:.0%} of records still out of range -- fix is incomplete.",
    )


@dataclass
class ValidationReport:
    checks: list[ValidationCheck]

    @property
    def verdict(self) -> str:
        applicable = [c for c in self.checks if c.passed is not None]
        if not applicable:
            return "NO VALIDATION PERFORMED"
        if all(c.passed for c in applicable):
            return "CONFIRMED"
        if any(c.passed is False for c in applicable):
            return "REFUTED (at least one check failed)"
        return "INCONCLUSIVE"


if __name__ == "__main__":
    import os

    from app.layers.layer1_defect_characterization import characterize

    base = os.path.join(os.path.dirname(__file__), "..", "..", "generated")
    warehouse = pd.read_csv(os.path.join(base, "warehouse.csv"), parse_dates=["observed_at"])
    mart = pd.read_csv(os.path.join(base, "mart.csv"), parse_dates=["observed_at", "sunrise", "sunset"])

    print("=== Validation: Corruption (relative_humidity) -- the case that SHOULD confirm ===")
    for fp in characterize(warehouse):
        if fp["defect_type"] != "Corruption" or "relative_humidity" not in fp["affected_fields"]:
            continue
        checks = [
            counterfactual_segment_test(warehouse, fp),
            reproduce_humidity_corruption(warehouse, fp),
            fix_and_verify_humidity_corruption(warehouse, fp),
        ]
        report = ValidationReport(checks)
        for c in checks:
            if c.passed is None:
                continue
            print(f"  [{'PASS' if c.passed else 'FAIL'}] {c.technique}: {c.detail}")
        print(f"  VERDICT: {report.verdict}")
        print()

    print("=== Validation: Omission (sunrise) -- testing the LLM's live hypothesis from the last session ===")
    print("    (LLM claimed: 'observed_at is NULL upstream for NOAA_ISD, causing DATE_TRUNC(NULL) to cascade')")
    for fp in characterize(mart):
        if fp["defect_type"] != "Omission" or "sunrise" not in fp["affected_fields"]:
            continue
        checks = [
            counterfactual_segment_test(mart, fp),
            counterfactual_mechanism_test(warehouse, fp, claimed_null_column="observed_at"),
        ]
        report = ValidationReport(checks)
        for c in checks:
            if c.passed is None:
                continue
            print(f"  [{'PASS' if c.passed else 'FAIL'}] {c.technique}: {c.detail}")
        print(f"  VERDICT (of the LLM's specific claim): {report.verdict}")
