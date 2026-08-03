"""
Layer 2: Temporal Analysis.

Core question: when did the defect start, and what shape does it have over
time? Implements three of the four onset-detection techniques from the
framework doc (Section 4.1) -- Historical Rule Replay, Volume Anomaly, and
a null-rate variant of Distribution Shift Detection -- plus the temporal
pattern classification from Section 4.2 (step / spike / gradual drift /
periodic / random scatter).

Schema Archaeology (the fourth technique, correlating onset with DDL
history) isn't implemented here -- it needs schema-change metadata, which
is exactly what Layer 5's change_events feed provides. Flagging that
dependency explicitly rather than faking it with data we don't have yet.

IMPORTANT DESIGN CHOICE: this layer does NOT simply reuse the exact set of
row IDs Layer 1 flagged. For most defect types that's fine, but for
Staleness specifically, Layer 1's rule flags every record belonging to a
now-quiet entity -- including records from long before the entity actually
went quiet. Reusing that set naively would make the onset look like "day
one," which is wrong. Instead, Layer 2 independently re-derives a
day-by-day signal using the technique appropriate to the defect type, which
is also what makes this a genuinely separate evidence layer rather than a
re-statement of Layer 1's output.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from app.layers.rules import RULES, Rule

ACTIVE_THRESHOLD = 0.05          # violation/null rate above this counts as "elevated" for a day
STALENESS_GAP_HOURS = 30         # matches R-007's threshold in rules.py
STEP_TAIL_WINDOW = 3             # how many trailing days must stay elevated to call it a step (not a spike)
DRIFT_MIN_R2 = 0.35              # minimum linear-trend fit to call something "drift" over "scatter"
PERIODIC_MIN_AUTOCORR = 0.5      # minimum autocorrelation at the detected lag to call something "periodic"


@dataclass
class TemporalResult:
    onset: pd.Timestamp | None
    technique: str
    pattern: str
    evidence: str
    daily_series: pd.DataFrame  # columns: day, rate (or count) -- kept for inspection/plotting


def _rule_by_id(rule_id: str) -> Rule | None:
    for r in RULES:
        if r.rule_id == rule_id:
            return r
    return None


# --------------------------------------------------------------------------- #
# Onset-detection techniques
# --------------------------------------------------------------------------- #

def historical_rule_replay(df: pd.DataFrame, rule: Rule) -> pd.DataFrame:
    """
    Re-runs `rule` against each day's partition independently -- i.e. "replays"
    the rule against historical snapshots, exactly as the framework doc
    describes. Only valid for rules whose check is day-independent (doesn't
    reference cross-day aggregates like a global max timestamp).
    """
    working = df.copy()
    working["_day"] = pd.to_datetime(working["observed_at"]).dt.normalize()
    rows = []
    for day, day_df in working.groupby("_day"):
        try:
            mask = rule.check(day_df)
        except KeyError:
            continue
        rows.append((day, mask.fillna(False).mean(), len(day_df)))
    return pd.DataFrame(rows, columns=["day", "rate", "n"]).sort_values("day").reset_index(drop=True)


def null_rate_daily_series(df: pd.DataFrame, field: str, segment_col: str | None, segment_value) -> pd.DataFrame:
    """Distribution Shift Detection (null-rate variant): per-day null rate for `field`."""
    working = df if segment_col is None else df[df[segment_col] == segment_value]
    working = working.copy()
    working["_day"] = pd.to_datetime(working["observed_at"]).dt.normalize()
    daily = working.groupby("_day")[field].apply(lambda s: s.isna().mean())
    counts = working.groupby("_day").size()
    return pd.DataFrame({"day": daily.index, "rate": daily.values, "n": counts.values}).reset_index(drop=True)


def duplicate_rate_daily_series(df: pd.DataFrame) -> pd.DataFrame:
    """Volume Anomaly variant: per-day fraction of records that are duplicate record_uids."""
    working = df.copy()
    working["_day"] = pd.to_datetime(working["observed_at"]).dt.normalize()
    working["_is_dup"] = working.duplicated(subset=["record_uid"], keep=False)
    daily = working.groupby("_day")["_is_dup"].mean()
    counts = working.groupby("_day").size()
    return pd.DataFrame({"day": daily.index, "rate": daily.values, "n": counts.values}).reset_index(drop=True)


def volume_anomaly_entity_onset(
    df: pd.DataFrame, entity_col: str, segment_col: str | None, segment_value
) -> tuple[pd.Timestamp | None, str, pd.DataFrame]:
    """
    For staleness-type defects: finds which entity within the segment "went
    quiet" -- i.e. its last observation is well behind the segment's overall
    most recent observation -- and returns that entity's last-seen date + 1
    as the onset, along with a daily record-count series for that entity
    (for pattern classification and plotting).
    """
    working = df if segment_col is None else df[df[segment_col] == segment_value]
    global_latest = pd.to_datetime(working["observed_at"]).max()

    last_seen = working.groupby(entity_col)["observed_at"].max()
    gap_hours = (global_latest - last_seen).dt.total_seconds() / 3600
    quiet_entities = gap_hours[gap_hours > STALENESS_GAP_HOURS]

    if quiet_entities.empty:
        return None, "none", pd.DataFrame(columns=["day", "rate", "n"])

    worst_entity = quiet_entities.idxmax()
    entity_df = working[working[entity_col] == worst_entity].copy()
    entity_df["_day"] = pd.to_datetime(entity_df["observed_at"]).dt.normalize()
    daily_counts = entity_df.groupby("_day").size()

    full_range = pd.date_range(daily_counts.index.min(), pd.to_datetime(global_latest).normalize(), freq="D")
    daily_counts = daily_counts.reindex(full_range, fill_value=0)
    # rate = 1 on days the entity went SILENT (the defect), not days it reported --
    # classify_temporal_pattern treats "elevated rate" as "defect present", so this
    # must be an absence signal, not a presence signal.
    daily = pd.DataFrame({"day": daily_counts.index, "rate": (daily_counts.values == 0).astype(float),
                           "n": daily_counts.values})

    onset = last_seen[worst_entity].normalize() + pd.Timedelta(days=1)
    return onset, worst_entity, daily


# --------------------------------------------------------------------------- #
# Onset boundary detection (shared across techniques once you have a daily series)
# --------------------------------------------------------------------------- #

def find_onset_via_backward_walk(daily: pd.DataFrame, threshold: float = ACTIVE_THRESHOLD) -> pd.Timestamp | None:
    """
    Finds the first day the rate crosses the activity threshold, scanning
    chronologically forward. This is the general form of "Historical Rule
    Replay": whether the defect is still ongoing (step) or already resolved
    (spike), the onset is the first day it stopped passing. A backward walk
    from the most recent day only works when the defect is still active at
    the end of the observed range -- it silently breaks on spikes that have
    already returned to baseline, so a forward scan is used instead.
    """
    if daily.empty:
        return None
    daily = daily.sort_values("day").reset_index(drop=True)
    elevated = daily.index[daily["rate"] > threshold]
    return daily.loc[elevated[0], "day"] if len(elevated) else None


# --------------------------------------------------------------------------- #
# Temporal pattern classification
# --------------------------------------------------------------------------- #

def classify_temporal_pattern(daily: pd.DataFrame, threshold: float = ACTIVE_THRESHOLD) -> tuple[str, str]:
    """
    Classifies the shape of a (day, rate) series into one of the five
    patterns from the framework doc Section 4.2. Returns (pattern, evidence).
    """
    if daily.empty:
        return "none", "No time series available."

    daily = daily.sort_values("day").reset_index(drop=True)
    active = daily["rate"] > threshold
    if not active.any():
        return "none", "No day exceeded the activity threshold."

    active_idx = daily.index[active].tolist()
    first_active, last_active = active_idx[0], active_idx[-1]
    last_idx = len(daily) - 1

    # Step: once elevated, stays elevated through (near) the end of the observed range.
    tail = daily.iloc[max(0, last_idx - STEP_TAIL_WINDOW + 1):]
    if last_active >= last_idx - 1 and (tail["rate"] > threshold).mean() >= 0.6:
        return (
            "step",
            f"Rate became elevated on {daily.loc[first_active, 'day'].date()} and remained elevated "
            f"through the end of the observed range ({daily.loc[last_idx, 'day'].date()}) -- "
            "consistent with a one-time event (deployment, migration, config change) whose effect persists.",
        )

    # Spike: short contiguous elevated burst that returns to baseline before the range ends.
    if last_active < last_idx - 1:
        return (
            "spike",
            f"Rate spiked from {daily.loc[first_active, 'day'].date()} to "
            f"{daily.loc[last_active, 'day'].date()} then returned to baseline -- "
            "consistent with a transient event (retry storm, one-off manual correction, network blip).",
        )

    # Drift: gradual monotonic increase over the whole series, no sharp jump dominates.
    x = np.arange(len(daily))
    slope, intercept, r_value, p_value, _ = scipy_stats.linregress(x, daily["rate"])
    if slope > 0 and r_value**2 >= DRIFT_MIN_R2 and p_value < 0.05:
        return (
            "drift",
            f"Rate shows a statistically significant upward trend (R^2={r_value**2:.2f}, p={p_value:.3f}) "
            "rather than a sharp onset -- consistent with a continuous process (sensor degradation, "
            "slowly changing source).",
        )

    # Periodic: check autocorrelation at a handful of candidate lags for a strong repeating pattern.
    series = daily["rate"].values - daily["rate"].mean()
    n = len(series)
    best_lag, best_corr = None, 0.0
    for lag in range(2, min(n // 2, 14) + 1):
        if n - lag < 3:
            continue
        corr = np.corrcoef(series[:-lag], series[lag:])[0, 1]
        if not np.isnan(corr) and abs(corr) > abs(best_corr):
            best_lag, best_corr = lag, corr
    if best_lag and abs(best_corr) >= PERIODIC_MIN_AUTOCORR:
        return (
            "periodic",
            f"Rate shows a repeating pattern with period ~{best_lag} days (autocorrelation={best_corr:.2f}) -- "
            "consistent with a scheduled process (batch job, periodic report, timezone-sensitive logic).",
        )

    return (
        "scatter",
        "No consistent step, spike, drift, or periodic shape found -- defects appear unpredictably, "
        "consistent with a stochastic cause (race condition, intermittent upstream failure, user error).",
    )


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #

def analyze_temporal(df: pd.DataFrame, fingerprint: dict) -> TemporalResult:
    """
    Dispatches to the appropriate onset-detection technique based on the
    fingerprint's defect_type, then classifies the resulting time series.
    """
    defect_type = fingerprint["defect_type"]
    fields = fingerprint["affected_fields"]
    seg_col = fingerprint.get("dominant_segment_column")
    seg_val = fingerprint.get("dominant_segment_value")

    if defect_type == "Staleness":
        onset, entity, daily = volume_anomaly_entity_onset(df, "station_id", seg_col, seg_val)
        technique = f"Volume Anomaly (per-entity daily record count; entity={entity})"
        pattern, evidence = classify_temporal_pattern(daily, threshold=0.5)  # binary presence/absence series
        return TemporalResult(onset, technique, pattern, evidence, daily)

    if defect_type == "Omission":
        daily = null_rate_daily_series(df, fields[0], seg_col, seg_val)
        onset = find_onset_via_backward_walk(daily)
        technique = f"Distribution Shift Detection (null-rate series for {fields[0]})"
        pattern, evidence = classify_temporal_pattern(daily)
        return TemporalResult(onset, technique, pattern, evidence, daily)

    if defect_type == "Duplication":
        daily = duplicate_rate_daily_series(df if seg_col is None else df[df[seg_col] == seg_val])
        onset = find_onset_via_backward_walk(daily)
        technique = "Volume Anomaly (daily duplicate-record rate)"
        pattern, evidence = classify_temporal_pattern(daily)
        return TemporalResult(onset, technique, pattern, evidence, daily)

    # Default path: Corruption, Contradiction, Injection -- use the matched
    # deterministic rule if we have one and it's day-independent.
    rule_ids = [r for r in fingerprint.get("matched_rule_ids", []) if not r.startswith("auto:")]
    if rule_ids:
        rule = _rule_by_id(rule_ids[0])
        if rule is not None:
            working = df if seg_col is None else df[df[seg_col] == seg_val]
            daily = historical_rule_replay(working, rule)
            onset = find_onset_via_backward_walk(daily)
            pattern, evidence = classify_temporal_pattern(daily)
            return TemporalResult(onset, f"Historical Rule Replay ({rule.rule_id})", pattern, evidence, daily)

    # Fallback: statistical-only fingerprint with no deterministic rule to replay.
    daily = null_rate_daily_series(df, fields[0], seg_col, seg_val)  # crude fallback, still day-partitioned
    onset = find_onset_via_backward_walk(daily)
    pattern, evidence = classify_temporal_pattern(daily)
    return TemporalResult(onset, "Distribution Shift Detection (fallback)", pattern, evidence, daily)


if __name__ == "__main__":
    import os

    from app.layers.layer1_defect_characterization import characterize

    base = os.path.join(os.path.dirname(__file__), "..", "..", "generated")
    warehouse = pd.read_csv(os.path.join(base, "warehouse.csv"), parse_dates=["observed_at"])
    mart = pd.read_csv(os.path.join(base, "mart.csv"), parse_dates=["observed_at", "sunrise", "sunset"])

    print("=== Layer 2 temporal analysis: warehouse stage ===")
    for fp in characterize(warehouse):
        result = analyze_temporal(warehouse, fp)
        print(f"[{fp['defect_type']}] {fp['affected_fields']}")
        print(f"  onset      : {result.onset}")
        print(f"  technique  : {result.technique}")
        print(f"  pattern    : {result.pattern}")
        print(f"  evidence   : {result.evidence}")
        print()

    print("=== Layer 2 temporal analysis: mart stage (Omission only, not present in warehouse) ===")
    for fp in characterize(mart):
        if fp["defect_type"] != "Omission":
            continue
        result = analyze_temporal(mart, fp)
        print(f"[{fp['defect_type']}] {fp['affected_fields']}")
        print(f"  onset      : {result.onset}")
        print(f"  technique  : {result.technique}")
        print(f"  pattern    : {result.pattern}")
        print(f"  evidence   : {result.evidence}")
        print()
