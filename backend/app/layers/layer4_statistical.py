"""
Layer 4: Statistical Profiling & Cross-Field Analysis.

Core question: what other data anomalies co-occur with the defect, and what
correlations exist between fields? This layer moves beyond the failing
field itself to examine the entire record -- per the framework doc, the
"healthy vs. failing" case-vs-control comparison paradigm (Section 6.2) is
the single most powerful lineage-free RCA technique, because it doesn't
require knowing anything about the pipeline's internals: just two groups
of rows to compare.

Four techniques implemented, all working off the same case (failing) vs.
control (healthy) split:

  1. Case-vs-control column profiling (6.2): every column's distribution is
     compared between the two groups and ranked by how different it is --
     the framework doc's "differential analysis."
  2. Null co-occurrence (6.1): among columns OTHER than the fingerprint's
     own affected fields, which ones are also disproportionately null in
     the failing rows? Clusters of co-null fields often share a root cause.
  3. Cross-field correlation shift (6.1): do any two numeric fields that
     are roughly independent in healthy data become strongly correlated in
     failing data (or vice versa)? A shift like that suggests a
     transformation coupled fields that shouldn't be coupled.
  4. Value domain novelty (6.1): for categorical fields, are there values
     present in failing rows that never appear in healthy rows at all?
     Novel values are injection-type candidates.

Note on our test bed: the four injected defects are each single-cause and
don't have deliberately co-occurring secondary anomalies, so this layer's
role here is mostly CORROBORATION -- confirming what Layers 1/2/3 already
found via an independent statistical lens, and honestly reporting "nothing
further found" where that's the true answer, rather than manufacturing
findings. That's intentional and matches the framework's design principle:
more independent signal raises confidence even when every layer converges
on the same answer, and a layer that finds nothing further is not a layer
that's broken.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp

EXCLUDE_COLUMNS = {"record_uid", "stage", "_gt_is_defective", "_gt_defect_type", "_gt_root_cause_id"}
NUMERIC_FIELDS = ["celsius_temperature", "relative_humidity", "wind_speed", "sea_level_pressure"]
CATEGORICAL_FIELDS = ["source_system", "country", "region", "load_type", "weather_description"]
NULL_COOCCUR_THRESHOLD = 0.10     # min null-rate delta to call a field "co-null" with the primary defect
CORRELATION_SHIFT_THRESHOLD = 0.3  # min |corr_failing - corr_healthy| to flag a cross-field shift
TOP_N_DELTAS = 6


@dataclass
class StatisticalProfile:
    ranked_deltas: list[dict] = field(default_factory=list)
    co_null_fields: list[dict] = field(default_factory=list)
    correlation_shifts: list[dict] = field(default_factory=list)
    novel_values: dict = field(default_factory=dict)
    summary: str = ""


def _split_case_control(df: pd.DataFrame, defective_uids: set) -> tuple[pd.DataFrame, pd.DataFrame]:
    is_case = df["record_uid"].isin(defective_uids)
    return df[is_case], df[~is_case]


def _column_delta(failing: pd.Series, healthy: pd.Series, is_numeric: bool) -> dict:
    null_rate_f, null_rate_h = failing.isna().mean(), healthy.isna().mean()
    null_delta = abs(null_rate_f - null_rate_h)
    nf, nh = failing.dropna(), healthy.dropna()

    if is_numeric and len(nf) >= 2 and len(nh) >= 2:
        stat, pvalue = ks_2samp(nf, nh)
        dist_score, kind, extra = float(stat), "numeric", {
            "mean_failing": float(nf.mean()), "mean_healthy": float(nh.mean()),
            "std_failing": float(nf.std()), "std_healthy": float(nh.std()), "ks_pvalue": float(pvalue),
        }
    elif not is_numeric and (len(nf) or len(nh)):
        vf, vh = nf.value_counts(normalize=True), nh.value_counts(normalize=True)
        all_vals = set(vf.index) | set(vh.index)
        tvd = 0.5 * sum(abs(vf.get(v, 0) - vh.get(v, 0)) for v in all_vals)
        dist_score, kind, extra = float(tvd), "categorical", {
            "top_failing": vf.index[0] if len(vf) else None, "top_healthy": vh.index[0] if len(vh) else None,
        }
    else:
        dist_score, kind, extra = 0.0, "numeric" if is_numeric else "categorical", {}

    return dict(
        null_rate_failing=float(null_rate_f), null_rate_healthy=float(null_rate_h), null_delta=float(null_delta),
        distribution_shift=dist_score, kind=kind, overall_score=max(null_delta, dist_score), **extra,
    )


def rank_column_deltas(df: pd.DataFrame, defective_uids: set) -> list[dict]:
    """Case-vs-control profiling (framework doc Section 6.2), ranked by how different each column is."""
    failing, healthy = _split_case_control(df, defective_uids)
    results = []
    for col in df.columns:
        if col in EXCLUDE_COLUMNS:
            continue
        is_numeric = pd.api.types.is_numeric_dtype(df[col]) and col not in ("observed_at",)
        delta = _column_delta(failing[col], healthy[col], is_numeric)
        results.append(dict(column=col, **delta))
    return sorted(results, key=lambda r: r["overall_score"], reverse=True)


def find_co_null_fields(ranked_deltas: list[dict], own_fields: list[str]) -> list[dict]:
    """Fields OTHER than the fingerprint's own affected fields that are also disproportionately null."""
    return [
        {"column": r["column"], "null_delta": r["null_delta"],
         "null_rate_failing": r["null_rate_failing"], "null_rate_healthy": r["null_rate_healthy"]}
        for r in ranked_deltas
        if r["column"] not in own_fields and r["null_delta"] >= NULL_COOCCUR_THRESHOLD
    ]


def find_correlation_shifts(df: pd.DataFrame, defective_uids: set) -> list[dict]:
    """
    Numeric field pairs whose Pearson correlation shifts substantially AND
    significantly between case and control groups.

    A raw threshold on |corr_failing - corr_healthy| alone is unreliable
    when the failing group is small (our Duplication and Omission
    fingerprints only have 36-60 rows) -- correlation estimates on small
    samples are noisy, and a large-looking delta can easily be sampling
    noise rather than a real shift. Fisher's r-to-z transformation gives a
    proper significance test for "do these two correlations actually
    differ," accounting for both groups' sample sizes directly, rather than
    trying to guess a sample-size cutoff that works for every case.
    """
    failing, healthy = _split_case_control(df, defective_uids)
    available = [f for f in NUMERIC_FIELDS if f in df.columns]
    shifts = []
    for a, b in combinations(available, 2):
        fa, fb = failing[[a, b]].dropna(), healthy[[a, b]].dropna()
        n1, n2 = len(fa), len(fb)
        if n1 < 5 or n2 < 5:
            continue
        corr_f = fa[a].corr(fa[b])
        corr_h = fb[a].corr(fb[b])
        if pd.isna(corr_f) or pd.isna(corr_h):
            continue
        delta = abs(corr_f - corr_h)
        if delta < CORRELATION_SHIFT_THRESHOLD:
            continue

        # Fisher r-to-z significance test
        r_f = np.clip(corr_f, -0.999, 0.999)
        r_h = np.clip(corr_h, -0.999, 0.999)
        z_f, z_h = np.arctanh(r_f), np.arctanh(r_h)
        se = np.sqrt(1 / (n1 - 3) + 1 / (n2 - 3)) if n1 > 3 and n2 > 3 else None
        if se is None or se == 0:
            continue
        z_stat = (z_f - z_h) / se
        p_value = float(2 * (1 - _std_normal_cdf(abs(z_stat))))
        if p_value < 0.05:
            shifts.append(dict(field_a=a, field_b=b, corr_failing=float(corr_f), corr_healthy=float(corr_h),
                                delta=float(delta), n_failing=n1, n_healthy=n2, p_value=p_value))
    return sorted(shifts, key=lambda s: s["delta"], reverse=True)


def _std_normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / np.sqrt(2)))


def find_novel_values(df: pd.DataFrame, defective_uids: set) -> dict:
    """Categorical values that appear in failing rows but never in healthy rows -- injection candidates."""
    failing, healthy = _split_case_control(df, defective_uids)
    novel = {}
    for col in CATEGORICAL_FIELDS:
        if col not in df.columns:
            continue
        failing_vals = set(failing[col].dropna().unique())
        healthy_vals = set(healthy[col].dropna().unique())
        only_in_failing = failing_vals - healthy_vals
        if only_in_failing:
            novel[col] = sorted(only_in_failing)
    return novel


def analyze_statistical(df: pd.DataFrame, fingerprint: dict) -> StatisticalProfile:
    defective_uids = set(fingerprint.get("_record_uids", []))
    if not defective_uids:
        return StatisticalProfile(summary="No record-level evidence available to profile.")

    own_fields = fingerprint["affected_fields"]
    ranked = rank_column_deltas(df, defective_uids)
    co_null = find_co_null_fields(ranked, own_fields)
    corr_shifts = find_correlation_shifts(df, defective_uids)
    novel = find_novel_values(df, defective_uids)

    summary = _build_summary(ranked[:TOP_N_DELTAS], own_fields, co_null, corr_shifts, novel)
    return StatisticalProfile(
        ranked_deltas=ranked[:TOP_N_DELTAS], co_null_fields=co_null,
        correlation_shifts=corr_shifts, novel_values=novel, summary=summary,
    )


def _build_summary(top_deltas: list[dict], own_fields: list[str], co_null: list[dict],
                    corr_shifts: list[dict], novel: dict) -> str:
    lines = []
    other_top = [d for d in top_deltas if d["column"] not in own_fields][:3]
    if other_top:
        cols = ", ".join(f"{d['column']} (score={d['overall_score']:.2f})" for d in other_top)
        lines.append(f"Beyond the affected field(s) itself, the most-shifted columns are: {cols}.")
    else:
        lines.append("No other column shows a meaningful distributional shift between failing and healthy rows.")

    if co_null:
        cols = ", ".join(c["column"] for c in co_null)
        lines.append(f"Co-null with the defect: {cols} -- these likely share a root cause with the primary field.")
    else:
        lines.append("No co-occurring null pattern found in other fields.")

    if corr_shifts:
        top = corr_shifts[0]
        lines.append(
            f"Cross-field correlation shift detected: {top['field_a']} vs {top['field_b']} "
            f"(healthy r={top['corr_healthy']:.2f}, failing r={top['corr_failing']:.2f}) -- "
            "a transformation may have coupled fields that shouldn't be coupled."
        )
    else:
        lines.append("No cross-field correlation shift detected among numeric fields.")

    if novel:
        parts = ", ".join(f"{col}={vals}" for col, vals in novel.items())
        lines.append(f"Novel categorical values found only in failing rows: {parts} -- possible injection.")
    else:
        lines.append("No novel categorical values found only in failing rows.")

    return " ".join(lines)


if __name__ == "__main__":
    import os

    from app.layers.layer1_defect_characterization import characterize

    base = os.path.join(os.path.dirname(__file__), "..", "..", "generated")
    warehouse = pd.read_csv(os.path.join(base, "warehouse.csv"), parse_dates=["observed_at"])
    mart = pd.read_csv(os.path.join(base, "mart.csv"), parse_dates=["observed_at", "sunrise", "sunset"])

    print("=== Layer 4 statistical profiling: warehouse stage ===")
    for fp in characterize(warehouse):
        profile = analyze_statistical(warehouse, fp)
        print(f"[{fp['defect_type']}] {fp['affected_fields']}")
        print(f"  top deltas: {[(d['column'], round(d['overall_score'], 2)) for d in profile.ranked_deltas[:4]]}")
        print(f"  SUMMARY: {profile.summary}")
        print()

    print("=== Layer 4 statistical profiling: mart stage (Omission only) ===")
    for fp in characterize(mart):
        if fp["defect_type"] != "Omission":
            continue
        profile = analyze_statistical(mart, fp)
        print(f"[{fp['defect_type']}] {fp['affected_fields']}")
        print(f"  top deltas: {[(d['column'], round(d['overall_score'], 2)) for d in profile.ranked_deltas[:4]]}")
        print(f"  SUMMARY: {profile.summary}")
        print()
