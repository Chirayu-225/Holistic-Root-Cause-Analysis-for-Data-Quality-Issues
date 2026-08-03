const SEVERITY_COLOR = {
  Critical: "var(--refuted)",
  High: "var(--amber)",
  Medium: "var(--text-dim)",
  Low: "var(--text-faint)",
};

export default function CaseList({ stage, stages, onStageChange, fingerprints, loading, selectedId, onSelect }) {
  return (
    <aside className="case-list">
      <div className="case-list__header">
        <div className="eyebrow">Holistic RCA Framework</div>
        <h1 className="case-list__title">Case File</h1>
      </div>

      <div className="case-list__stage-picker">
        <div className="eyebrow" style={{ marginBottom: 6 }}>
          Pipeline stage
        </div>
        <div className="stage-tabs">
          {Object.keys(stages).map((s) => (
            <button
              key={s}
              className={`stage-tab ${s === stage ? "stage-tab--active" : ""}`}
              onClick={() => onStageChange(s)}
            >
              {s}
              <span className="stage-tab__count">{stages[s]}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="eyebrow case-list__section-label">
        {loading ? "Loading cases…" : `${fingerprints.length} open case${fingerprints.length === 1 ? "" : "s"}`}
      </div>

      <div className="case-list__items scrollbox">
        {fingerprints.map((fp) => {
          const id = fp.fingerprint_id;
          const isReview = fp.needs_review;
          return (
            <button
              key={id}
              className={`case-card ${id === selectedId ? "case-card--active" : ""}`}
              onClick={() => onSelect(id)}
            >
              <div className="case-card__top">
                <span className="case-card__defect-type">{fp.defect_type}</span>
                <span className="case-card__severity" style={{ color: SEVERITY_COLOR[fp.severity] }}>
                  {fp.severity}
                </span>
              </div>
              <div className="case-card__fields mono">{fp.affected_fields.join(", ")}</div>
              <div className="case-card__meta">
                <span>{fp.failure_volume} records</span>
                {isReview && <span className="case-card__flag">needs review</span>}
              </div>
            </button>
          );
        })}
        {!loading && fingerprints.length === 0 && (
          <div className="case-list__empty">No defects detected at this stage.</div>
        )}
      </div>
    </aside>
  );
}
