import { useEffect, useState } from "react";
import { api } from "../api";
import EvidenceSection from "./EvidenceSection";
import VerdictStamp from "./VerdictStamp";
import SummaryCard from "./SummaryCard";
import { DEFECT_TYPE_GLOSS, SEVERITY_GLOSS, LAYER_QUESTIONS, buildPlainSummary } from "../glossary";

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
  const plainSummary = buildPlainSummary(report);
  const defectGloss = DEFECT_TYPE_GLOSS[fp.defect_type];
  const severityGloss = SEVERITY_GLOSS[fp.severity];

  return (
    <div className="detail scrollbox">
      <header className="detail__header">
        <div className="eyebrow">
          {fp.stage} · {fp.dq_dimension}
        </div>
        <h2 className="detail__title">
          {fp.defect_type} in <span className="mono">{fp.affected_fields.join(", ")}</span>
        </h2>
        {defectGloss && <p className="detail__gloss">In other words: {defectGloss}.</p>}
        <div className="detail__stats">
          <span>
            <strong>{fp.failure_volume}</strong> of {fp.failure_total} records
          </span>
          <span className={`severity-tag severity-tag--${fp.severity.toLowerCase()}`} title={severityGloss}>
            {fp.severity} — {severityGloss}
          </span>
        </div>
        {fp.needs_review && (
          <div className="detail__review-flag">
            ⚑ Flagged for human review — {fp.review_reason}
          </div>
        )}
      </header>

      <SummaryCard summary={plainSummary} verdict={validation.verdict} needsReview={fp.needs_review} />

      <div className="detail__evidence-label eyebrow">The full evidence trail, layer by layer</div>

      <EvidenceSection number={1} title="Defect Characterization" question={LAYER_QUESTIONS[1]} subtitle={fp.severity}>
        <p>
          Detected via <span className="mono">{(fp.matched_rule_ids || []).join(", ")}</span>. First observed{" "}
          <span className="mono">{fp.first_observed ? new Date(fp.first_observed).toLocaleString() : "—"}</span>.
        </p>
        <p className="evidence__pattern-text">{fp.failure_pattern}</p>
      </EvidenceSection>

      <EvidenceSection number={2} title="Temporal Analysis" question={LAYER_QUESTIONS[2]} subtitle={temporal.pattern}>
        <p>
          Onset: <span className="mono">{temporal.onset ? new Date(temporal.onset).toLocaleDateString() : "unknown"}</span>{" "}
          — detected using {temporal.technique}
        </p>
        <p className="evidence__detail-text">{temporal.evidence}</p>
      </EvidenceSection>

      <EvidenceSection number={3} title="Segmentation Analysis" question={LAYER_QUESTIONS[3]}>
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

      <EvidenceSection number={4} title="Statistical Profiling & Cross-Field Analysis" question={LAYER_QUESTIONS[4]}>
        <p className="evidence__detail-text">{statistical.summary}</p>
      </EvidenceSection>

      <EvidenceSection number={5} title="Change Event Correlation" question={LAYER_QUESTIONS[5]}>
        <p className="evidence__detail-text">{change_events.statement}</p>
        {change_events.top_matches.length > 0 && (
          <div className="pill-row">
            {change_events.top_matches.map((m, i) => (
              <ScorePill key={i} label={m.event_type} value={m.composite_score} />
            ))}
          </div>
        )}
      </EvidenceSection>

      <EvidenceSection number={6} title="Lineage Traversal" question={LAYER_QUESTIONS[6]}>
        <p>
          Introduced between <span className="mono">{lineage.injection_upstream_stage}</span> and{" "}
          <span className="mono">{lineage.injection_downstream_stage}</span> in the pipeline.
        </p>
        {lineage.transform_description && <p className="evidence__detail-text">{lineage.transform_description}</p>}
      </EvidenceSection>

      <section className="verdict-section">
        <div>
          <div className="eyebrow" style={{ marginBottom: 4 }}>
            Validation & Confirmation
          </div>
          <p className="verdict-section__lead">Each explanation above was tested against the real data, not just assumed to be true.</p>
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
        </div>
        <VerdictStamp verdict={validation.verdict} />
      </section>
    </div>
  );
}
