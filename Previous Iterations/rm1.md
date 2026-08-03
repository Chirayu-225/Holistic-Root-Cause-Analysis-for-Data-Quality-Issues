# Identifying & Classifying Bad Data — Enterprise Methods

## Overview

This document covers **how large organizations actually identify and classify bad data** in production environments — as opposed to ad hoc exploratory data analysis (EDA) or manual outlier hunting. It serves as the foundational research for **Phase 1** of the Holistic RCA Framework for Data Quality project, answering: *what really identifies bad data, and do practitioners follow a shared set of guidelines?*

---

## 1. Who Identifies Bad Data: Humans or Machines?

Both — but they cover different ground, and neither works alone.

| | What it catches | Limitation |
|---|---|---|
| **Machines** | Hard constraints, statistical anomalies, cross-field logic violations, known patterns (duplicates, broken formats) | Blind to *plausible-looking* wrong data — a value that's structurally valid but factually wrong |
| **Humans** | Business-logic violations, ambiguous cases, novel failure modes nobody wrote a rule for yet | Doesn't scale — can't manually review billions of rows |

**Better framing:** machines *detect* at scale, humans *define and adjudicate*. Machines enforce rules; humans decide what the rules should be and resolve the cases rules can't settle.

---

## 2. Do They Follow Shared Guidelines? Yes.

Bad data isn't classified arbitrarily. Enterprises rely on standardized frameworks:

- **DAMA-DMBOK** (Data Management Body of Knowledge)
- **ISO 8000** (data quality standard)

These define a common set of **data quality dimensions** used across virtually all vendor platforms:

| Dimension | Question It Answers |
|---|---|
| Accuracy | Does the value reflect real-world truth? |
| Validity | Does it conform to defined format/domain/type? |
| Completeness | Is required data present? |
| Consistency | Does it agree across systems/fields? |
| Uniqueness | Is it duplicated when it shouldn't be? |
| Timeliness | Is it current enough to be useful? |

This is the **symptom-level classification**. A separate, **mechanism-level taxonomy** (Corruption, Injection, Omission, Duplication, Drift, Staleness, Contradiction) describes *how* the defect got there — useful for narrowing root cause hypotheses.

---

## 3. Known Corporate Methods for Identifying Bad Data

### 3.1 Rule-Based Validation (the foundation)
The oldest and most literal method — explicit, deterministic rules enforced at ingestion or within the pipeline.

- **Constraint rules**: not-null, type checks, range checks, regex/format checks, referential integrity, uniqueness constraints
- **Business rules**: e.g., "order total must equal sum of line items," "discount can't exceed 100%"
- Enforced as **quality gates** — failing data is quarantined or rejected before it moves downstream
- Common tools: Great Expectations, Soda, dbt tests, Informatica, Ataccama, Talend

### 3.2 Statistical / ML-Based Anomaly Detection (Data Observability)
Instead of hand-writing every rule, platforms **learn what "normal" looks like** for a dataset (volume, distribution, freshness, schema) and flag deviations automatically. Reduces manual rule maintenance and catches "unknown unknowns."

- Common tools: Monte Carlo, Metaplane, Acceldata, Bigeye, Anomalo

### 3.3 Data Profiling
Automated scanning of a dataset to report distinct value counts, null rates, min/max, pattern frequency, and inferred data types. Profiling is increasingly used to **auto-generate quality rules** rather than have a human hand-pick thresholds.

### 3.4 Dimension-Based Scoring Frameworks
Data is scored against the standardized dimensions (validity, completeness, uniqueness, accuracy, timeliness, consistency) from DAMA-DMBOK / ISO 8000 — the shared vocabulary referenced in Section 2.

### 3.5 Reconciliation / Cross-System Matching
Comparing the same fact across systems (e.g., revenue in the data warehouse vs. revenue in the finance system). Mismatches surface quality issues even when each source looks internally valid — heavily used in finance and banking.

### 3.6 Governance-as-Code + Human Stewardship
Quality and compliance rules are embedded directly into pipelines as executable code, enforced continuously rather than through static policy documents. Human **data stewards** retain ownership of exception review and sign-off on ambiguous, business-context-dependent cases.

### 3.7 AI/Agentic Quality Management (2026 shift)
The current frontier isn't "more rules" — it's **automating rule generation and prioritization** so coverage scales across the tens or hundreds of thousands of data assets modern enterprises manage, which hand-authored rule programs cannot cover.

---

## 4. Summary Takeaway

> Bad data is identified through a combination of
> **(a)** deterministic rule enforcement,
> **(b)** statistical/ML anomaly detection (observability), and
> **(c)** human stewardship for judgment calls — all organized around a shared taxonomy of quality dimensions (DAMA-DMBOK: accuracy, completeness, validity, consistency, uniqueness, timeliness).
> Enterprises layer all three approaches because rules catch **known** failure modes, observability catches **unknown** ones, and humans resolve **ambiguity** that neither can settle alone.

This output feeds directly into **Layer 1: Defect Characterization** of the RCA Framework — the classification produced here is the input the rest of the RCA pipeline consumes.

---

## References / Landscape Context

- DAMA-DMBOK — industry standard for data management practices
- ISO 8000 — international data quality standard
- Representative enterprise tooling: Informatica, Ataccama ONE, Collibra, Monte Carlo, Soda, Great Expectations, OvalEdge, DQLabs Prizm
- Gartner's 2026 Market Guide for Data Observability defines five core pillars for evaluating observability platforms: data content, pipelines, infrastructure/compute, usage, and cost allocation
