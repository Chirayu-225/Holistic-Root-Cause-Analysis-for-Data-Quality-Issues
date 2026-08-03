"""
Shared "does this defect show up in this data?" check.

Originally lived only inside Layer 6 (layer6_lineage.py), where it answers
"is the defect present at this pipeline STAGE?" for boundary testing. It's
extracted here because the Validation layer (validate.py) needs the exact
same check applied to a different kind of slice -- "is the defect present
OUTSIDE the claimed segment?" for a counterfactual test -- and duplicating
the rule/statistical-technique dispatch logic in two places would be a
real risk of the two copies drifting out of sync over time.
"""
from __future__ import annotations

import pandas as pd

from app.layers.rules import RULES

NULL_PRESENCE_THRESHOLD = 0.5  # for null-rate-based fingerprints: fraction null to call "still present"


def _rule_by_id(rule_id: str):
    for r in RULES:
        if r.rule_id == rule_id:
            return r
    return None


def defect_present_in(data_slice: pd.DataFrame, fingerprint: dict, record_uids: set) -> bool | None:
    """
    Returns True (defect present in this slice), False (absent -- clean),
    or None (undefined -- the affected field(s) don't exist in this slice,
    or none of the given record_uids are present in it).
    """
    fields = fingerprint["affected_fields"]
    if not all(f in data_slice.columns for f in fields):
        return None

    subset = data_slice[data_slice["record_uid"].isin(record_uids)] if record_uids else data_slice
    if subset.empty:
        return None

    rule_ids = [r for r in fingerprint.get("matched_rule_ids", []) if not r.startswith("auto:")]
    if rule_ids:
        rule = _rule_by_id(rule_ids[0])
        if rule is not None:
            try:
                mask = rule.check(data_slice)
            except KeyError:
                return None
            return bool(mask.loc[subset.index].fillna(False).any())

    auto_ids = [r for r in fingerprint.get("matched_rule_ids", []) if r.startswith("auto:")]
    technique = auto_ids[0].split(":")[1] if auto_ids else None

    if technique == "null_spike":
        return bool(subset[fields[0]].isna().mean() > NULL_PRESENCE_THRESHOLD)

    if technique == "zscore":
        from app.layers.statistical_baseline import MODIFIED_ZSCORE_THRESHOLD, _modified_zscore
        segment_col = fingerprint.get("dominant_segment_column")
        segment_val = fingerprint.get("dominant_segment_value")
        population = data_slice if segment_col is None else data_slice[data_slice[segment_col] == segment_val]
        values = population[fields[0]].dropna()
        if len(values) < 2:
            return None
        z = _modified_zscore(values)
        overlapping_idx = subset.index.intersection(z.index)
        if overlapping_idx.empty:
            return None
        return bool(z.loc[overlapping_idx].abs().gt(MODIFIED_ZSCORE_THRESHOLD).any())

    return None


def rule_violation_mask_anywhere(data_slice: pd.DataFrame, fingerprint: dict) -> pd.Series | None:
    """
    For rule-based fingerprints, returns the FULL boolean violation mask
    (not restricted to the fingerprint's own record_uids) so callers can
    check whether the defect shows up in records the fingerprint never
    flagged in the first place -- used by the counterfactual test to check
    "does this rule also fail outside the claimed segment?"
    """
    rule_ids = [r for r in fingerprint.get("matched_rule_ids", []) if not r.startswith("auto:")]
    if not rule_ids:
        return None
    rule = _rule_by_id(rule_ids[0])
    if rule is None:
        return None
    try:
        return rule.check(data_slice).fillna(False)
    except KeyError:
        return None
