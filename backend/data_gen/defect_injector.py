"""
Injects known defects into the simulated pipeline output, logging ground
truth (defect type, onset, segment, root cause statement, and the change
event that "caused" it) so the RCA framework's hypotheses can be scored
for precision/recall during evaluation.

Each scenario returns:
  - the mutated DataFrame for the stage it targets
  - a `ground_truth` dict matching the RCA Knowledge Base schema (doc section 11.1)
  - a `change_event` dict to be inserted into change_events (Layer 5 fodder)
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

RNG = np.random.default_rng(7)


def inject_humidity_double_multiply(
    warehouse: pd.DataFrame, start_date, onset_day: int = 40
) -> tuple[pd.DataFrame, dict, dict]:
    """
    Defect type: Corruption. Segment: source_system=API_v3 only.
    Temporal pattern: step function at onset_day.
    Root cause: a warehouse ETL deploy re-applies the *100 normalization
    to already-normalized API_v3 humidity values -> values up to 100x too high.
    """
    df = warehouse.copy()
    onset_ts = start_date + timedelta(days=onset_day)
    mask = (df["source_system"] == "API_v3") & (df["observed_at"] >= onset_ts)

    df.loc[mask, "relative_humidity"] = df.loc[mask, "relative_humidity"] * 100
    df.loc[mask, "_gt_is_defective"] = True
    df.loc[mask, "_gt_defect_type"] = "Corruption"
    df.loc[mask, "_gt_root_cause_id"] = "RC-001"

    ground_truth = dict(
        root_cause_id="RC-001",
        defect_type="Corruption",
        affected_fields=["relative_humidity"],
        segment="source_system=API_v3",
        onset=onset_ts,
        temporal_pattern="step",
        statement=(
            "A warehouse-layer ETL deploy on "
            f"{onset_ts.date()} re-applied the fraction-to-percentage "
            "(*100) normalization to API_v3 humidity values that were "
            "already normalized upstream, producing readings up to 100x "
            "too high for every API_v3 station from that point forward."
        ),
    )
    change_event = dict(
        event_type="code_deploy",
        occurred_at=onset_ts,
        scope_table="warehouse.weather_clean",
        scope_column="relative_humidity",
        scope_source_system="API_v3",
        description="Deploy: humidity_normalization_v2.sql - refactored unit conversion step",
        actor="data-eng-ci-bot",
        _gt_root_cause_id="RC-001",
    )
    return df, ground_truth, change_event


def inject_schema_omission(
    mart: pd.DataFrame, start_date, onset_day: int = 25, duration_days: int = 3
) -> tuple[pd.DataFrame, dict, dict]:
    """
    Defect type: Omission. Segment: all NOAA_ISD stations, one batch window.
    Root cause: a schema migration dropped sunrise/sunset temporarily.
    """
    df = mart.copy()
    window_start = start_date + timedelta(days=onset_day)
    window_end = window_start + timedelta(days=duration_days)
    mask = (
        (df["source_system"] == "NOAA_ISD")
        & (df["observed_at"] >= window_start)
        & (df["observed_at"] < window_end)
    )
    df.loc[mask, ["sunrise", "sunset"]] = None
    df.loc[mask, "_gt_is_defective"] = True
    df.loc[mask, "_gt_defect_type"] = "Omission"
    df.loc[mask, "_gt_root_cause_id"] = "RC-002"

    ground_truth = dict(
        root_cause_id="RC-002",
        defect_type="Omission",
        affected_fields=["sunrise", "sunset"],
        segment="source_system=NOAA_ISD",
        onset=window_start,
        temporal_pattern="spike",
        statement=(
            f"A schema migration on {window_start.date()} temporarily "
            "dropped the sunrise/sunset derivation step in the mart "
            f"layer for {duration_days} days, producing null clusters "
            "for all NOAA_ISD-sourced records until the migration was "
            "rolled back."
        ),
    )
    change_event = dict(
        event_type="schema_change",
        occurred_at=window_start,
        scope_table="mart.weather_daily",
        scope_column="sunrise,sunset",
        scope_source_system="NOAA_ISD",
        description="ALTER TABLE mart.weather_daily: derived-column migration (rolled back "
        f"{duration_days} days later)",
        actor="jsmith",
        _gt_root_cause_id="RC-002",
    )
    return df, ground_truth, change_event


def inject_duplication(
    warehouse: pd.DataFrame, start_date, onset_day: int = 33
) -> tuple[pd.DataFrame, dict, dict]:
    """
    Defect type: Duplication. Segment: one batch only.
    Root cause: retry storm from an infra blip re-inserted the same batch.
    """
    df = warehouse.copy()
    target_batch_prefix = f"batch_{onset_day:03d}_"
    dup_mask = df["batch_id"].str.startswith(target_batch_prefix)
    dup_rows = df[dup_mask].copy()
    dup_rows["_gt_is_defective"] = True
    dup_rows["_gt_defect_type"] = "Duplication"
    dup_rows["_gt_root_cause_id"] = "RC-003"

    df.loc[dup_mask, "_gt_is_defective"] = True
    df.loc[dup_mask, "_gt_defect_type"] = "Duplication"
    df.loc[dup_mask, "_gt_root_cause_id"] = "RC-003"

    out = pd.concat([df, dup_rows], ignore_index=True)
    onset_ts = start_date + timedelta(days=onset_day)

    ground_truth = dict(
        root_cause_id="RC-003",
        defect_type="Duplication",
        affected_fields=["record_uid"],
        segment=f"batch_id startswith {target_batch_prefix}",
        onset=onset_ts,
        temporal_pattern="spike",
        statement=(
            f"A network interruption during the {target_batch_prefix}* "
            "load job triggered the ETL's retry logic, which lacked an "
            "idempotency check, re-inserting the same batch a second time."
        ),
    )
    change_event = dict(
        event_type="infra_event",
        occurred_at=onset_ts,
        scope_table="warehouse.weather_clean",
        scope_column=None,
        scope_source_system=None,
        description=f"PagerDuty incident: network blip during {target_batch_prefix}* load, "
        "auto-retry triggered",
        actor="infra-monitor",
        _gt_root_cause_id="RC-003",
    )
    return out, ground_truth, change_event


def inject_staleness(
    warehouse: pd.DataFrame, start_date, station_id: str = "037720-99999", onset_day: int = 50
) -> tuple[pd.DataFrame, dict, dict]:
    """
    Defect type: Staleness. Segment: single station.
    Root cause: upstream source stopped sending data (vendor-side outage).
    """
    df = warehouse.copy()
    onset_ts = start_date + timedelta(days=onset_day)
    mask = (df["station_id"] == station_id) & (df["observed_at"] >= onset_ts)
    df = df[~mask].copy()  # simulate missing records rather than mutating values

    ground_truth = dict(
        root_cause_id="RC-004",
        defect_type="Staleness",
        affected_fields=["observed_at"],
        segment=f"station_id={station_id}",
        onset=onset_ts,
        temporal_pattern="step",
        statement=(
            f"Station {station_id}'s upstream provider had an outage "
            f"starting {onset_ts.date()}; no new records have been "
            "received since, causing the freshness check to fail for "
            "that station only."
        ),
    )
    change_event = dict(
        event_type="source_system_change",
        occurred_at=onset_ts,
        scope_table="raw.weather_ingest",
        scope_column=None,
        scope_source_system="NOAA_ISD",
        description=f"Vendor changelog: {station_id} feed suspended pending hardware repair",
        actor="vendor-changelog-sync",
        _gt_root_cause_id="RC-004",
    )
    return df, ground_truth, change_event


ALL_SCENARIOS = [
    inject_humidity_double_multiply,  # applied to warehouse
    inject_schema_omission,           # applied to mart
    inject_duplication,               # applied to warehouse
    inject_staleness,                 # applied to warehouse
]
