"""
Layer 5: Change Event Correlation.

Core question: what changed in the environment around the time the defect
appeared? Per the framework doc (Section 7), this layer needs access to
operational metadata -- deployment logs, schema change histories,
configuration management, incident logs -- not data lineage. It's the
first CONDITIONAL layer in the framework (vs. Layers 1-4, which are always
available): it only produces results if event-log access exists at all.

This is also the first layer to actually consume the synthetic change
events generated all the way back at the start of the project
(`data_gen/synthetic_metadata.py` / `defect_injector.py`), which up to now
have just been sitting in the database unused.

Method (framework doc Section 7.2): once Layer 2 gives an onset time, scan
all change events within a window around it (doc suggests +/-24-72h), then
rank candidates by:
  - Temporal proximity  -- closer to onset = higher suspicion
  - Scope overlap        -- does the event touch the same table/column as
                             the defect?
  - Segment match         -- does the event's scope match the same segment
                             Layer 3 isolated? (doc: "much higher suspicion")
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

WINDOW_HOURS = 72  # doc: "typically +/-24-72 hours"
WEIGHT_TEMPORAL = 0.3
WEIGHT_SCOPE = 0.3
WEIGHT_SEGMENT = 0.4  # doc: segment match carries "much higher suspicion" than the other two


@dataclass
class EventCorrelation:
    event_type: str
    occurred_at: pd.Timestamp
    description: str
    scope_table: str | None
    scope_column: str | None
    scope_source_system: str | None
    hours_from_onset: float
    temporal_score: float
    scope_score: float
    segment_score: float
    composite_score: float


def _temporal_score(occurred_at: pd.Timestamp, onset: pd.Timestamp, window_hours: int) -> tuple[float, float]:
    hours = (occurred_at - onset).total_seconds() / 3600
    score = max(0.0, 1 - abs(hours) / window_hours)
    return score, hours


def _scope_score(event_row, fingerprint: dict) -> float:
    """
    1.0 if the event's scope_column explicitly names one of the fingerprint's
    affected fields; 0.5 if only the table matches (same pipeline stage, but
    no column-level specificity); 0.0 otherwise.
    """
    own_fields = set(fingerprint["affected_fields"])
    scope_cols = set(str(event_row.get("scope_column") or "").split(","))
    scope_cols.discard("")
    if own_fields & scope_cols:
        return 1.0
    if pd.notna(event_row.get("scope_table")):
        return 0.5
    return 0.0


def _segment_score(event_row, fingerprint: dict) -> float:
    """
    1.0 if the event's scope_source_system matches the fingerprint's
    dominant segment (from Layer 1 -- always evaluated against source_system,
    so it's the reliable match target here regardless of which dimension
    Layer 3 ultimately drilled to). Real operational logs are typically
    scoped at the system/vendor level, not down to individual entities, so
    source_system is the realistic granularity to match against.
    """
    seg_col = fingerprint.get("dominant_segment_column")
    seg_val = fingerprint.get("dominant_segment_value")
    if seg_col != "source_system" or seg_val is None:
        return 0.0
    return 1.0 if event_row.get("scope_source_system") == seg_val else 0.0


def correlate_change_events(
    fingerprint: dict, onset: pd.Timestamp | None, change_events: pd.DataFrame, window_hours: int = WINDOW_HOURS
) -> list[EventCorrelation]:
    if onset is None or change_events.empty:
        return []

    results = []
    for _, row in change_events.iterrows():
        occurred_at = pd.to_datetime(row["occurred_at"])
        temporal, hours = _temporal_score(occurred_at, onset, window_hours)
        if temporal <= 0:
            continue  # outside the correlation window entirely -- not a candidate
        scope = _scope_score(row, fingerprint)
        segment = _segment_score(row, fingerprint)
        composite = WEIGHT_TEMPORAL * temporal + WEIGHT_SCOPE * scope + WEIGHT_SEGMENT * segment

        results.append(EventCorrelation(
            event_type=row["event_type"], occurred_at=occurred_at, description=row["description"],
            scope_table=row.get("scope_table"), scope_column=row.get("scope_column"),
            scope_source_system=row.get("scope_source_system"), hours_from_onset=hours,
            temporal_score=temporal, scope_score=scope, segment_score=segment, composite_score=composite,
        ))
    return sorted(results, key=lambda r: r.composite_score, reverse=True)


def build_statement(correlations: list[EventCorrelation]) -> str:
    if not correlations:
        return "No change events fall within the correlation window -- no operational cause identified."
    top = correlations[0]
    direction = "after" if top.hours_from_onset >= 0 else "before"
    return (
        f"Best-matching change event: [{top.event_type}] \"{top.description}\" -- occurred "
        f"{abs(top.hours_from_onset):.0f}h {direction} the detected onset "
        f"(temporal={top.temporal_score:.2f}, scope={top.scope_score:.2f}, segment={top.segment_score:.2f}, "
        f"composite={top.composite_score:.2f})."
    )


if __name__ == "__main__":
    import os

    from app.layers.layer1_defect_characterization import characterize
    from app.layers.layer2_temporal import analyze_temporal

    base = os.path.join(os.path.dirname(__file__), "..", "..", "generated")
    warehouse = pd.read_csv(os.path.join(base, "warehouse.csv"), parse_dates=["observed_at"])
    mart = pd.read_csv(os.path.join(base, "mart.csv"), parse_dates=["observed_at", "sunrise", "sunset"])
    change_events = pd.read_csv(os.path.join(base, "change_events.csv"), parse_dates=["occurred_at"])

    print("=== Layer 5 change event correlation: warehouse stage ===")
    for fp in characterize(warehouse):
        temporal_result = analyze_temporal(warehouse, fp)
        correlations = correlate_change_events(fp, temporal_result.onset, change_events)
        print(f"[{fp['defect_type']}] {fp['affected_fields']}  (onset={temporal_result.onset})")
        for c in correlations[:3]:
            print(f"  {c.composite_score:.2f}  [{c.event_type}] {c.description}  "
                  f"(t={c.temporal_score:.2f}, scope={c.scope_score:.2f}, seg={c.segment_score:.2f})")
        print(f"  -> {build_statement(correlations)}")
        print()

    print("=== Layer 5 change event correlation: mart stage (Omission only) ===")
    for fp in characterize(mart):
        if fp["defect_type"] != "Omission":
            continue
        temporal_result = analyze_temporal(mart, fp)
        correlations = correlate_change_events(fp, temporal_result.onset, change_events)
        print(f"[{fp['defect_type']}] {fp['affected_fields']}  (onset={temporal_result.onset})")
        for c in correlations[:3]:
            print(f"  {c.composite_score:.2f}  [{c.event_type}] {c.description}  "
                  f"(t={c.temporal_score:.2f}, scope={c.scope_score:.2f}, seg={c.segment_score:.2f})")
        print(f"  -> {build_statement(correlations)}")
        print()
