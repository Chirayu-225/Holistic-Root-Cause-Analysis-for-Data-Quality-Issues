"""
Tier 2 (auto-derived) of Layer 1 -- the "statistical/ML-based anomaly
detection" tier from the enterprise-methods research: learn what "normal"
looks like directly from the data rather than hand-writing thresholds, so
this tier catches unknown-unknowns the rule tier (rules.py) has no rule for.

Two techniques, both intentionally simple and explainable (no black-box
model -- a mentor/reviewer should be able to see exactly why something was
flagged):

  1. Robust numeric outlier detection via the modified z-score
     (median + MAD instead of mean + stddev, since a defect can itself
     blow out the mean/stddev and hide from a naive z-score -- MAD is far
     less sensitive to the very outliers we're trying to catch).
  2. Null-rate spike detection: compare each day's null rate per column
     against the dataset-wide baseline null rate; flag days where the
     jump is large relative to normal day-to-day noise.

Baselines are computed PER SEGMENT (source_system) and only from rows the
rule tier did NOT already flag, so a rule-caught defect doesn't pollute the
"normal" distribution the statistical tier learns from.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

NUMERIC_FIELDS = ["celsius_temperature", "relative_humidity", "wind_speed", "sea_level_pressure"]
MODIFIED_ZSCORE_THRESHOLD = 3.5
NULL_RATE_SPIKE_THRESHOLD = 0.15  # absolute jump over baseline null rate to flag a day


def _modified_zscore(series: pd.Series) -> pd.Series:
    median = series.median()
    mad = (series - median).abs().median()
    if mad == 0:
        return pd.Series(0.0, index=series.index)
    return 0.6745 * (series - median) / mad


def detect_numeric_outliers(df: pd.DataFrame, already_flagged_idx: set) -> pd.DataFrame:
    clean = df.loc[~df.index.isin(already_flagged_idx)]
    violations = []

    for segment, seg_df in clean.groupby("source_system"):
        for field in NUMERIC_FIELDS:
            if field not in seg_df.columns or seg_df[field].dropna().empty:
                continue
            z = _modified_zscore(seg_df[field].dropna())
            outlier_idx = z[z.abs() > MODIFIED_ZSCORE_THRESHOLD].index
            for idx in outlier_idx:
                violations.append(
                    dict(
                        row_index=idx,
                        record_uid=df.at[idx, "record_uid"],
                        rule_id=f"auto:zscore:{field}",
                        dq_dimension="Accuracy",
                        defect_type_hint="Corruption",
                        affected_fields=(field,),
                        severity="Medium",
                        source="statistical",
                        segment=segment,
                    )
                )
    return pd.DataFrame(violations)


def detect_null_rate_spikes(df: pd.DataFrame, already_flagged_idx: set) -> pd.DataFrame:
    """
    Flags fields whose per-day null rate jumps well above the dataset's
    baseline null rate for that field+segment -- this is what catches the
    schema-omission defect (sunrise/sunset going null for a window) even
    though no hand-written rule targets those columns.
    """
    clean = df.loc[~df.index.isin(already_flagged_idx)].copy()
    clean["day"] = pd.to_datetime(clean["observed_at"]).dt.normalize()
    violations = []

    candidate_fields = [c for c in clean.columns if clean[c].isna().any()]
    for segment, seg_df in clean.groupby("source_system"):
        for field in candidate_fields:
            baseline_null_rate = seg_df[field].isna().mean()
            daily = seg_df.groupby("day")[field].apply(lambda s: s.isna().mean())
            spike_days = daily[daily - baseline_null_rate > NULL_RATE_SPIKE_THRESHOLD].index
            if len(spike_days) == 0:
                continue
            hit_idx = seg_df.index[seg_df["day"].isin(spike_days) & seg_df[field].isna()]
            for idx in hit_idx:
                violations.append(
                    dict(
                        row_index=idx,
                        record_uid=df.at[idx, "record_uid"],
                        rule_id=f"auto:null_spike:{field}",
                        dq_dimension="Completeness",
                        defect_type_hint="Omission",
                        affected_fields=(field,),
                        severity="High",
                        source="statistical",
                        segment=segment,
                    )
                )
    return pd.DataFrame(violations)


def run_statistical_detection(df: pd.DataFrame, already_flagged_idx: set) -> pd.DataFrame:
    outliers = detect_numeric_outliers(df, already_flagged_idx)
    null_spikes = detect_null_rate_spikes(df, already_flagged_idx)
    frames = [f for f in (outliers, null_spikes) if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
