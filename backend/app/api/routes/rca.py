"""
API routes for the Holistic RCA Framework.

Each layer already exists as a standalone, independently-verified function
(app/layers/layer*.py, app/validation/validate.py) -- these routes are
deliberately thin wrappers around that existing logic, not a
reimplementation. The one new piece of design here is the fingerprint ID
scheme: fingerprints aren't persisted with stable database IDs yet (that's
what the DefectFingerprint table is FOR once a real RCA-run persistence
flow exists), so a deterministic ID is derived from
(stage, defect_type, affected_fields) instead -- stable across requests as
long as the underlying data doesn't change, and human-readable for anyone
poking at the API directly.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api import data_loader
from app.api.json_safety import jsonify
from app.layers.layer1_defect_characterization import characterize
from app.layers.layer2_temporal import analyze_temporal
from app.layers.layer3_segmentation import segment
from app.layers.layer4_statistical import analyze_statistical
from app.layers.layer5_change_events import build_statement as build_l5_statement
from app.layers.layer5_change_events import correlate_change_events
from app.layers.layer6_lineage import attach_transform_and_inspect, boundary_test, mine_lineage_from_query_log
from app.validation.validate import (
    ValidationReport,
    counterfactual_segment_test,
    fix_and_verify_humidity_corruption,
    reproduce_humidity_corruption,
)
from data_gen.synthetic_metadata import LINEAGE_EDGES

router = APIRouter(prefix="/api", tags=["rca"])


def _fingerprint_id(stage: str, fp: dict) -> str:
    return f"{stage}:{fp['defect_type']}:{'_'.join(fp['affected_fields'])}"


def _find_fingerprint(stage: str, fingerprint_id: str) -> tuple:
    try:
        df = data_loader.get_stage_df(stage)
    except (ValueError, data_loader.DataNotGeneratedError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    for fp in characterize(df):
        if _fingerprint_id(stage, fp) == fingerprint_id:
            return df, fp
    raise HTTPException(status_code=404, detail=f"No fingerprint '{fingerprint_id}' found in stage '{stage}'.")


def _run_validation(df, fp: dict) -> ValidationReport:
    checks = [counterfactual_segment_test(df, fp)]
    if fp["defect_type"] == "Corruption" and "relative_humidity" in fp["affected_fields"]:
        checks.append(reproduce_humidity_corruption(df, fp))
        checks.append(fix_and_verify_humidity_corruption(df, fp))
    return ValidationReport(checks)


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/stages")
def list_stages():
    """Row counts per pipeline stage -- a quick sanity check that the test bed loaded correctly."""
    try:
        data = data_loader.load_all()
    except data_loader.DataNotGeneratedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {stage: len(df) for stage, df in data["stages"].items()}


@router.get("/fingerprints")
def list_fingerprints(stage: str = Query("warehouse", description="raw | staging | warehouse | mart")):
    """Layer 1: every defect fingerprint detected at this pipeline stage."""
    try:
        df = data_loader.get_stage_df(stage)
    except (ValueError, data_loader.DataNotGeneratedError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    fingerprints = characterize(df)
    return jsonify([{"fingerprint_id": _fingerprint_id(stage, fp), "stage": stage, **fp} for fp in fingerprints])


@router.get("/fingerprints/{fingerprint_id}/temporal")
def get_temporal(fingerprint_id: str, stage: str = "warehouse"):
    """Layer 2: onset detection + temporal pattern classification."""
    df, fp = _find_fingerprint(stage, fingerprint_id)
    result = analyze_temporal(df, fp)
    return jsonify({
        "onset": result.onset, "technique": result.technique,
        "pattern": result.pattern, "evidence": result.evidence,
    })


@router.get("/fingerprints/{fingerprint_id}/segmentation")
def get_segmentation(fingerprint_id: str, stage: str = "warehouse"):
    """Layer 3: multi-dimensional drill-down to the defect's true segment."""
    df, fp = _find_fingerprint(stage, fingerprint_id)
    result = segment(df, fp)
    return jsonify({
        "is_scattered": result.is_scattered, "statement": result.statement,
        "path": [
            {"dimension": lvl.dimension, "value": lvl.value, "coverage": lvl.coverage, "lift": lvl.lift,
             "n_total_in_segment": lvl.n_total_in_segment, "n_defective_in_segment": lvl.n_defective_in_segment}
            for lvl in result.path
        ],
    })


@router.get("/fingerprints/{fingerprint_id}/statistical")
def get_statistical(fingerprint_id: str, stage: str = "warehouse"):
    """Layer 4: case-vs-control profiling, null co-occurrence, correlation shifts, novel values."""
    df, fp = _find_fingerprint(stage, fingerprint_id)
    profile = analyze_statistical(df, fp)
    return jsonify({
        "ranked_deltas": profile.ranked_deltas, "co_null_fields": profile.co_null_fields,
        "correlation_shifts": profile.correlation_shifts, "novel_values": profile.novel_values,
        "summary": profile.summary,
    })


@router.get("/fingerprints/{fingerprint_id}/change-events")
def get_change_events(fingerprint_id: str, stage: str = "warehouse"):
    """Layer 5: correlated operational change events, ranked by temporal proximity, scope, and segment match."""
    df, fp = _find_fingerprint(stage, fingerprint_id)
    temporal_result = analyze_temporal(df, fp)
    change_events = data_loader.get_change_events()
    correlations = correlate_change_events(fp, temporal_result.onset, change_events)
    return jsonify({
        "onset_used": temporal_result.onset,
        "statement": build_l5_statement(correlations),
        "correlations": [
            {"event_type": c.event_type, "occurred_at": c.occurred_at, "description": c.description,
             "hours_from_onset": c.hours_from_onset, "temporal_score": c.temporal_score,
             "scope_score": c.scope_score, "segment_score": c.segment_score, "composite_score": c.composite_score}
            for c in correlations
        ],
    })


@router.get("/fingerprints/{fingerprint_id}/lineage")
def get_lineage(fingerprint_id: str, stage: str = "warehouse", run_llm: bool = True):
    """Layer 6: boundary testing across pipeline stages + optional LLM transformation code inspection."""
    _, fp = _find_fingerprint(stage, fingerprint_id)
    stage_dataframes = data_loader.load_all()["stages"]
    result = boundary_test(fp, stage_dataframes, own_stage=stage)
    result = attach_transform_and_inspect(result, LINEAGE_EDGES, fp, run_llm=run_llm)
    return jsonify({
        "stage_results": result.stage_results,
        "injection_upstream_stage": result.injection_upstream_stage,
        "injection_downstream_stage": result.injection_downstream_stage,
        "transform_description": result.transform_description,
        "transform_code": result.transform_code,
        "code_inspection": result.code_inspection,
    })


@router.get("/lineage/query-log-mining")
def get_query_log_mining():
    """Layer 6b: reconstructs the lineage graph from raw query log text alone, no formal catalog used."""
    data = data_loader.load_all()
    mined = mine_lineage_from_query_log(data["query_log"])
    formal = {(e["upstream_table"], e["downstream_table"]) for e in LINEAGE_EDGES}
    return jsonify({
        "mined_edges": sorted(list(mined)),
        "matches_formal_lineage_exactly": mined == formal,
    })


@router.get("/fingerprints/{fingerprint_id}/validation")
def get_validation(fingerprint_id: str, stage: str = "warehouse"):
    """Validation & Confirmation: empirically tests hypotheses rather than trusting layer output directly."""
    df, fp = _find_fingerprint(stage, fingerprint_id)
    report = _run_validation(df, fp)
    return jsonify({
        "verdict": report.verdict,
        "checks": [{"technique": c.technique, "passed": c.passed, "detail": c.detail} for c in report.checks],
    })


@router.get("/fingerprints/{fingerprint_id}/report")
def get_full_report(fingerprint_id: str, stage: str = "warehouse", run_llm: bool = True):
    """
    Everything at once: all six layers plus validation for one fingerprint,
    combined into a single payload. This is the endpoint the frontend's
    detail view is built around.
    """
    df, fp = _find_fingerprint(stage, fingerprint_id)

    temporal_result = analyze_temporal(df, fp)
    segmentation_result = segment(df, fp)
    statistical_profile = analyze_statistical(df, fp)

    change_events = data_loader.get_change_events()
    correlations = correlate_change_events(fp, temporal_result.onset, change_events)

    stage_dataframes = data_loader.load_all()["stages"]
    lineage_result = boundary_test(fp, stage_dataframes, own_stage=stage)
    lineage_result = attach_transform_and_inspect(lineage_result, LINEAGE_EDGES, fp, run_llm=run_llm)

    validation_report = _run_validation(df, fp)

    return jsonify({
        "fingerprint": {"fingerprint_id": fingerprint_id, "stage": stage, **fp},
        "temporal": {
            "onset": temporal_result.onset, "technique": temporal_result.technique,
            "pattern": temporal_result.pattern, "evidence": temporal_result.evidence,
        },
        "segmentation": {
            "is_scattered": segmentation_result.is_scattered, "statement": segmentation_result.statement,
            "path": [
                {"dimension": lvl.dimension, "value": lvl.value, "coverage": lvl.coverage, "lift": lvl.lift}
                for lvl in segmentation_result.path
            ],
        },
        "statistical": {
            "ranked_deltas": statistical_profile.ranked_deltas[:5], "co_null_fields": statistical_profile.co_null_fields,
            "correlation_shifts": statistical_profile.correlation_shifts, "novel_values": statistical_profile.novel_values,
            "summary": statistical_profile.summary,
        },
        "change_events": {
            "statement": build_l5_statement(correlations),
            "top_matches": [
                {"event_type": c.event_type, "description": c.description, "composite_score": c.composite_score}
                for c in correlations[:3]
            ],
        },
        "lineage": {
            "injection_upstream_stage": lineage_result.injection_upstream_stage,
            "injection_downstream_stage": lineage_result.injection_downstream_stage,
            "transform_description": lineage_result.transform_description,
            "code_inspection": lineage_result.code_inspection,
        },
        "validation": {
            "verdict": validation_report.verdict,
            "checks": [{"technique": c.technique, "passed": c.passed, "detail": c.detail}
                       for c in validation_report.checks],
        },
    })
