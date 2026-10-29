export default function SummaryCard({ summary, verdict, needsReview }) {
  const isRefuted = verdict.startsWith("REFUTED");
  const isConfirmed = verdict === "CONFIRMED" && !needsReview;
  const leadText = [summary.sentence1, summary.sentence2, summary.sentence3].filter(Boolean).join(" ");

  return (
    <div className="summary-card">
      <div className="summary-card__label">
        <span className="summary-card__dot" />
        In plain terms
      </div>
      <p className="summary-card__text">{leadText}</p>
      {summary.sentence4 && <p className="summary-card__text summary-card__text--secondary">{summary.sentence4}</p>}
      {summary.verdictSentence && (
        <p
          className={`summary-card__verdict ${
            needsReview
              ? "summary-card__verdict--caution"
              : isConfirmed
              ? "summary-card__verdict--confirmed"
              : isRefuted
              ? "summary-card__verdict--refuted"
              : ""
          }`}
        >
          {needsReview ? "⚑" : isConfirmed ? "✓" : isRefuted ? "✕" : "•"} {summary.verdictSentence}
        </p>
      )}
    </div>
  );
}
