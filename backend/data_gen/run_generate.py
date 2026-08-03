"""
End-to-end data generation entrypoint.

Usage:
    python -m data_gen.run_generate --out csv          # writes CSVs to ./generated/ (no DB needed)
    python -m data_gen.run_generate --out db            # writes directly into Postgres via DATABASE_URL

This produces:
  generated/raw.csv, staging.csv, warehouse.csv, mart.csv   (pipeline stages)
  generated/ground_truth.csv                                 (the answer key for evaluation)
  generated/change_events.csv                                (Layer 5 fodder)
  generated/lineage_edges.csv, generated/query_log.csv        (Layer 6 fodder)
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime

import pandas as pd

from data_gen import defect_injector as di
from data_gen import pipeline_simulator as ps
from data_gen import synthetic_metadata as sm
from data_gen.synthetic_source import generate_synthetic

START_DATE = datetime(2026, 1, 1)
NUM_DAYS = 60


def build_dataset() -> dict:
    raw = generate_synthetic(START_DATE, NUM_DAYS)
    stages = ps.run_pipeline(raw)

    for col in ["_gt_is_defective", "_gt_defect_type", "_gt_root_cause_id"]:
        for stage_df in stages.values():
            stage_df[col] = None if col != "_gt_is_defective" else False

    ground_truths = []
    change_events = []

    warehouse = stages["warehouse"]
    warehouse, gt1, ev1 = di.inject_humidity_double_multiply(warehouse, START_DATE)
    warehouse, gt3, ev3 = di.inject_duplication(warehouse, START_DATE)
    warehouse, gt4, ev4 = di.inject_staleness(warehouse, START_DATE)
    stages["warehouse"] = warehouse
    ground_truths += [gt1, gt3, gt4]
    change_events += [ev1, ev3, ev4]

    # rebuild mart from the (now defective) warehouse so defects propagate downstream,
    # exactly like a real pipeline would
    mart = ps.to_mart(warehouse)
    mart, gt2, ev2 = di.inject_schema_omission(mart, START_DATE)
    stages["mart"] = mart
    ground_truths.append(gt2)
    change_events.append(ev2)

    lineage_edges = sm.LINEAGE_EDGES
    query_log = sm.generate_query_log(START_DATE, NUM_DAYS)

    return dict(
        stages=stages,
        ground_truth=pd.DataFrame(ground_truths),
        change_events=pd.DataFrame(change_events),
        lineage_edges=pd.DataFrame(lineage_edges),
        query_log=pd.DataFrame(query_log),
    )


def write_csv(dataset: dict, out_dir: str = "generated") -> None:
    os.makedirs(out_dir, exist_ok=True)
    for stage_name, df in dataset["stages"].items():
        df.to_csv(f"{out_dir}/{stage_name}.csv", index=False)
    dataset["ground_truth"].to_csv(f"{out_dir}/ground_truth.csv", index=False)
    dataset["change_events"].to_csv(f"{out_dir}/change_events.csv", index=False)
    dataset["lineage_edges"].to_csv(f"{out_dir}/lineage_edges.csv", index=False)
    dataset["query_log"].to_csv(f"{out_dir}/query_log.csv", index=False)
    print(f"Wrote CSVs to ./{out_dir}/")
    for stage_name, df in dataset["stages"].items():
        n_defective = int(df["_gt_is_defective"].fillna(False).sum())
        print(f"  {stage_name:10s}: {len(df):5d} rows  ({n_defective} ground-truth defective)")
    print(f"  ground_truth entries: {len(dataset['ground_truth'])}")
    print(f"  change_events       : {len(dataset['change_events'])}")


def write_db(dataset: dict) -> None:
    from app.db.models import (
        ChangeEvent,
        LineageEdge,
        PipelineStage,
        QueryLogEntry,
        WeatherRecord,
    )
    from app.db.session import SessionLocal, init_db

    init_db()
    db = SessionLocal()
    try:
        for stage_name, df in dataset["stages"].items():
            for _, row in df.iterrows():
                db.add(
                    WeatherRecord(
                        record_uid=row["record_uid"],
                        stage=PipelineStage(stage_name),
                        station_id=row["station_id"],
                        source_system=row["source_system"],
                        country=row.get("country"),
                        region=row.get("region"),
                        observed_at=row["observed_at"],
                        batch_id=row.get("batch_id"),
                        load_type=row.get("load_type"),
                        celsius_temperature=row.get("celsius_temperature"),
                        relative_humidity=row.get("relative_humidity", row.get("relative_humidity_raw")),
                        wind_speed=row.get("wind_speed"),
                        sea_level_pressure=row.get("sea_level_pressure"),
                        sunrise=row.get("sunrise"),
                        sunset=row.get("sunset"),
                        weather_description=row.get("weather_description"),
                        _gt_is_defective=bool(row.get("_gt_is_defective") or False),
                        _gt_defect_type=row.get("_gt_defect_type"),
                        _gt_root_cause_id=row.get("_gt_root_cause_id"),
                    )
                )
        for _, row in dataset["change_events"].iterrows():
            db.add(ChangeEvent(**row.to_dict()))
        for _, row in dataset["lineage_edges"].iterrows():
            db.add(LineageEdge(**row.to_dict()))
        for _, row in dataset["query_log"].iterrows():
            db.add(QueryLogEntry(**row.to_dict()))
        db.commit()
        print("Wrote dataset into Postgres.")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", choices=["csv", "db"], default="csv")
    args = parser.parse_args()

    dataset = build_dataset()
    if args.out == "csv":
        write_csv(dataset)
    else:
        write_db(dataset)
