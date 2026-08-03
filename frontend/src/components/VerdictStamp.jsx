const STAMP_STYLE = {
  CONFIRMED: { color: "var(--confirmed)", label: "CONFIRMED" },
  "NO VALIDATION PERFORMED": { color: "var(--text-faint)", label: "UNVERIFIED" },
  INCONCLUSIVE: { color: "var(--text-dim)", label: "INCONCLUSIVE" },
};

export default function VerdictStamp({ verdict }) {
  const isRefuted = verdict.startsWith("REFUTED");
  const style = isRefuted ? { color: "var(--refuted)", label: "REFUTED" } : STAMP_STYLE[verdict] || STAMP_STYLE.INCONCLUSIVE;

  return (
    <div className="stamp-wrap">
      <div className="stamp" style={{ "--stamp-color": style.color }}>
        {style.label}
      </div>
    </div>
  );
}
