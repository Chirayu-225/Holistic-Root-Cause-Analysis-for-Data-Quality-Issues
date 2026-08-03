"""
Generates the two Layer 6 evidence sources:
  - LineageEdge rows: the explicit, formal lineage graph ("with lineage" path)
  - QueryLogEntry rows: INSERT...SELECT statements mined to *approximate*
    lineage ("without lineage" path) -- so the demo can show both techniques
    recovering the same answer independently.
"""
from __future__ import annotations

from datetime import timedelta

LINEAGE_EDGES = [
    dict(
        upstream_table="raw.weather_ingest",
        downstream_table="staging.weather_typed",
        transform_description="Type casting, timestamp parsing, surrogate key assignment. No business logic.",
        transform_code=(
            "INSERT INTO staging.weather_typed\n"
            "SELECT record_uid, station_id, source_system,\n"
            "       CAST(observed_at AS TIMESTAMP) AS observed_at,\n"
            "       relative_humidity_raw, celsius_temperature\n"
            "FROM raw.weather_ingest;"
        ),
    ),
    dict(
        upstream_table="staging.weather_typed",
        downstream_table="warehouse.weather_clean",
        transform_description=(
            "Business transform: normalize relative_humidity_raw to a 0-100 scale. "
            "API_v3 sends a 0-1 fraction and must be multiplied by 100; NOAA_ISD "
            "already sends 0-100 and must be passed through unchanged."
        ),
        transform_code=(
            "INSERT INTO warehouse.weather_clean\n"
            "SELECT *,\n"
            "  CASE WHEN source_system = 'API_v3'\n"
            "       THEN relative_humidity_raw * 100\n"
            "       ELSE relative_humidity_raw END AS relative_humidity\n"
            "FROM staging.weather_typed;"
        ),
    ),
    dict(
        upstream_table="warehouse.weather_clean",
        downstream_table="mart.weather_daily",
        transform_description="Derive sunrise/sunset from observed_at date; expose to BI/consumption layer.",
        transform_code=(
            "INSERT INTO mart.weather_daily\n"
            "SELECT *,\n"
            "  DATE_TRUNC('day', observed_at) + INTERVAL '6 hours' AS sunrise,\n"
            "  DATE_TRUNC('day', observed_at) + INTERVAL '18 hours' AS sunset\n"
            "FROM warehouse.weather_clean;"
        ),
    ),
]


def generate_query_log(start_date, num_days: int = 60) -> list[dict]:
    """
    Emits one INSERT...SELECT per stage transition per day, mimicking a daily
    batch job -- this is exactly the pattern Layer 6's query-log-mining
    technique looks for to reconstruct lineage edges without a formal catalog.
    """
    entries = []
    for day in range(num_days):
        run_time = start_date + timedelta(days=day, hours=1)  # nightly batch job at 01:00
        for edge in LINEAGE_EDGES:
            entries.append(
                dict(
                    executed_at=run_time,
                    query_text=edge["transform_code"],
                    target_table=edge["downstream_table"],
                    source_tables=[edge["upstream_table"]],
                    job_name=f"nightly_etl_{edge['downstream_table'].split('.')[0]}",
                )
            )
    return entries
