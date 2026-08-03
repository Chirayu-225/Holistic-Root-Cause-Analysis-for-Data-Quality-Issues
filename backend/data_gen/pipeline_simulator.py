"""
Simulates a realistic multi-stage transformation pipeline (raw -> staging ->
warehouse -> mart), matching Layer 6's lineage model. Each function is the
"correct" transform; defect_injector.py is the only place bugs get introduced,
so the ground truth root cause is always precisely known.
"""
from __future__ import annotations

import pandas as pd


def to_staging(raw: pd.DataFrame) -> pd.DataFrame:
    """staging: type normalization + surrogate key assignment. No business logic."""
    df = raw.copy()
    df["observed_at"] = pd.to_datetime(df["observed_at"])
    df["stage"] = "staging"
    return df


def to_warehouse(staging: pd.DataFrame) -> pd.DataFrame:
    """
    warehouse: business transformations. The one that matters most for the
    demo is humidity normalization: API_v3 sends a 0-1 fraction, NOAA_ISD
    sends 0-100 already. The CORRECT transform normalizes both to 0-100.
    """
    df = staging.copy()
    df["relative_humidity"] = df.apply(
        lambda r: r["relative_humidity_raw"] * 100
        if r["source_system"] == "API_v3"
        else r["relative_humidity_raw"],
        axis=1,
    )
    df["celsius_temperature"] = df["celsius_temperature"]
    df["stage"] = "warehouse"
    return df


def to_mart(warehouse: pd.DataFrame) -> pd.DataFrame:
    """mart: derived/aggregated fields for consumption layer."""
    df = warehouse.copy()
    df["sunrise"] = df["observed_at"].dt.normalize() + pd.Timedelta(hours=6)
    df["sunset"] = df["observed_at"].dt.normalize() + pd.Timedelta(hours=18)
    df["stage"] = "mart"
    return df


def run_pipeline(raw: pd.DataFrame) -> dict[str, pd.DataFrame]:
    staging = to_staging(raw)
    warehouse = to_warehouse(staging)
    mart = to_mart(warehouse)
    return {"raw": raw.assign(stage="raw"), "staging": staging, "warehouse": warehouse, "mart": mart}
