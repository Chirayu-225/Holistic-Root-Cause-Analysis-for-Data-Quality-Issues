# Holistic RCA Framework for Data Quality

A lineage-adaptive, six-layer root cause analysis system for data quality
defects, built and validated against a synthetic-but-realistic enterprise
data pipeline with ground-truth-labeled defects.

This README documents everything built **so far**: the test bed (synthetic
data generator) and **Layer 1 (Defect Characterization)**. It will be
updated as each subsequent layer is added.

---

## 1. What this project actually does

Real RCA frameworks are hard to demo convincingly because you rarely have
access to a real company's messy pipeline *and* the true answer for what
went wrong in it. This project solves that by building both halves:

1. **A synthetic data pipeline** that mimics a real multi-stage enterprise
   warehouse (raw → staging → warehouse → mart), with realistic defects
   deliberately injected into it — and the true root cause for each defect
   recorded separately as a hidden "answer key."
2. **The RCA framework itself**, built one layer at a time, which must
   detect and diagnose those defects *without* being given the answer key.
   Every layer's output can be scored against ground truth, which is what
   makes this a genuine engineering demonstration rather than a "looks
   plausible" toy.

---

## 2. Tech stack

| Concern | Choice | Why |
|---|---|---|
| Backend language | Python 3.12/3.13 | Data-science-friendly, matches pandas/numpy/scipy ecosystem needed for the statistical layers |
| Data manipulation | pandas, numpy, scipy | Core to Layers 1–4 (profiling, z-scores, distribution comparisons) |
| Database | PostgreSQL (via SQLAlchemy 2.0 ORM) | Realistic "warehouse" simulation; also stores all RCA metadata (fingerprints, hypotheses, knowledge base) |
| API layer | FastAPI | Will expose the RCA engine as a service (not yet built) |
| LLM reasoning | Gemini 2.5 Flash (`google-genai`) | Free-tier, used later for Layer 6 transformation-code inspection and hypothesis narrative synthesis — no local GPU needed anywhere in this stack |
| Frontend | React + Vite (planned, not yet built) | Dashboard for exploring fingerprints, timelines, segments, hypotheses |
| Deployment | Docker Compose (postgres + backend + frontend) | One-command local spin-up; portable to Railway/Render for a public demo |
| Graph/lineage utilities | networkx | For Layer 6's lineage graph traversal (not yet built) |

Nothing in this stack requires GPU/local model inference — deliberate,
given the target hardware (RTX 3050 Ti, 4GB VRAM). Every statistic and rule
runs on CPU; the one LLM dependency (Layer 6) is a cloud API call.

---

## 3. Project architecture

```
rca-framework/
├── docker-compose.yml          # postgres + backend + frontend, one command
├── .gitignore
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── core/
│   │   │   └── config.py       # pydantic-settings: DATABASE_URL, GEMINI_API_KEY, etc.
│   │   ├── db/
│   │   │   ├── models.py       # SQLAlchemy schema (pipeline tables + RCA metadata tables)
│   │   │   └── session.py      # engine/session factory, init_db()
│   │   ├── layers/             # one file per RCA layer, all flat in this folder
│   │   │   ├── rules.py                     # Layer 1, Tier 1: hand-written constraint rules
│   │   │   ├── statistical_baseline.py      # Layer 1, Tier 2: auto-derived anomaly detection
│   │   │   ├── layer1_defect_characterization.py   # Layer 1 orchestrator
│   │   │   ├── layer2_temporal.py           # NOT YET BUILT
│   │   │   ├── layer3_segmentation.py       # NOT YET BUILT
│   │   │   ├── layer4_statistical.py        # NOT YET BUILT
│   │   │   ├── layer5_change_events.py      # NOT YET BUILT
│   │   │   └── layer6_lineage.py            # NOT YET BUILT
│   │   ├── synthesis/          # hypothesis ranking (NOT YET BUILT)
│   │   ├── knowledge_base/     # persisted resolved RCA cases (NOT YET BUILT)
│   │   ├── api/routes/         # FastAPI endpoints (NOT YET BUILT)
│   │   └── llm/                # Gemini client (NOT YET BUILT)
│   └── data_gen/               # the synthetic test bed generator
│       ├── synthetic_source.py     # generates clean base weather-station data
│       ├── pipeline_simulator.py   # "correct" raw→staging→warehouse→mart transforms
│       ├── defect_injector.py      # injects 4 known defects + logs ground truth
│       ├── synthetic_metadata.py   # fake lineage graph + query logs (for Layer 6, later)
│       └── run_generate.py         # orchestrates all of the above, writes CSV or DB
└── frontend/                    # NOT YET BUILT
```

---

## 4. The test bed (`backend/data_gen/`)

Before any RCA logic could be built, we needed data to run it against —
with a known, hidden answer.

### 4.1 `synthetic_source.py`
Generates realistic weather-station observations: 9 stations across two
source systems (`NOAA_ISD`, a legacy feed present from day 1, and
`API_v3`, a newer vendor onboarded partway through the timeline). This
mirrors the exact domain and field names used in the original framework
specification (`CelsiusTemperatureQuantity`, `RelativeHumidityNumber`,
`station_id`, etc.), just with clean, generated values. One deliberate
realistic quirk is built in: `API_v3` reports humidity as a 0–1 fraction
while `NOAA_ISD` reports 0–100 — a legitimate format difference the
pipeline must normalize correctly (this becomes the setup for defect #1
below).

### 4.2 `pipeline_simulator.py`
Defines the **correct** transformation logic for each pipeline stage:
- `raw → staging`: type casting only, no business logic
- `staging → warehouse`: normalizes humidity to a consistent 0–100 scale
- `warehouse → mart`: derives `sunrise`/`sunset` fields

This also doubles as the formal lineage graph that Layer 6 will eventually
need to traverse.

### 4.3 `defect_injector.py`
Injects four realistic, independent defects into the clean pipeline output,
each with a **ground truth record** (the true root cause, in the exact
schema the original framework spec calls for in its Knowledge Base
section) and a matching **synthetic change event** (a fake deploy/
migration/incident log entry — exactly the kind of evidence Layer 5 will
later have to discover on its own):

| ID | Defect type | Segment | Description |
|---|---|---|---|
| RC-001 | Corruption | `source_system=API_v3` only, from day 40 onward | A simulated ETL deploy double-applies the `*100` humidity normalization, producing physically impossible values (e.g. 4182%) |
| RC-002 | Omission | `source_system=NOAA_ISD` only, 3-day window | A simulated schema migration drops `sunrise`/`sunset` temporarily |
| RC-003 | Duplication | One batch only | A simulated network retry re-inserts an entire batch |
| RC-004 | Staleness | One station only | A simulated vendor outage — the station just stops reporting |

### 4.4 `synthetic_metadata.py`
Generates the two independent Layer 6 evidence sources: an explicit
lineage graph (the "formal lineage available" path) and daily
`INSERT...SELECT` query log entries (the "no lineage, reconstruct it from
query logs" path) — so the eventual Layer 6 implementation can demonstrate
both techniques converging on the same answer.

### 4.5 `run_generate.py`
Orchestrates all of the above: generates clean data → runs it through the
pipeline → injects all 4 defects → **rebuilds `mart` from the now-defective
`warehouse`** so defects propagate downstream exactly like they would in a
real system → writes everything to CSV (for quick local iteration) or
directly into Postgres.

Running it produces, among other things, `ground_truth.csv` — the hidden
answer key every later layer's output will be checked against.

---

## 5. Layer 1: Defect Characterization (`backend/app/layers/`)

This is the first layer of the actual RCA framework — it answers the
question *"what exactly is wrong?"* before any cause is investigated.

### Design: two detection tiers + a human-review escalation path

This design is grounded in outside research (not just the framework spec)
into how real enterprises actually detect bad data: a combination of
deterministic rules, statistical/ML-based anomaly detection ("data
observability"), and human judgment for ambiguous cases — because rules
alone only catch *known* failure modes, and stats alone can't resolve
business-context judgment calls.

**`rules.py` — Tier 1, deterministic constraint rules**
Seven hand-written rules covering known, expected failure modes: valid
humidity/temperature ranges, required-field nullness, uniqueness of
record IDs, sunrise-before-sunset consistency, and station-level
freshness. Each rule is a pure `DataFrame -> boolean Series` function,
tagged with its DQ dimension (Accuracy/Validity/Completeness/etc.) and a
defect-type hint (Corruption/Omission/Duplication/etc.), so a hit
immediately narrows the hypothesis space.

**`statistical_baseline.py` — Tier 2, auto-derived anomaly detection**
Two techniques that learn "normal" directly from the data instead of
relying on hand-written thresholds, so they catch failure modes no rule
was written for:
- **Robust numeric outlier detection** via modified z-score (median + MAD,
  not mean + stddev — MAD is far less sensitive to the very outliers being
  searched for, which a naive z-score would let skew the baseline).
- **Null-rate spike detection** — flags fields whose per-day null rate
  jumps well above their own historical baseline, per segment.

Crucially, both tiers only compute their "healthy" baseline from records
the *other* tier hasn't already flagged, so a defect can't quietly bias
its own detection threshold.

**`layer1_defect_characterization.py` — orchestrator**
Runs both tiers, merges their violations, groups them into fingerprints by
(affected fields, defect type), and computes the full fingerprint schema
from the framework spec: DQ dimension, affected fields, failure pattern,
volume, distribution (concentrated in one segment vs. scattered),
severity, and first-observed timestamp. It also sets a `needs_review` flag
— the human-steward escalation path — when evidence is thin (fewer than 5
records), when both tiers disagree, or when no single segment explains a
majority of the failures.

### Validated result

Running Layer 1 against the test bed correctly finds **all 4 injected
defects**, including the RC-002 omission — caught *only* by the
auto-derived statistical tier, since no hand-written rule targets
`sunrise`/`sunset` nullness. That's the clearest evidence the two-tier
design is pulling real weight, not just duplicating effort.

One known, intentional limitation: the Staleness fingerprint's
`first_observed` shows the station's very first record, not the actual
outage onset date. Layer 1 flags every record belonging to a currently-
stale station, not just the point it went quiet — pinpointing the precise
onset is explicitly Layer 2's job (historical rule replay), so this is a
deliberate handoff, not a bug.

---

## 6. Layer 2: Temporal Analysis (`backend/app/layers/layer2_temporal.py`)

The second layer of the RCA framework. It answers a different question from
Layer 1: not "what is wrong," but **"when did it start, and what shape does
it have over time?"** Time is the single most powerful RCA signal that
requires zero lineage — knowing exactly when something broke lets you go
looking for what else happened at that moment (this sets up Layer 5 later).

### Why Layer 2 couldn't just reuse Layer 1's output

The naive approach: take the exact records Layer 1 flagged, find the
earliest timestamp among them, call that the onset. This works for most
defect types but breaks for **Staleness** — Layer 1's rule flags *every*
record belonging to a station that's currently stale, including records
from weeks before it actually went quiet, since "this station's most recent
record is too old" is true for its whole history once triggered. So the
earliest flagged timestamp was just the station's first-ever record, not
the outage date — a known limitation flagged back in the Layer 1 write-up.

Layer 2 fixes this by **never reusing Layer 1's flagged row set directly**.
For each defect type, it independently re-derives a day-by-day signal using
the technique actually appropriate to that defect — which is also more
faithful to the framework spec's description of Layer 2 as an independent
evidence layer, not a restatement of Layer 1.

### The three onset-detection techniques implemented

- **Historical Rule Replay** — for defects with a known deterministic rule
  (e.g. the humidity corruption), the same rule from `rules.py` is
  re-executed against each day's data independently. The first day it
  starts failing is the onset — a literal implementation of the technique
  named in the framework spec.
- **Volume Anomaly** — used two ways: (a) Staleness, tracking each
  station's daily record count to identify which station "went quiet" and
  stayed quiet, using its last-seen date + 1 as onset; and (b) Duplication,
  tracking the daily duplicate-record rate.
- **Distribution Shift Detection** (null-rate variant) — for Omission,
  tracks each field's daily null rate against its own baseline and finds
  the first day it spikes.

A fourth technique from the spec, **Schema Archaeology** (correlating onset
with DDL/schema-change history), isn't implemented yet — it depends on
schema-change metadata, which is exactly what Layer 5's change-event feed
will provide. Deliberate sequencing, not an oversight.

### Temporal pattern classification

Once a day-by-day signal exists (whichever technique produced it), a
shared classifier determines its shape, matching the five patterns from the
framework spec:
- **Step** — elevated and stays elevated through the end of the observed
  range → one-time event (deploy, migration, config change)
- **Spike** — a short elevated burst that returns to baseline → transient
  event (retry storm, one-off manual fix)
- **Drift** — statistically significant gradual upward trend (linear
  regression, R² and p-value thresholds) → continuous process (sensor
  degradation, slowly changing source)
- **Periodic** — repeating pattern via autocorrelation at short lags →
  scheduled process (batch job, timezone-sensitive logic)
- **Scatter** — none of the above fit → stochastic cause (race condition,
  intermittent failure)

### Validated result

Running Layer 2 against all 4 injected defects, both the onset date **and**
the temporal pattern matched the hidden ground truth exactly:

| Defect | True onset | Detected onset | True pattern | Detected pattern |
|---|---|---|---|---|
| Corruption (RC-001) | 2026-02-10 | 2026-02-10 | step | step |
| Duplication (RC-003) | 2026-02-03 | 2026-02-03 | spike | spike |
| Staleness (RC-004) | 2026-02-20 | 2026-02-20 | step | step |
| Omission (RC-002) | 2026-01-26 | 2026-01-26 | spike | spike |

This directly confirms the Layer 1 handoff worked as designed — the
staleness onset is now precise, resolved independently by Layer 2 exactly
as intended.

### A small Layer 1 change that enabled this

`layer1_defect_characterization.py` was extended (not restructured) to emit
two additional fields on each fingerprint dict: `dominant_segment_column` /
`dominant_segment_value` (structured segment info, not just the
human-readable `failure_distribution` string) and `matched_rule_ids`
(structured list, not just embedded in the `failure_pattern` string). Both
additions are additive — nothing existing was removed or changed shape.

---

## 7. How to run everything built so far

### 7.1 Setup (one-time)

```powershell
cd rca-framework\backend
py -3.13 -m venv rca
rca\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Use Python 3.12 or 3.13. Avoid 3.14 for now — several pinned dependencies
don't yet ship prebuilt wheels for it.

### 7.2 Generate the test bed

```powershell
python -m data_gen.run_generate --out csv
```

Writes `backend/generated/*.csv`: the 4 pipeline stages (`raw`, `staging`,
`warehouse`, `mart`), `ground_truth.csv`, `change_events.csv`,
`lineage_edges.csv`, and `query_log.csv`.

Expected output:
```
Wrote CSVs to ./generated/
  raw       :  1920 rows  (0 ground-truth defective)
  staging   :  1920 rows  (0 ground-truth defective)
  warehouse :  1916 rows  (312 ground-truth defective)
  mart      :  1916 rows  (384 ground-truth defective)
  ground_truth entries: 4
  change_events       : 4
```

### 7.3 Run Layer 1

```powershell
set PYTHONPATH=.
python app\layers\layer1_defect_characterization.py
```

Expected output: a list of 7 fingerprint dicts across the `warehouse` and
`mart` stages, covering all 4 injected defects (Staleness, Duplication,
Corruption, and two Omission entries for `sunrise`/`sunset`).

### 7.4 Run Layer 2

```powershell
python app\layers\layer2_temporal.py
```

(Assumes `PYTHONPATH` is still set from the previous step, in the same
shell session.)

Expected output: onset date, detection technique, and temporal pattern for
each of the 4 defects — see the validated-result table in Section 6 above
for the exact values to check against.

### 7.5 (Not yet needed) Full Docker stack

`docker-compose.yml` is in place for when the FastAPI routes and frontend
exist:
```powershell
docker-compose up --build
```
This isn't usable yet — there's no API or frontend to serve — but the
Postgres service and backend container build will work today if you want
to test `run_generate.py --out db` against a real database:
```powershell
docker-compose up postgres -d
docker-compose exec backend python -m data_gen.run_generate --out db
```

---

## 8. Status

- [x] Project scaffold + Docker Compose setup
- [x] DB schema (pipeline tables + RCA metadata tables)
- [x] Synthetic test bed generator with 4 ground-truth-labeled defects
- [x] Layer 1: Defect Characterization (rule-based + auto-derived statistical tiers), validated against ground truth
- [x] Layer 2: Temporal Analysis (onset detection + pattern classification), validated against ground truth
- [ ] Layer 3: Segmentation Analysis
- [ ] Layer 4: Statistical Profiling & Cross-Field Analysis
- [ ] Layer 5: Change Event Correlation
- [ ] Layer 6: Lineage Traversal
- [ ] Synthesis / hypothesis ranking engine
- [ ] Knowledge base
- [ ] FastAPI routes
- [ ] React frontend
- [ ] Deployment (Railway/Render)
