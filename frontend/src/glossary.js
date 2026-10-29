export const DEFECT_TYPE_GLOSS = {
  Corruption: "a value got changed to something wrong",
  Injection: "invalid data got mixed in",
  Omission: "data that should be there is missing",
  Duplication: "the same record shows up more than once",
  Drift: "values are gradually sliding away from normal",
  Staleness: "the data stopped updating",
  Contradiction: "two fields disagree with each other",
};

export const SEVERITY_GLOSS = {
  Critical: "needs attention right away",
  High: "should be looked at soon",
  Medium: "worth keeping an eye on",
  Low: "minor, low urgency",
};

export const LAYER_QUESTIONS = {
  1: "What exactly went wrong?",
  2: "When did it start, and how?",
  3: "Where is it happening?",
  4: "What else looks unusual nearby?",
  5: "What changed around that time?",
  6: "Which step introduced it?",
};

const PATTERN_PLAIN = {
  step: "started suddenly and has stayed that way since",
  spike: "happened in a short burst, then went back to normal",
  drift: "has been slowly getting worse over time",
  periodic: "keeps happening on a regular schedule",
  scatter: "shows up unpredictably, with no clear pattern",
};

function fmtDate(iso) {
  if (!iso) return null;
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function buildPlainSummary(report) {
  const { fingerprint: fp, temporal, segmentation, change_events, validation } = report;

  const what = DEFECT_TYPE_GLOSS[fp.defect_type] || fp.defect_type.toLowerCase();
  const fields = fp.affected_fields.join(" and ");
  const count = fp.failure_volume;

  let sentence1 = `In the "${fields}" field${fp.affected_fields.length > 1 ? "s" : ""}, ${what} — affecting ${count.toLocaleString()} record${count === 1 ? "" : "s"}.`;

  let sentence2 = "";
  const onsetDate = fmtDate(temporal.onset);
  if (onsetDate) {
    const patternPlain = PATTERN_PLAIN[temporal.pattern] || "";
    sentence2 = `It started around ${onsetDate}${patternPlain ? " — it " + patternPlain : ""}.`;
  }

  let sentence3 = "";
  if (segmentation.path && segmentation.path.length > 0) {
    const top = segmentation.path[0];
    sentence3 = `It's isolated to ${top.dimension.replace(/_/g, " ")} = ${top.value} — it doesn't show up anywhere else.`;
  }

  let sentence4 = "";
  if (change_events.top_matches && change_events.top_matches.length > 0) {
    sentence4 = `The most likely trigger: ${change_events.top_matches[0].description}`;
  }

  // A fingerprint flagged needs_review means Layer 1 itself wasn't confident this
  // is even a real defect (e.g. thin evidence, or the detection tiers disagreeing).
  // The validation verdict only reflects whatever checks actually applied to it --
  // for a low-volume statistical fluke, that's often just the generic segment
  // check, which can trivially "pass" without meaning much. Overstating
  // confidence here would be worse than saying nothing, so this overrides the
  // normal verdict framing rather than letting a technically-true-but-shallow
  // CONFIRMED read as a real conclusion.
  let verdictSentence = "";
  if (fp.needs_review) {
    const shortReason = (fp.review_reason || "").split(" -- ")[0].toLowerCase();
    verdictSentence = `This case is flagged as low-confidence${shortReason ? " (" + shortReason + ")" : ""} — treat it as a possible false alarm, not a confirmed problem.`;
  } else if (validation.verdict === "CONFIRMED") {
    verdictSentence = "This explanation has been checked against the data and holds up.";
  } else if (validation.verdict.startsWith("REFUTED")) {
    verdictSentence = "One part of this explanation was checked against the data and did NOT hold up — treat it as unconfirmed.";
  }

  return { sentence1, sentence2, sentence3, sentence4, verdictSentence };
}
