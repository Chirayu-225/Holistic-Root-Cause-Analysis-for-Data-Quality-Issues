"""
Tier 1 (deterministic) of Layer 1 defect characterization -- the "rule-based
validation" tier from the enterprise-methods research: explicit constraint
and business rules, the same category as Great Expectations / Soda / dbt
tests. Each rule is intentionally narrow and interpretable; it only catches
KNOWN failure modes. Unknown-unknowns are the statistical tier's job
(statistical_baseline.py).

Every rule function takes the full warehouse/mart DataFrame and returns a
boolean Series (True = violates the rule). Keeping rules as pure
DataFrame -> Series functions makes them trivial to unit test in isolation
and to add to without touching the orchestration logic in
layer1_defect_characterization.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd


@dataclass(frozen=True)
class Rule:
    rule_id: str
    dq_dimension: str          # Accuracy, Validity, Completeness, Consistency, Uniqueness, Timeliness
    defect_type_hint: str      # Corruption, Injection, Omission, Duplication, Drift, Staleness, Contradiction
    affected_fields: list[str]
    description: str
    severity: str              # Critical, High, Medium, Low
    check: Callable[[pd.DataFrame], pd.Series]


def _humidity_out_of_range(df: pd.DataFrame) -> pd.Series:
    col = df["relative_humidity"]
    return (col < 0) | (col > 100)


def _temperature_physically_implausible(df: pd.DataFrame) -> pd.Series:
    # Earth surface air temp: generous bounds to avoid false positives, -90C to 60C
    col = df["celsius_temperature"]
    return (col < -90) | (col > 60)


def _required_field_null(field: str) -> Callable[[pd.DataFrame], pd.Series]:
    def _check(df: pd.DataFrame) -> pd.Series:
        return df[field].isna()
    return _check


def _duplicate_record_uid(df: pd.DataFrame) -> pd.Series:
    return df.duplicated(subset=["record_uid"], keep=False)


def _sunset_before_sunrise(df: pd.DataFrame) -> pd.Series:
    if "sunrise" not in df.columns or "sunset" not in df.columns:
        return pd.Series(False, index=df.index)
    both_present = df["sunrise"].notna() & df["sunset"].notna()
    violates = pd.Series(False, index=df.index)
    violates.loc[both_present] = df.loc[both_present, "sunset"] <= df.loc[both_present, "sunrise"]
    return violates


def _stale_station_feed(max_gap_hours: int = 30) -> Callable[[pd.DataFrame], pd.Series]:
    """
    A record is flagged stale if its station's most recent observation is more
    than `max_gap_hours` behind the dataset's global most-recent timestamp --
    i.e. every OTHER station kept reporting, but this one went quiet.
    """
    def _check(df: pd.DataFrame) -> pd.Series:
        global_latest = df["observed_at"].max()
        latest_per_station = df.groupby("station_id")["observed_at"].transform("max")
        gap_hours = (global_latest - latest_per_station).dt.total_seconds() / 3600
        return gap_hours > max_gap_hours
    return _check


RULES: list[Rule] = [
    Rule(
        rule_id="R-001-humidity-range",
        dq_dimension="Validity",
        defect_type_hint="Corruption",
        affected_fields=["relative_humidity"],
        description="relative_humidity must be within [0, 100]",
        severity="Critical",
        check=_humidity_out_of_range,
    ),
    Rule(
        rule_id="R-002-temperature-range",
        dq_dimension="Validity",
        defect_type_hint="Corruption",
        affected_fields=["celsius_temperature"],
        description="celsius_temperature must be within physically plausible bounds",
        severity="High",
        check=_temperature_physically_implausible,
    ),
    Rule(
        rule_id="R-003-required-temperature",
        dq_dimension="Completeness",
        defect_type_hint="Omission",
        affected_fields=["celsius_temperature"],
        description="celsius_temperature is a required field",
        severity="Critical",
        check=_required_field_null("celsius_temperature"),
    ),
    Rule(
        rule_id="R-004-required-humidity",
        dq_dimension="Completeness",
        defect_type_hint="Omission",
        affected_fields=["relative_humidity"],
        description="relative_humidity is a required field",
        severity="Critical",
        check=_required_field_null("relative_humidity"),
    ),
    Rule(
        rule_id="R-005-duplicate-record",
        dq_dimension="Uniqueness",
        defect_type_hint="Duplication",
        affected_fields=["record_uid"],
        description="record_uid must be unique per pipeline stage",
        severity="Medium",
        check=_duplicate_record_uid,
    ),
    Rule(
        rule_id="R-006-sunset-before-sunrise",
        dq_dimension="Consistency",
        defect_type_hint="Contradiction",
        affected_fields=["sunrise", "sunset"],
        description="sunset must be after sunrise on the same record",
        severity="Medium",
        check=_sunset_before_sunrise,
    ),
    Rule(
        rule_id="R-007-station-staleness",
        dq_dimension="Timeliness",
        defect_type_hint="Staleness",
        affected_fields=["observed_at"],
        description="station's latest observation must be within 30h of the freshest data in the feed",
        severity="High",
        check=_stale_station_feed(max_gap_hours=30),
    ),
]


def run_rules(df: pd.DataFrame, rules: list[Rule] = RULES) -> pd.DataFrame:
    """
    Runs every rule against df and returns a long-format violations table:
    one row per (record, rule) violation. This is the Tier-1 input to the
    Layer 1 fingerprint synthesizer.
    """
    violations = []
    for rule in rules:
        try:
            mask = rule.check(df)
        except KeyError:
            continue  # rule references a column not present at this stage; skip gracefully
        hit_idx = df.index[mask.fillna(False)]
        for idx in hit_idx:
            violations.append(
                dict(
                    row_index=idx,
                    record_uid=df.at[idx, "record_uid"],
                    rule_id=rule.rule_id,
                    dq_dimension=rule.dq_dimension,
                    defect_type_hint=rule.defect_type_hint,
                    affected_fields=tuple(rule.affected_fields),
                    severity=rule.severity,
                    source="rule",
                )
            )
    return pd.DataFrame(violations)
