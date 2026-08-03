import { useEffect, useState } from "react";
import { api } from "../api";
import EvidenceSection from "./EvidenceSection";
import VerdictStamp from "./VerdictStamp";

function ScorePill({ label, value }) {
  return (
    <span className="score-pill">
      {label} <strong className="mono">{typeof value === "number" ? value.toFixed(2) : value}</strong>
    </span>
  );
}

export default function CaseDetail({ fingerprintId, stage }) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!fingerprintId) return;
    setReport(null);
    setError(null);
    api
      .report(fingerprintId, stage)
      .then(setReport)
      .catch((e) => setError(e.message));
  }, [fingerprintId, stage]);

  if (!fingerprintId) {
    return (
      <div className="detail detail--empty">
        <div className="eyebrow">No case selected</div>
        <p>Choose a case from the file on the left to open its evidence trail.</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="detail detail--empty">
        <div className="eyebrow" style={{ color: "var(--refuted)" }}>
          Could not load case
        </div>
        <p>{error}</p>
      </div>
    );
  }

  if (!report) {
    return (
      <div className="detail detail--empty">
        <div className="eyebrow">Opening case…</div>
      </div>
    );
  }

  const { fingerprint: fp, temporal, segmentation, statistical, change_events, lineage, validation } = report;

  return (
    <div className="detail scrollbox">
      <header className="detail__header">
        <div className="eyebrow">
          {fp.stage} · {fp.dq_dimension}
        </div>
        <h2 className="detail__title">
          {fp.defect_type} in <span className="mono">{fp.affected_fields.join(", ")}</span>
        </h2>
        <p className="detail__pattern">{fp.failure_pattern}</p>
        <div className="detail__stats">
          <span>
            <strong>{fp.failure_volume}</strong> of {fp.failure_total} records
          </span>
          <span>{fp.failure_distribution}</span>
        </div>
        {fp.needs_review && <div className="detail__review-flag">⚑ Flagged for human review — {fp.review_reason}</div>}
      </header>

      <EvidenceSection number={1} title="Defect Characterization" subtitle={fp.severity}>
        <p>
          Detected via <span className="mono">{(fp.matched_rule_ids || []).join(", ")}</span>. First observed{" "}
          <span className="mono">{fp.first_observed ? new Date(fp.first_observed).toLocaleString() : "—"}</span>.
        </p>
      </EvidenceSection>

      <EvidenceSection number={2} title="Temporal Analysis" subtitle={temporal.pattern}>
        <p>
          Onset: <span className="mono">{temporal.onset ? new Date(temporal.onset).toLocaleDateString() : "unknown"}</span>{" "}
          via {temporal.technique}
        </p>
        <p className="evidence__detail-text">{temporal.evidence}</p>
      </EvidenceSection>

      <EvidenceSection number={3} title="Segmentation Analysis">
        <p className="evidence__detail-text">{segmentation.statement}</p>
        {segmentation.path.length > 0 && (
          <div className="pill-row">
            {segmentation.path.map((lvl, i) => (
              <span key={i} className="segment-pill mono">
                {lvl.dimension} = {String(lvl.value)}
              </span>
            ))}
          </div>
        )}
      </EvidenceSection>

      <EvidenceSection number={4} title="Statistical Profiling & Cross-Field Analysis">
        <p className="evidence__detail-text">{statistical.summary}</p>
      </EvidenceSection>

      <EvidenceSection number={5} title="Change Event Correlation">
        <p className="evidence__detail-text">{change_events.statement}</p>
        {change_events.top_matches.length > 0 && (
          <div className="pill-row">
            {change_events.top_matches.map((m, i) => (
              <ScorePill key={i} label={m.event_type} value={m.composite_score} />
            ))}
          </div>
        )}
      </EvidenceSection>

      <EvidenceSection number={6} title="Lineage Traversal">
        <p>
          Injection point: <span className="mono">{lineage.injection_upstream_stage}</span> →{" "}
          <span className="mono">{lineage.injection_downstream_stage}</span>
        </p>
        {lineage.transform_description && <p className="evidence__detail-text">{lineage.transform_description}</p>}
      </EvidenceSection>

      <section className="verdict-section">
        <div className="eyebrow" style={{ marginBottom: 10 }}>
          Validation & Confirmation
        </div>
        <div className="verdict-checks">
          {validation.checks.map((c, i) => (
            <div key={i} className={`check-row check-row--${c.passed ? "pass" : "fail"}`}>
              <span className="check-row__mark">{c.passed ? "✓" : "✕"}</span>
              <div>
                <div className="check-row__technique">{c.technique}</div>
                <div className="check-row__detail">{c.detail}</div>
              </div>
            </div>
          ))}
        </div>
        <VerdictStamp verdict={validation.verdict} />
      </section>
    </div>
  );
}
