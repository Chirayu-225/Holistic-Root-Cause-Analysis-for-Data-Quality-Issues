"""
Layer 3: Segmentation Analysis.

Core question: is the defect universal, or concentrated in a specific slice
of the data? Per the framework doc (Section 5), slice failing records by
every available categorical/temporal dimension and compute the defect rate
per slice -- if one segment value explains nearly all the defects, that's
often enough to solve the case without ever touching lineage.

This implementation goes one step further than a single-pass sweep: it
DRILLS DOWN. After finding the best single dimension, it re-runs the same
sweep *within* that segment looking for further refinement (e.g. "100% of
defects are in source_system=API_v3" -> "and within that, 100% are from
station X"), stopping only when no further dimension adds meaningful
specificity. This mirrors the framework doc's own worked example, which
isolates a defect down to a specific source + batch + station jointly, not
just one dimension in isolation.

Unlike Layer 2 (which re-derives an independent day-by-day signal), Layer 3
genuinely needs the EXACT set of violating records -- segmentation is
inherently about slicing those specific rows -- so it consumes the
`_record_uids` field Layer 1 attaches to each fingerprint.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

CANDIDATE_DIMENSIONS = ["source_system", "country", "region", "station_id", "batch_id", "load_type"]
COVERAGE_THRESHOLD = 0.8   # fraction of remaining defects a segment must explain to be "dominant"
MIN_SCOPE_SIZE = 5          # stop drilling once the remaining population is this small
MAX_DEPTH = 4                # safety cap on drill-down levels
COVERAGE_TIE_EPSILON = 0.001

# When multiple dimensions tie on coverage (e.g. a defect confined to one station in
# one country is 100% explained by BOTH source_system and country, since they're
# collinear for that station), prefer whichever dimension is more directly
# ACTIONABLE for investigation -- the actual system/entity boundary someone would go
# look at -- over one that merely happens to be statistically more "enriched" (higher
# lift from having a smaller population). This also makes downstream layers that
# correlate against operational metadata (Layer 5) more reliable, since real
# change/incident logs are typically scoped to systems and entities, not to
# incidental attributes like country.
DIMENSION_PRIORITY = {"source_system": 0, "station_id": 1, "batch_id": 2, "_day": 3,
                       "country": 4, "region": 5, "load_type": 6}


@dataclass
class SegmentLevel:
    dimension: str
    value: object
    coverage: float           # fraction of defects (at this drill level) explained by this value
    lift: float                # how many times more likely a record in this segment is defective vs. the scope baseline
    n_total_in_segment: int
    n_defective_in_segment: int


@dataclass
class SegmentationResult:
    path: list[SegmentLevel] = field(default_factory=list)
    is_scattered: bool = False
    statement: str = ""


def _dimension_sweep(df_scope: pd.DataFrame, defective_uids: set, dimension: str) -> pd.DataFrame:
    """
    For one dimension, computes per-value: n_total, n_defective, defect_rate,
    coverage (share of the scope's defects explained by this value), and
    lift (defect rate in this value vs. the scope's overall defect rate).

    Operates on unique record_uid space (one row per logical record) rather
    than raw physical rows. This matters because a record can physically
    appear more than once in the table -- either because the fingerprint
    under analysis IS a duplication defect, or because an unrelated
    duplication defect elsewhere happens to touch the same rows (this
    actually happens in the test bed: the injected duplication batch and
    the stale station's history overlap on one calendar day). Without this,
    coverage could exceed 100% simply from physical row counts, which would
    misrepresent what fraction of the *actual defects* a segment explains.
    """
    if dimension not in df_scope.columns or df_scope[dimension].dropna().empty:
        return pd.DataFrame()

    working = df_scope.drop_duplicates(subset=["record_uid"]).copy()
    scope_defect_rate = len(defective_uids) / len(working) if len(working) else 0
    if scope_defect_rate == 0:
        return pd.DataFrame()

    working["_is_defective"] = working["record_uid"].isin(defective_uids)

    grouped = working.groupby(dimension, dropna=True).agg(
        n_total=("record_uid", "size"), n_defective=("_is_defective", "sum")
    )
    grouped["defect_rate"] = grouped["n_defective"] / grouped["n_total"]
    grouped["coverage"] = grouped["n_defective"] / len(defective_uids)
    grouped["lift"] = grouped["defect_rate"] / scope_defect_rate
    return grouped.sort_values("coverage", ascending=False)


def _best_dimension_at_level(
    df_scope: pd.DataFrame, defective_uids: set, dimensions: list[str]
) -> SegmentLevel | None:
    """Among the given dimensions, finds the one whose top value best isolates the remaining defects."""
    candidates = []
    for dim in dimensions:
        sweep = _dimension_sweep(df_scope, defective_uids, dim)
        if sweep.empty:
            continue
        top = sweep.iloc[0]
        if top["coverage"] >= COVERAGE_THRESHOLD:
            candidates.append(
                SegmentLevel(
                    dimension=dim,
                    value=sweep.index[0],
                    coverage=float(top["coverage"]),
                    lift=float(top["lift"]),
                    n_total_in_segment=int(top["n_total"]),
                    n_defective_in_segment=int(top["n_defective"]),
                )
            )
    if not candidates:
        return None
    # Step 1: keep only candidates whose coverage is within epsilon of the best coverage found
    max_coverage = max(c.coverage for c in candidates)
    near_best = [c for c in candidates if max_coverage - c.coverage <= COVERAGE_TIE_EPSILON]
    # Step 2: among those, prefer the more actionable dimension (see DIMENSION_PRIORITY above)
    # Step 3: lift only breaks ties within the same priority tier
    return min(near_best, key=lambda c: (DIMENSION_PRIORITY.get(c.dimension, 99), -c.lift))


def segment(df: pd.DataFrame, fingerprint: dict) -> SegmentationResult:
    """
    Drills down through CANDIDATE_DIMENSIONS, at each level narrowing the
    scope to the best-isolating segment found so far, until no dimension
    adds further specificity, the scope gets too small, or MAX_DEPTH is hit.
    """
    defective_uids = set(fingerprint.get("_record_uids", []))
    if not defective_uids:
        return SegmentationResult(is_scattered=True, statement="No record-level evidence available to segment.")

    scope_df = df.copy()
    # derived Time Window dimension (framework doc Section 5.1): batch_id in this
    # test bed is per-hour, which is too granular to isolate a defect that spans a
    # whole calendar day across several hourly batches -- day is the coarser sibling.
    scope_df["_day"] = pd.to_datetime(scope_df["observed_at"]).dt.normalize()
    remaining_dims = CANDIDATE_DIMENSIONS + ["_day"]
    path: list[SegmentLevel] = []

    for _ in range(MAX_DEPTH):
        if len(scope_df) < MIN_SCOPE_SIZE or not remaining_dims:
            break
        best = _best_dimension_at_level(scope_df, defective_uids, remaining_dims)
        if best is None:
            break
        path.append(best)
        scope_df = scope_df[scope_df[best.dimension] == best.value]
        remaining_dims.remove(best.dimension)
        if best.coverage >= 0.999:
            # already fully explains the defects -- further dimensions can only add
            # redundant clauses (lift ~1.0), not genuine additional specificity
            break

    if not path:
        return SegmentationResult(
            is_scattered=True,
            statement=(
                "No single dimension explains a majority of the failures -- the defect appears "
                "systemic (affects a broad cross-section of the data) rather than tied to one "
                "source, region, station, or batch."
            ),
        )

    statement = _build_statement(path)
    return SegmentationResult(path=path, is_scattered=False, statement=statement)


def _build_statement(path: list[SegmentLevel]) -> str:
    clauses = [f"{level.dimension}={level.value}" for level in path]
    last = path[-1]
    joined = ", ".join(clauses)
    return (
        f"{last.coverage:.0%} of the remaining defects are explained by {joined} "
        f"({last.n_defective_in_segment}/{last.n_total_in_segment} records in that segment are defective, "
        f"a {last.lift:.1f}x higher rate than the surrounding population)."
    )


if __name__ == "__main__":
    import os

    from app.layers.layer1_defect_characterization import characterize

    base = os.path.join(os.path.dirname(__file__), "..", "..", "generated")
    warehouse = pd.read_csv(os.path.join(base, "warehouse.csv"), parse_dates=["observed_at"])
    mart = pd.read_csv(os.path.join(base, "mart.csv"), parse_dates=["observed_at", "sunrise", "sunset"])

    print("=== Layer 3 segmentation: warehouse stage ===")
    for fp in characterize(warehouse):
        result = segment(warehouse, fp)
        print(f"[{fp['defect_type']}] {fp['affected_fields']}")
        if result.is_scattered:
            print(f"  SCATTERED: {result.statement}")
        else:
            for level in result.path:
                print(f"  -> {level.dimension} = {level.value}  "
                      f"(coverage={level.coverage:.0%}, lift={level.lift:.1f}x, "
                      f"n={level.n_defective_in_segment}/{level.n_total_in_segment})")
            print(f"  STATEMENT: {result.statement}")
        print()

    print("=== Layer 3 segmentation: mart stage (Omission only) ===")
    for fp in characterize(mart):
        if fp["defect_type"] != "Omission":
            continue
        result = segment(mart, fp)
        print(f"[{fp['defect_type']}] {fp['affected_fields']}")
        if result.is_scattered:
            print(f"  SCATTERED: {result.statement}")
        else:
            for level in result.path:
                print(f"  -> {level.dimension} = {level.value}  "
                      f"(coverage={level.coverage:.0%}, lift={level.lift:.1f}x, "
                      f"n={level.n_defective_in_segment}/{level.n_total_in_segment})")
            print(f"  STATEMENT: {result.statement}")
        print()
