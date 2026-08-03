"""
Layer 1: Defect Characterization.

Combines Tier 1 (rules.py, deterministic) and Tier 2 (statistical_baseline.py,
auto-derived) violations into the defect fingerprint schema from the RCA
framework doc (Section 3.1): DQ Dimension, Affected Field(s), Failure
Pattern, Failure Volume, Failure Distribution, First Observed, Severity --
plus a mechanistic Defect Type classification (Corruption, Omission, etc.)
that narrows the hypothesis space for the rest of the pipeline.

Grouping logic: violations are grouped into one fingerprint per
(affected_fields, defect_type_hint) combination, since that's the natural
unit a downstream layer would investigate as a single issue. Within each
group, `needs_review` is set when the engine's own evidence is thin or
conflicting -- mirroring the human-steward escalation tier: rules and
statistics don't get to silently overrule each other, ambiguity gets
surfaced instead of guessed at.
"""
from __future__ import annotations

from collections import Counter

import pandas as pd

from app.layers.rules import run_rules
from app.layers.statistical_baseline import run_statistical_detection

SEVERITY_ORDER = {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}
MIN_VOLUME_FOR_CONFIDENT_CLASSIFICATION = 5
CONCENTRATION_THRESHOLD = 0.8  # fraction of violations in one segment to call it "concentrated"


def characterize(df: pd.DataFrame, rule_id: str | None = None) -> list[dict]:
    """
    Runs both detection tiers on `df` (a single pipeline stage) and returns
    a list of defect fingerprint dicts, matching the DefectFingerprint model.
    """
    rule_violations = run_rules(df)
    already_flagged = set(rule_violations["row_index"]) if not rule_violations.empty else set()
    stat_violations = run_statistical_detection(df, already_flagged)

    all_violations = pd.concat(
        [v for v in (rule_violations, stat_violations) if not v.empty], ignore_index=True
    )
    if all_violations.empty:
        return []

    fingerprints = []
    grouped = all_violations.groupby(["affected_fields", "defect_type_hint"])
    for (affected_fields, defect_type), group in grouped:
        fingerprints.append(_build_fingerprint(df, group, affected_fields, defect_type, rule_id))
    return fingerprints


def _build_fingerprint(
    df: pd.DataFrame, group: pd.DataFrame, affected_fields: tuple, defect_type: str, rule_id: str | None
) -> dict:
    record_uids = group["record_uid"].unique()
    volume = len(record_uids)
    total = len(df)
    dq_dimension = Counter(group["dq_dimension"]).most_common(1)[0][0]
    severity = max(group["severity"], key=lambda s: SEVERITY_ORDER.get(s, 0))

    hit_rows = df[df["record_uid"].isin(record_uids)]
    first_observed = pd.to_datetime(hit_rows["observed_at"]).min()

    # distribution: is this concentrated in one segment, or scattered?
    seg_counts = hit_rows["source_system"].value_counts(normalize=True)
    dominant_segment_column = None
    dominant_segment_value = None
    if not seg_counts.empty and seg_counts.iloc[0] >= CONCENTRATION_THRESHOLD:
        distribution = f"{seg_counts.iloc[0]:.0%} concentrated in source_system={seg_counts.index[0]}"
        dominant_segment_column = "source_system"
        dominant_segment_value = seg_counts.index[0]
    else:
        distribution = "scattered across multiple source_systems"

    sources_used = set(group["source"])
    rule_ids_hit = sorted(group["rule_id"].unique())
    failure_pattern = f"{defect_type} in {', '.join(affected_fields)} (triggers: {', '.join(rule_ids_hit)})"

    needs_review, review_reason = _needs_review(volume, sources_used, seg_counts)

    return dict(
        dq_dimension=dq_dimension,
        affected_fields=list(affected_fields),
        failure_pattern=failure_pattern,
        failure_volume=volume,
        failure_total=total,
        failure_distribution=distribution,
        dominant_segment_column=dominant_segment_column,
        dominant_segment_value=dominant_segment_value,
        matched_rule_ids=rule_ids_hit,
        defect_type=defect_type,
        severity=severity,
        first_observed=first_observed,
        rule_id=rule_id,
        needs_review=needs_review,
        review_reason=review_reason,
        # in-process only (not part of the DefectFingerprint DB schema): the exact
        # violating record_uids, for layers that need precise slicing rather than a
        # re-derived signal. Strip this key before persisting to the DB.
        _record_uids=list(record_uids),
    )


def _needs_review(volume: int, sources_used: set, seg_counts: pd.Series) -> tuple[bool, str | None]:
    if volume < MIN_VOLUME_FOR_CONFIDENT_CLASSIFICATION:
        return True, f"Low volume ({volume} records) -- confirm this isn't noise before investigating."
    if len(sources_used) > 1:
        return True, "Both rule-based and statistical detection independently flagged this pattern with different signals -- worth a sanity check, though this often means stronger evidence, not weaker."
    if not seg_counts.empty and seg_counts.iloc[0] < 0.5:
        return True, "No single segment explains the majority of failures -- may be a global issue or multiple co-occurring causes."
    return False, None


if __name__ == "__main__":
    # quick manual check against the generated test bed
    import os

    base = os.path.join(os.path.dirname(__file__), "..", "..", "generated")
    warehouse = pd.read_csv(os.path.join(base, "warehouse.csv"), parse_dates=["observed_at"])
    mart = pd.read_csv(os.path.join(base, "mart.csv"), parse_dates=["observed_at", "sunrise", "sunset"])

    print("=== Layer 1 fingerprints: warehouse stage ===")
    for fp in characterize(warehouse):
        print(fp)
        print()

    print("=== Layer 1 fingerprints: mart stage ===")
    for fp in characterize(mart):
        print(fp)
        print()
