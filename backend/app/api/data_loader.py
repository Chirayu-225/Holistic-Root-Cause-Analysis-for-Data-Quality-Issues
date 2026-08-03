"""
Loads the generated test bed (backend/generated/*.csv) into memory once and
serves it to every API route. Deliberately CSV-backed rather than
Postgres-backed for now -- the DB schema and `run_generate.py --out db`
path already exist for when a real Postgres-backed deployment is wired up,
but every layer built so far has been developed and verified against the
CSV path, so the API reuses exactly that, rather than introducing a second,
less-tested data path just for the API.
"""
from __future__ import annotations

import os

import pandas as pd

_BASE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "generated")
_cache: dict | None = None


class DataNotGeneratedError(RuntimeError):
    pass


def load_all() -> dict:
    global _cache
    if _cache is not None:
        return _cache

    if not os.path.isdir(_BASE_DIR):
        raise DataNotGeneratedError(
            "No generated test bed found. Run `python -m data_gen.run_generate --out csv` "
            "from the backend/ directory first."
        )

    stages = {}
    for stage in ["raw", "staging", "warehouse", "mart"]:
        parse_dates = ["observed_at"] if stage != "mart" else ["observed_at", "sunrise", "sunset"]
        path = os.path.join(_BASE_DIR, f"{stage}.csv")
        stages[stage] = pd.read_csv(path, parse_dates=parse_dates)

    change_events = pd.read_csv(os.path.join(_BASE_DIR, "change_events.csv"), parse_dates=["occurred_at"])
    query_log = pd.read_csv(os.path.join(_BASE_DIR, "query_log.csv"))

    _cache = dict(stages=stages, change_events=change_events, query_log=query_log)
    return _cache


def get_stage_df(stage: str) -> pd.DataFrame:
    data = load_all()
    if stage not in data["stages"]:
        raise ValueError(f"Unknown stage '{stage}'. Valid stages: {list(data['stages'].keys())}")
    return data["stages"][stage]


def get_change_events() -> pd.DataFrame:
    return load_all()["change_events"]


def reset_cache() -> None:
    """Used by tests / after regenerating the test bed without restarting the server."""
    global _cache
    _cache = None
