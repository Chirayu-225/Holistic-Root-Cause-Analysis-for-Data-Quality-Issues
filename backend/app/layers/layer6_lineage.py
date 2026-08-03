"""
Layer 6: Lineage Traversal.

Core question: at which specific stage in the data pipeline was the defect
introduced? The most precise layer when lineage metadata exists, per the
framework doc (Section 8). Implements both paths the doc describes:

  8.1 WITH formal lineage:
      - Boundary Testing: walk upstream through the pipeline stages,
        re-testing at each boundary, until the defect stops appearing. The
        stage immediately after the last "clean" boundary is the injection
        point.
      - Transformation Code Inspection: once the injection stage is known,
        an LLM reads that stage's transformation code and correlates it
        with the defect pattern (app/llm/gemini_client.py).

  8.2 WITHOUT formal lineage (approximation): this test bed's synthetic
      query log (data_gen/synthetic_metadata.py) is mined via regex to
      reconstruct the same lineage graph the formal LineageEdge table
      already encodes -- proving the two independent techniques converge
      on the same answer, exactly as the framework doc's own worked example
      claims for query log mining ("the single most underused technique in
      the industry").

Honest scope note: this test bed's defects were injected directly into the
`warehouse` DataFrame in Python (see defect_injector.py), not by writing
genuinely buggy SQL into the simulated pipeline stages. For the humidity
Corruption defect, that Python injection deliberately mirrors a real
transform bug (the double-multiply is a faithful stand-in for a bad SQL
CASE expression), so boundary testing AND code inspection both point to a
real, plausible culprit. For Duplication and Staleness, the true root
cause is an *operational* event (a network retry, a vendor outage) rather
than buggy transform logic -- boundary testing still correctly and
honestly reports "the data changed between staging and warehouse," which
is factually true in this pipeline, but the transformation code at that
boundary is NOT actually the cause for those two. This is why Layer 5
(change event correlation) and Layer 6 (lineage traversal) are separate,
complementary layers rather than redundant ones: Layer 6 answers "which
stage," Layer 5 answers "what/why." Neither is sufficient alone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from app.layers.defect_presence import defect_present_in

STAGE_ORDER = ["raw", "staging", "warehouse", "mart"]


@dataclass
class LineageResult:
    stage_results: dict = field(default_factory=dict)   # stage -> True/False/None (present/absent/undefined)
    injection_upstream_stage: str | None = None
    injection_downstream_stage: str | None = None
    transform_description: str | None = None
    transform_code: str | None = None
    code_inspection: dict | None = None


def boundary_test(fingerprint: dict, stage_dataframes: dict, own_stage: str) -> LineageResult:
    """
    Walks upstream (backward through STAGE_ORDER) from the stage the defect
    was detected at, testing presence at each boundary, until the defect is
    absent or undefined -- that marks the injection point.
    """
    record_uids = set(fingerprint.get("_record_uids", []))
    own_idx = STAGE_ORDER.index(own_stage)
    stage_results = {}
    result = LineageResult()

    for i in range(own_idx, -1, -1):
        stage = STAGE_ORDER[i]
        df = stage_dataframes.get(stage)
        if df is None:
            stage_results[stage] = None
            result.injection_upstream_stage = stage
            result.injection_downstream_stage = STAGE_ORDER[i + 1] if i + 1 <= own_idx else stage
            break

        present = defect_present_in(df, fingerprint, record_uids)
        stage_results[stage] = present

        if present is not True:
            # False (clean) or None (undefined) both mark this as the last stage BEFORE injection
            result.injection_upstream_stage = stage
            result.injection_downstream_stage = STAGE_ORDER[i + 1] if i + 1 <= own_idx else stage
            break
    else:
        # walked all the way back to raw and the defect was present there too
        result.injection_upstream_stage = "<source>"
        result.injection_downstream_stage = STAGE_ORDER[0]

    result.stage_results = stage_results
    return result


def attach_transform_and_inspect(result: LineageResult, lineage_edges: list[dict], fingerprint: dict,
                                  run_llm: bool = True) -> LineageResult:
    """Looks up the transform code for the injection boundary and optionally runs LLM code inspection."""
    edge = next(
        (e for e in lineage_edges
         if e["upstream_table"].split(".")[0] == result.injection_upstream_stage
         and e["downstream_table"].split(".")[0] == result.injection_downstream_stage),
        None,
    )
    if edge is None:
        return result

    result.transform_description = edge["transform_description"]
    result.transform_code = edge["transform_code"]

    if run_llm:
        from app.llm.gemini_client import inspect_transform_code
        result.code_inspection = inspect_transform_code(fingerprint, edge["transform_description"], edge["transform_code"])
    return result


# --------------------------------------------------------------------------- #
# Query log mining (Section 8.2 -- the "without lineage" approximation path)
# --------------------------------------------------------------------------- #

_INSERT_RE = re.compile(r"INSERT INTO\s+(\S+)", re.IGNORECASE)
_FROM_RE = re.compile(r"FROM\s+(\S+?);?\s*$", re.IGNORECASE | re.MULTILINE)


def mine_lineage_from_query_log(query_log: pd.DataFrame) -> set[tuple[str, str]]:
    """
    Reconstructs (upstream_table, downstream_table) edges purely by
    regex-parsing the raw `query_text` of each log entry -- deliberately
    NOT using the pre-parsed `target_table`/`source_tables` columns already
    in the CSV, since those were written by the generator and would make
    this a label-reading exercise rather than genuine mining. This is
    meant to simulate what you'd actually do against a real
    `pg_stat_statements`-style query log with no structured lineage
    metadata at all.
    """
    edges = set()
    for text in query_log["query_text"].dropna().unique():
        insert_match = _INSERT_RE.search(text)
        from_match = _FROM_RE.search(text)
        if insert_match and from_match:
            downstream = insert_match.group(1).rstrip(";")
            upstream = from_match.group(1).rstrip(";")
            edges.add((upstream, downstream))
    return edges


if __name__ == "__main__":
    import os

    from app.layers.layer1_defect_characterization import characterize
    from data_gen.synthetic_metadata import LINEAGE_EDGES

    base = os.path.join(os.path.dirname(__file__), "..", "..", "generated")
    stage_dataframes = {
        stage: pd.read_csv(os.path.join(base, f"{stage}.csv"), parse_dates=["observed_at"])
        for stage in STAGE_ORDER
    }
    # mart also needs sunrise/sunset parsed as dates
    stage_dataframes["mart"] = pd.read_csv(
        os.path.join(base, "mart.csv"), parse_dates=["observed_at", "sunrise", "sunset"]
    )

    print("=== Layer 6a: Query log mining vs. formal lineage graph ===")
    query_log = pd.read_csv(os.path.join(base, "query_log.csv"))
    mined_edges = mine_lineage_from_query_log(query_log)
    formal_edges = {(e["upstream_table"], e["downstream_table"]) for e in LINEAGE_EDGES}
    print(f"Mined {len(mined_edges)} edges from {len(query_log)} query log entries:")
    for edge in sorted(mined_edges):
        print(f"  {edge[0]} -> {edge[1]}")
    print(f"Matches formal lineage graph exactly: {mined_edges == formal_edges}")
    print()

    print("=== Layer 6b: Boundary testing + code inspection: warehouse-stage defects ===")
    for fp in characterize(stage_dataframes["warehouse"]):
        result = boundary_test(fp, stage_dataframes, own_stage="warehouse")
        result = attach_transform_and_inspect(result, LINEAGE_EDGES, fp, run_llm=True)
        print(f"[{fp['defect_type']}] {fp['affected_fields']}")
        print(f"  stage presence: {result.stage_results}")
        print(f"  injection point: {result.injection_upstream_stage} -> {result.injection_downstream_stage}")
        if result.transform_code:
            print(f"  transform: {result.transform_description}")
            print(f"  code inspection [{result.code_inspection['source']}]: {result.code_inspection['explanation']}")
        print()

    print("=== Layer 6b: Boundary testing + code inspection: mart-stage Omission ===")
    for fp in characterize(stage_dataframes["mart"]):
        if fp["defect_type"] != "Omission":
            continue
        result = boundary_test(fp, stage_dataframes, own_stage="mart")
        result = attach_transform_and_inspect(result, LINEAGE_EDGES, fp, run_llm=True)
        print(f"[{fp['defect_type']}] {fp['affected_fields']}")
        print(f"  stage presence: {result.stage_results}")
        print(f"  injection point: {result.injection_upstream_stage} -> {result.injection_downstream_stage}")
        if result.transform_code:
            print(f"  transform: {result.transform_description}")
            print(f"  code inspection [{result.code_inspection['source']}]: {result.code_inspection['explanation']}")
        print()
