"""
Database models for the Holistic RCA Framework.

Two model groups:
  1. PIPELINE DATA  — the simulated enterprise data pipeline (raw -> staging -> warehouse -> mart)
     that the RCA framework analyzes. This is what gets defects injected into it.
  2. RCA METADATA    — everything the six layers produce and consume: defect fingerprints,
     change events, lineage graph, query logs, hypotheses, and the knowledge base.

Ground truth columns (prefixed `_gt_`) are populated ONLY by the data generator / defect
injector, and are NEVER read by the RCA engine itself. They exist purely so we can score
the framework's hypotheses against the real answer during evaluation.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def gen_uuid() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
# 1. PIPELINE DATA
# --------------------------------------------------------------------------- #

class PipelineStage(str, enum.Enum):
    RAW = "raw"
    STAGING = "staging"
    WAREHOUSE = "warehouse"
    MART = "mart"


class WeatherRecord(Base):
    """
    A single weather observation, present at every pipeline stage.
    `stage` + `record_uid` (stable across stages) lets Layer 6 boundary-testing
    walk the same logical record upstream through raw -> staging -> warehouse -> mart.
    """
    __tablename__ = "weather_records"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    record_uid = Column(String, nullable=False, index=True)  # stable logical id across stages
    stage = Column(Enum(PipelineStage), nullable=False, index=True)

    station_id = Column(String, index=True, nullable=False)
    source_system = Column(String, index=True, nullable=False)  # e.g. NOAA_ISD, API_v3
    country = Column(String, index=True)
    region = Column(String, index=True)

    observed_at = Column(DateTime, index=True, nullable=False)
    ingested_at = Column(DateTime, index=True, nullable=False, default=datetime.utcnow)
    batch_id = Column(String, index=True)
    load_type = Column(String)  # full | incremental

    celsius_temperature = Column(Float)
    relative_humidity = Column(Float)
    wind_speed = Column(Float)
    sea_level_pressure = Column(Float)
    sunrise = Column(DateTime)
    sunset = Column(DateTime)
    weather_description = Column(String)

    # --- ground truth (generator-only, never read by RCA engine) ---
    _gt_is_defective = Column(Boolean, default=False)
    _gt_defect_type = Column(String, nullable=True)
    _gt_root_cause_id = Column(String, nullable=True)

    __table_args__ = (
        Index("ix_weather_stage_source_time", "stage", "source_system", "observed_at"),
    )


# --------------------------------------------------------------------------- #
# 2. RCA METADATA
# --------------------------------------------------------------------------- #

class DefectFingerprint(Base):
    """Output of Layer 1."""
    __tablename__ = "defect_fingerprints"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    dq_dimension = Column(String, nullable=False)          # Accuracy, Validity, Completeness, ...
    affected_fields = Column(JSON, nullable=False)          # list[str]
    failure_pattern = Column(Text)
    failure_volume = Column(Integer)
    failure_total = Column(Integer)
    failure_distribution = Column(Text)
    defect_type = Column(String, nullable=False)             # Corruption, Injection, Omission, ...
    severity = Column(String)
    first_observed = Column(DateTime)
    rule_id = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

    # human-steward escalation tier (see docs/layer1_detection_research.md):
    # set True when rule-based and statistical evidence disagree, or volume/segment
    # is too ambiguous for the engine to classify confidently on its own.
    needs_review = Column(Boolean, default=False)
    review_reason = Column(Text, nullable=True)

    rca_run_id = Column(UUID(as_uuid=False), ForeignKey("rca_runs.id"))
    rca_run = relationship("RcaRun", back_populates="fingerprint")


class ChangeEvent(Base):
    """Synthetic operational metadata consumed by Layer 5."""
    __tablename__ = "change_events"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    event_type = Column(String, nullable=False)  # code_deploy, schema_change, config_change,
                                                   # source_change, infra_event, volume_anomaly, manual_edit
    occurred_at = Column(DateTime, nullable=False, index=True)
    scope_table = Column(String)
    scope_column = Column(String)
    scope_source_system = Column(String)
    description = Column(Text)
    actor = Column(String)
    raw_payload = Column(JSON)

    # ground truth linkage: which injected defect (if any) this event actually caused
    _gt_root_cause_id = Column(String, nullable=True)


class LineageEdge(Base):
    """Explicit lineage graph (Layer 6, 'with formal lineage' path)."""
    __tablename__ = "lineage_edges"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    upstream_table = Column(String, nullable=False)
    downstream_table = Column(String, nullable=False)
    transform_description = Column(Text)
    transform_code = Column(Text)  # SQL/pseudo-code, inspectable by the LLM in Layer 6


class QueryLogEntry(Base):
    """Synthetic query logs (Layer 6, 'without lineage' approximation path)."""
    __tablename__ = "query_log_entries"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    executed_at = Column(DateTime, nullable=False, index=True)
    query_text = Column(Text, nullable=False)
    target_table = Column(String, index=True)
    source_tables = Column(JSON)  # list[str], parsed from query_text
    job_name = Column(String)


class RcaRun(Base):
    """One end-to-end RCA investigation (a single triggering DQ rule failure)."""
    __tablename__ = "rca_runs"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    triggered_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="running")  # running, completed, failed
    trigger_rule_id = Column(String)

    fingerprint = relationship("DefectFingerprint", back_populates="rca_run", uselist=False)
    hypotheses = relationship("RootCauseHypothesis", back_populates="rca_run")


class RootCauseHypothesis(Base):
    """Output of the synthesis stage — one ranked candidate explanation."""
    __tablename__ = "root_cause_hypotheses"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    rca_run_id = Column(UUID(as_uuid=False), ForeignKey("rca_runs.id"))
    rca_run = relationship("RcaRun", back_populates="hypotheses")

    statement = Column(Text, nullable=False)
    confidence_score = Column(Float, nullable=False)
    supporting_layers = Column(JSON)   # {"L1": "...", "L3": "...", ...}
    contradicting_layers = Column(JSON)
    rank = Column(Integer)

    # ground-truth scoring (populated during evaluation, not by the engine)
    _gt_is_correct = Column(Boolean, nullable=True)


class KnowledgeBaseEntry(Base):
    """Phase 5: persisted, resolved RCA cases for pattern matching on future defects."""
    __tablename__ = "knowledge_base_entries"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    rca_run_id = Column(UUID(as_uuid=False), ForeignKey("rca_runs.id"))
    defect_fingerprint_summary = Column(JSON)
    root_cause_statement = Column(Text)
    remediation_applied = Column(Text)
    preventive_rule_spec = Column(JSON)
    time_to_resolution_minutes = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    embedding = Column(JSON, nullable=True)  # store vector for similarity search later
