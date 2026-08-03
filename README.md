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

## 7. Layer 3: Segmentation Analysis (`backend/app/layers/layer3_segmentation.py`)

The third layer of the RCA framework. It answers: **is the defect universal,
or concentrated in a specific slice of the data?** Per the framework spec,
this is often the fastest way to narrow root cause hypotheses without
touching lineage — if 100% of failures trace to one source, one station, or
one batch, that's frequently enough to solve the case outright.

### Design: multi-dimensional drill-down, not a single sweep

A naive version of this layer would check one dimension (e.g.
`source_system`) and stop. This implementation instead **drills down**:
after finding the best single dimension, it re-runs the same sweep *within*
that segment looking for further refinement — e.g. "100% of defects are in
`source_system=API_v3`" → "and within that, does one station or batch
narrow it further?" — stopping only when no further dimension adds real
specificity. This mirrors the framework spec's own worked example, which
isolates a defect down to a joint source + batch + station combination, not
just one dimension in isolation.

Candidate dimensions swept: `source_system`, `country`, `region`,
`station_id`, `batch_id`, `load_type`, plus a derived `_day` dimension
(date-only, coarser than the hourly `batch_id` already in the data) — added
specifically because the framework spec lists "Time Window" as its own
segmentation dimension, and one of the injected defects (duplication) spans
a whole calendar day across several hourly batches, which no single
`batch_id` value could isolate on its own.

For each dimension, every value's **coverage** (what fraction of the
fingerprint's defects fall in that value) and **lift** (how many times more
likely a record in that value is to be defective, versus the current
scope's baseline rate) are computed. The best candidate at each drill level
is chosen by coverage first, lift only as a tiebreaker — a segment that
fully explains the defects should always beat one that's merely more
statistically "enriched" but incomplete. Drilling stops once a segment
reaches ~100% coverage, since any further dimension at that point can only
add redundant clauses, not genuine specificity.

### Two real bugs this layer's construction surfaced (and fixed)

Building this against the test bed surfaced two issues worth documenting,
since both reflect real engineering lessons rather than one-off mistakes:

1. **Physical duplication can quietly break math done in row-space.**
   Coverage was initially computing >100% for some fingerprints. The cause:
   the injected duplication defect and the injected staleness defect
   happen to overlap on one calendar day for one station, so a handful of
   "unrelated" defective records physically appear twice in the table.
   Counting rows instead of unique `record_uid`s inflated the numerator
   past the denominator. Fixed by deduplicating on `record_uid` before any
   coverage/lift computation — segmentation now reasons in unique-record
   space throughout, which is the conceptually correct approach regardless
   of the incidental data overlap that exposed it.
2. **Optimizing for lift alone can pick a worse answer.** The first ranking
   heuristic chose the highest-lift dimension at each level. For the
   Omission defect, this picked `country=US` (80% coverage, higher lift)
   over the actually-correct `source_system=NOAA_ISD` (100% coverage,
   slightly lower lift) — a more "enriched" but incomplete segment beat the
   one that fully explained the defect. Fixed by ranking on coverage first
   and using lift only to break ties among comparably complete candidates.

### Validated result

All 4 injected defects now resolve to their true segment at 100% coverage:

| Defect | True segment | Layer 3 result |
|---|---|---|
| Corruption (RC-001) | `source_system=API_v3` | `source_system=API_v3` ✅ |
| Duplication (RC-003) | `batch_id startswith batch_033_` | `_day=2026-02-03` ✅ (correctly resolved via the derived day dimension, since no single hourly `batch_id` covers the whole injected event) |
| Staleness (RC-004) | `station_id=037720-99999` | `country=GB` — mathematically equivalent in this dataset, since GB has exactly one station; not wrong, just a tie broken by dimension order rather than "true" specificity. A dataset with multiple stations per country wouldn't have this ambiguity. |
| Omission (RC-002) | `source_system=NOAA_ISD` | `source_system=NOAA_ISD` ✅ |

### A small Layer 1 change that enabled this

`layer1_defect_characterization.py` was extended again, additively: each
fingerprint dict now also carries `_record_uids` — the exact set of
violating record IDs. Layer 2 didn't need this (it re-derives its own
day-by-day signal independently), but Layer 3 genuinely does, since
segmentation is inherently about slicing those specific rows. This field is
explicitly marked as in-process only (not part of the `DefectFingerprint`
DB schema) and should be stripped before persisting a fingerprint.

---

## 8. Layer 4: Statistical Profiling & Cross-Field Analysis (`backend/app/layers/layer4_statistical.py`)

The fourth layer. It moves beyond the failing field itself to ask: **what
other anomalies co-occur with the defect, and what correlations exist
between fields?** The framework spec calls the core technique here — case
(failing rows) vs. control (healthy rows) differential profiling — "the
most powerful lineage-free RCA technique," since it needs nothing but the
two groups of rows to compare.

### Four techniques implemented

1. **Case-vs-control column profiling** — every column's distribution is
   compared between failing and healthy rows (numeric via the
   Kolmogorov-Smirnov statistic, categorical via total variation distance,
   both alongside a null-rate delta) and ranked by how different it is.
2. **Null co-occurrence** — among columns *other than* the fingerprint's
   own affected fields, which are also disproportionately null in the
   failing rows? Fields that go null together often share a root cause.
3. **Cross-field correlation shift** — do any two numeric fields that are
   roughly independent in healthy data become strongly correlated in
   failing data, or vice versa? Gated by a proper Fisher r-to-z
   significance test (see the reliability note below), not just a raw
   threshold on the correlation delta.
4. **Value domain novelty** — categorical values present in failing rows
   that never appear at all in healthy rows (injection candidates).

### A reproducibility bug this layer's construction surfaced (and fixed)

Running this layer exposed a real bug in the test bed generator, unrelated
to Layer 4's own logic: `synthetic_source.py`'s `weather_description` field
was assigned using Python's unseeded `random.choice()`, while every other
field used a seeded NumPy generator. This meant the "reproducible" test bed
wasn't actually fully reproducible — rerunning the generator on a different
machine (or even the same machine at a different time) could silently
produce slightly different data, which is exactly the kind of thing that
erodes trust in a project built around a fixed ground-truth answer key.
Fixed by switching `weather_description` to draw from the same seeded RNG
as everything else. Verified by generating the test bed twice in a row and
diffing the output files byte-for-byte — now identical every time.

One side effect worth noting honestly: because all the random draws share
a single RNG stream, fixing this shifted which exact pseudo-random numbers
land where (weather_description now consumes draws that used to go to
other fields later in the sequence). The specific defect scenarios are
unaffected — they're driven by fixed onset days and station/segment
assignments, not by the numeric noise — but the exact statistical delta
scores below reflect the *post-fix*, now-stable values.

### An honest reliability note on the correlation-shift technique

Two of the four fingerprints (Duplication, Omission-before-the-fix)
initially showed a cross-field correlation shift that looked real but
wasn't causally explained by anything actually injected — a case of
small-sample noise (36-60 failing rows) producing a large-looking
correlation delta by chance. Rather than just raising an arbitrary
sample-size cutoff, this was fixed properly with a Fisher r-to-z
significance test, which accounts for both groups' sample sizes directly.
After the reproducibility fix above, neither fingerprint's correlation
shift survives that significance test any more — both now correctly report
"no cross-field correlation shift detected." This is a good demonstration
of the layer behaving honestly: it doesn't manufacture findings just
because a threshold was crossed.

### Validated result (post-fix, fully reproducible)

| Defect | Top corroborating signal | Genuinely new signal (not visible to Layers 1-3) |
|---|---|---|
| Corruption (`relative_humidity`) | region/station_id/source_system shift confirms the API_v3 concentration Layer 3 already found | — |
| Duplication | observed_at/batch_id shift confirms the single-day concentration Layer 3 already found | — |
| Staleness | station_id/country/region shift confirms the GB concentration Layer 3 already found | — |
| Omission (`sunrise`/`sunset`) | batch_id/observed_at shift, consistent with Layer 2/3 findings | **sunrise↔sunset co-null relationship** — an exact match to the framework spec's own worked example (Section 6.1: "if sunrise, sunset, and UV index are always null together, they likely share a common source or extraction path") |

For the single-cause defects in this test bed, Layer 4 mostly serves as
**corroboration** — confirming what earlier layers found via an
independent statistical lens, which is itself valuable (the framework spec
explicitly treats more independent agreeing signal as higher confidence,
even when every layer converges on the same answer). The Omission case is
the one genuine exception: the co-null finding wasn't visible to any prior
layer, which is exactly the kind of result this layer exists to surface.

### An unplanned but welcome validation: the escalation path actually fired

While verifying the reproducibility fix, the statistical tier flagged one
single record's `sea_level_pressure` as an outlier — a natural extreme
value crossing the z-score threshold by chance, not a real defect. Layer
1's `needs_review` flag correctly caught it: `"Low volume (1 records) --
confirm this isn't noise before investigating."` This is the first time in
this project the human-steward escalation path has actually triggered on
real data (every defect in the test bed so far has been a "clean," well-isolated case) — a small but genuine confirmation that the escalation logic
works as designed, not just in theory.

### A small Layer 1 change that enabled this

No changes to Layer 1 were needed for Layer 4 — it reuses the same
`_record_uids` field Layer 3 already relies on.

---

## 9. Layer 5: Change Event Correlation (`backend/app/layers/layer5_change_events.py`)

The fifth layer, and the first **conditional** one in the framework — unlike
Layers 1-4 (always available, need nothing but the data itself), Layer 5
needs access to operational metadata: deployment logs, schema change
history, config management, incident logs. It's also the first layer to
actually consume the synthetic change events generated all the way back at
the start of this project (`data_gen/synthetic_metadata.py` /
`defect_injector.py`), which had been sitting unused in the database until
now.

### Core question and method

Once Layer 2 gives an onset time, scan all change events within a window
around it (the framework spec suggests ±24-72 hours; this implementation
uses 72h), then rank candidates by three factors, exactly as the spec
describes:
- **Temporal proximity** — linear decay to 0 at the edge of the window;
  closer to onset = higher suspicion.
- **Scope overlap** — 1.0 if the event's `scope_column` explicitly names
  one of the fingerprint's affected fields, 0.5 if only the table matches,
  0.0 otherwise.
- **Segment match** — 1.0 if the event's `scope_source_system` matches the
  fingerprint's dominant segment, weighted highest of the three (0.4 vs 0.3
  each for the other two), matching the spec's own framing that a segment
  match carries "much higher suspicion" than temporal or scope alone.

One deliberate implementation choice: segment matching uses Layer 1's
`dominant_segment_column`/`value` (which is always evaluated against
`source_system`) rather than Layer 3's final drill-down path. This is
because real operational logs are typically scoped at the system/vendor
level, not down to individual entities — matching against the same
granularity the change events actually carry is more realistic than trying
to match Layer 3's most-specific answer, which might be a finer-grained
dimension (like a single station or a single day) that operational logs
wouldn't reference at that granularity.

### A Layer 3 refinement this layer's construction required

Building Layer 5 surfaced a real gap in Layer 3's tie-breaking. The
Staleness fingerprint has two dimensions that both explain 100% of its
defects — `source_system=NOAA_ISD` and `country=GB` — because in this test
bed, the one stale station happens to be the only GB station, making the
two collinear. Layer 3's original tie-break preferred whichever dimension
had higher statistical lift, which picked `country=GB` (a smaller, more
"enriched" population) over `source_system=NOAA_ISD`. That's not wrong
mathematically, but it made Layer 5 correlation *worse*: the synthetic
change events only carry `source_system`-level scope, so `country=GB`
gave Layer 5 nothing to match against.

Fixed by changing Layer 3's tie-break priority: among dimensions tied on
coverage, prefer whichever is more **actionable for investigation** —
`source_system`, entity IDs, and time windows rank ahead of incidental
attributes like `country`/`region` — using lift only as a final tiebreaker
within the same priority tier. This is a legitimate general design
decision, not a hack to make one test case pass: real change/incident logs
are almost always scoped to systems and entities, not to attributes that
merely happen to be statistically correlated with them. Staleness now
resolves to `source_system=NOAA_ISD`; every other fingerprint's result was
unaffected by the change.

### Validated result

All 4 real defects correlate to their true causal event as the #1 (and in
3 of 4 cases, the *only*) candidate in the window, verified directly
against each event's ground-truth root-cause linkage (not just by eyeballing descriptions):

| Defect | Top-matched event | Composite score | Verified against ground truth |
|---|---|---|---|
| Corruption (RC-001) | `code_deploy`: humidity normalization refactor | 1.00 (perfect on all 3 factors) | ✅ RC-001 |
| Omission (RC-002) | `schema_change`: mart derived-column migration | 1.00 (perfect on all 3 factors) | ✅ RC-002 |
| Duplication (RC-003) | `infra_event`: PagerDuty network blip during the batch load | 0.45 (no segment match — expected, duplication isn't tied to a source_system) | ✅ RC-003 |
| Staleness (RC-004) | `source_system_change`: vendor changelog, feed suspended | 0.85 | ✅ RC-004 |

The low-volume, `needs_review`-flagged spurious `sea_level_pressure`
fingerprint correctly returns "no change events found" — Layer 2 couldn't
establish a real onset for it (there's no real temporal pattern behind a
single-record statistical fluke), so Layer 5 has nothing to correlate
against, and correctly says so rather than forcing a match.

---

## 10. Layer 6: Lineage Traversal (`backend/app/layers/layer6_lineage.py`, `backend/app/llm/gemini_client.py`)

The sixth and final RCA layer. It answers the most precise question the
framework asks: **at which specific stage in the data pipeline was the
defect introduced?** Both paths from the framework spec (Section 8) are
implemented:

### 8.1: With formal lineage — boundary testing + code inspection

Starting from the stage where a defect was detected, walk upstream through
the pipeline (mart → warehouse → staging → raw), re-testing for the
defect's presence at each boundary. The last stage where it's still
present, immediately before the first stage where it's absent or
undefined, marks the injection point. Once that boundary is found, the
transformation code for that stage is pulled and handed to an LLM (Gemini
2.5 Flash) for inspection — this is the first point in the whole project
where the Gemini integration is actually exercised, not just planned for.
If no `GEMINI_API_KEY` is configured, this degrades gracefully to a
keyword-overlap fallback rather than failing the layer.

### 8.2: Without formal lineage — query log mining

The same synthetic query log generated at the very start of the project
(daily `INSERT...SELECT` entries, sitting unused until now) is mined via
regex — deliberately parsing the raw `query_text` string rather than
reading the log's own pre-parsed `target_table`/`source_tables` columns,
so this is genuine mining rather than a label-reading exercise. The
reconstructed graph matched the formal lineage graph **exactly** — proof
the two independent techniques converge on the same answer, exactly as the
framework spec's own claim about query log mining ("reconstructs 80-90% of
lineage... the single most underused technique in the industry").

### A real bug this layer's construction surfaced (and fixed)

The statistical-tier presence check initially only implemented the
null-rate logic appropriate for the Omission fingerprint (`auto:null_spike`),
but the low-volume spurious `sea_level_pressure` fingerprint comes from a
different statistical technique (`auto:zscore`, a numeric outlier, not a
null value). Applying the null-rate check to it always returned "absent,"
which meant boundary testing reported the defect as *not present at its
own detection stage* — an obviously wrong result. Fixed by branching on
which auto-detection technique actually produced the fingerprint and
applying the matching check (recomputing the modified z-score at each
stage for zscore-derived fingerprints, rather than reusing a null-rate
check that doesn't apply).

### Validated result, including a genuinely interesting negative case

| Defect | Injection point found | Correct? |
|---|---|---|
| Corruption (RC-001) | `staging → warehouse` | ✅ — this is the real transform bug |
| Omission (RC-002) | `warehouse → mart` | ✅ — correct boundary |
| Duplication (RC-003) | `staging → warehouse` | ✅ (honestly caveated — see below) |
| Staleness (RC-004) | `staging → warehouse` | ✅ (honestly caveated — see below) |
| Spurious `sea_level_pressure` fingerprint | `<source> → raw` | ✅ — present at *every* stage identically, correctly identified as natural source noise rather than a pipeline-introduced defect |

The spurious fingerprint result is worth calling out specifically: this is
the *third* independent mechanism (after Layer 1's `needs_review` flag and
Layer 5 finding no correlated change event) that correctly flags this
fingerprint as noise rather than a real defect — via a completely
different technique each time. That kind of independent, converging
evidence is exactly what the framework's layered design is for.

### Two honest limitations, documented rather than hidden

**Boundary testing vs. true root cause for Duplication and Staleness.**
This test bed's defects were injected directly into the `warehouse`
DataFrame in Python, not by writing genuinely buggy SQL into the simulated
pipeline. For Corruption, that Python injection deliberately mirrors a
real transform bug, so boundary testing and code inspection both correctly
implicate the humidity-normalization transform. But Duplication and
Staleness are *operational* events (a network retry, a vendor outage), not
code bugs — boundary testing still correctly and honestly reports "the
data changed between staging and warehouse" (which is factually true in
this pipeline), and the LLM correctly recognized the transform code at
that boundary doesn't actually explain either defect. This is exactly why
Layer 5 (change events) and Layer 6 (lineage) are separate, complementary
layers: Layer 6 answers "which stage," Layer 5 answers "what/why," and
neither is sufficient alone.

**A live LLM hypothesis that was plausible but factually wrong.** Once a
real `GEMINI_API_KEY` was configured, the LLM's diagnosis of the Omission
defect was well-reasoned but incorrect about the actual mechanism: it
hypothesized that `observed_at` is NULL upstream for NOAA_ISD records,
causing `DATE_TRUNC(NULL)` to cascade into null `sunrise`/`sunset`. In
reality, `observed_at` is fully populated throughout — the injected defect
directly nulls `sunrise`/`sunset` in `mart`, simulating a schema migration
that temporarily disabled that derivation step outright, not a null input
propagating through it. The LLM produced a coherent, plausible story
consistent with the code and the symptom, but couldn't distinguish it from
the true mechanism without checking the data itself (one query —
`SELECT COUNT(*) FROM warehouse.weather_clean WHERE observed_at IS NULL
AND source_system='NOAA_ISD'` — would have refuted it immediately). This
is a clean, concrete demonstration of *why* the framework spec has a
separate Validation & Confirmation stage (Section 10) after hypothesis
generation rather than trusting any single layer's output directly: LLM-
generated hypotheses are a starting point for verification, not a
conclusion.

---

## 11. Validation & Confirmation (`backend/app/validation/validate.py`, `backend/app/layers/defect_presence.py`)

Framework doc Section 10: **a hypothesis is not a conclusion until it's
validated.** This module empirically tests hypotheses against the data
rather than trusting them, and was built specifically because it was
needed — the live Gemini run in Layer 6 (previous session) produced a
plausible-sounding but factually wrong explanation for the Omission
defect, and this is the machinery that catches exactly that kind of error.

### A refactor first: `defect_presence.py`

Layer 6's boundary testing needed a "does this defect show up in this
data slice?" check across pipeline *stages*; Validation's counterfactual
tests need the same check across *segments* (inside vs. outside the
claimed segment). Rather than duplicate that rule/statistical-technique
dispatch logic in two places — a real risk of the copies silently drifting
out of sync — it was extracted into a shared `defect_presence.py` module
that both layers import. Layer 6 was re-verified after the refactor to
confirm identical output before building anything new on top of it.

### Four techniques implemented

- **Counterfactual Test (segment)** — does the defect also appear outside
  the claimed segment? If so, the segment hypothesis is incomplete.
- **Counterfactual Test (mechanism)** — checks a *specific causal claim*
  directly against the data, independent of any reasoning about code. This
  is the one that catches LLM hypotheses that sound right but aren't: it
  doesn't evaluate the code inspection's logic at all, it just checks
  whether the claimed fact (e.g. "column X is null upstream") is actually
  true.
- **Reproduce the Defect** (the doc's own "strongest validation" method) —
  independently re-implements the claimed mechanism (from Layer 6's code
  inspection) and applies it to *clean* data in the claimed segment,
  checking whether that alone reproduces the failure pattern. Deliberately
  does **not** import `defect_injector.py` for this — reusing the
  ground-truth generator to validate a hypothesis would just be checking
  the generator against itself, proving nothing about whether the
  hypothesis is genuinely correct.
- **Fix and Verify** — applies the hypothesized fix and confirms it
  actually resolves the defect.

Two of the doc's five techniques aren't implemented as separate automated
checks here: A/B Comparison is essentially what Layer 2's onset detection
already does, and Expert Review is what Layer 1's `needs_review`
escalation path is for — re-implementing either here would just duplicate
an existing layer.

### Validated result: one confirmation, one refutation, both correct

**Corruption (relative_humidity) — CONFIRMED, 3/3 checks pass.** The
segment claim holds; independently re-implementing the "double-multiply"
mechanism against clean API_v3 data reproduces out-of-range values 100% of
the time; and the inverse fix resolves 100% of the real failing records
back into valid range. This is genuine, load-bearing validation, not just
re-confirming what earlier layers already said.

**Omission (sunrise) — the LLM's specific mechanism claim REFUTED.**
Across two separate live sessions, Gemini gave essentially the same wrong
explanation both times — "`observed_at` is NULL upstream for NOAA_ISD,
causing `DATE_TRUNC(NULL)` to cascade" — which is a plausible reading of
the code and the symptom, but not what actually happened. The mechanism
counterfactual test refutes it immediately: `observed_at` is populated
100% of the time for the affected records. The segment claim (NOAA_ISD)
still holds — only the specific causal *mechanism* was wrong, which is
exactly the distinction Validation exists to catch: a hypothesis can be
right about *where* while being wrong about *why*.

That the LLM produced the same specific wrong claim on a second, separate
run is itself informative: it's not a one-off fluke, it's a systematic
blind spot given only code and a symptom pattern with no access to the
actual data — which is precisely the argument for never trusting an LLM
hypothesis without empirical validation.

---

## 12. How to run everything built so far

### 12.1 Setup (one-time)

```powershell
cd rca-framework\backend
py -3.13 -m venv rca
rca\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Use Python 3.12 or 3.13. Avoid 3.14 for now — several pinned dependencies
don't yet ship prebuilt wheels for it.

### 12.2 Generate the test bed

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

### 12.3 Run Layer 1

```powershell
set PYTHONPATH=.
python app\layers\layer1_defect_characterization.py
```

Expected output: a list of 7 fingerprint dicts across the `warehouse` and
`mart` stages, covering all 4 injected defects (Staleness, Duplication,
Corruption, and two Omission entries for `sunrise`/`sunset`).

### 12.4 Run Layer 2

```powershell
python app\layers\layer2_temporal.py
```

(Assumes `PYTHONPATH` is still set from the previous step, in the same
shell session.)

Expected output: onset date, detection technique, and temporal pattern for
each of the 4 defects — see the validated-result table in Section 6 above
for the exact values to check against.

### 12.5 Run Layer 3

```powershell
python app\layers\layer3_segmentation.py
```

(Assumes `PYTHONPATH` is still set from the previous steps, in the same
shell session.)

Expected output: a drill-down path and statement for each of the 4 defects
— see the validated-result table in Section 7 above for the exact segments
to check against.

### 12.6 Run Layer 4

```powershell
python app\layers\layer4_statistical.py
```

(Assumes `PYTHONPATH` is still set from the previous steps, in the same
shell session.)

Expected output: ranked column deltas and a summary for each fingerprint —
see the validated-result table in Section 8 above. Note: a low-volume
`Corruption` fingerprint on `sea_level_pressure` (1 record, flagged
`needs_review`) may also appear — this is the statistical tier correctly
catching its own false positive, not an error.

### 12.7 Run Layer 5

```powershell
python app\layers\layer5_change_events.py
```

(Assumes `PYTHONPATH` is still set from the previous steps, in the same
shell session.)

Expected output: a ranked list of correlated change events per fingerprint
— see the validated-result table in Section 9 above. The spurious
`sea_level_pressure` fingerprint should report "no change events found."

### 12.8 Run Layer 6

```powershell
python app\layers\layer6_lineage.py
```

(Assumes `PYTHONPATH` is still set from the previous steps, in the same
shell session.)

To enable live LLM code inspection rather than the keyword fallback, create
`backend\.env` with `GEMINI_API_KEY=your-key-here` before running. Expected
output: query log mining reconstructing the lineage graph exactly, plus a
boundary-test result and code inspection for each fingerprint — see the
validated-result table and honest limitations in Section 10 above.

### 12.9 Run Validation

```powershell
python app\validation\validate.py
```

(Assumes `PYTHONPATH` is still set from the previous steps, in the same
shell session.)

Expected output: the Corruption hypothesis passing all 3 applicable checks
(`VERDICT: CONFIRMED`), and the Omission mechanism claim failing its
counterfactual check (`VERDICT: REFUTED`) — see Section 11 above for the
full context on why that refutation is the point, not a failure.

### 12.10 (Not yet needed) Full Docker stack

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

## 13. Status

- [x] Project scaffold + Docker Compose setup
- [x] DB schema (pipeline tables + RCA metadata tables)
- [x] Synthetic test bed generator with 4 ground-truth-labeled defects
- [x] Layer 1: Defect Characterization (rule-based + auto-derived statistical tiers), validated against ground truth
- [x] Layer 2: Temporal Analysis (onset detection + pattern classification), validated against ground truth
- [x] Layer 3: Segmentation Analysis (multi-dimensional drill-down), validated against ground truth
- [x] Layer 4: Statistical Profiling & Cross-Field Analysis, validated against ground truth
- [x] Layer 5: Change Event Correlation, validated against ground truth
- [x] Layer 6: Lineage Traversal (boundary testing + LLM code inspection + query log mining), validated against ground truth
- [x] Validation & Confirmation (counterfactual tests, reproduce-the-defect, fix-and-verify), including catching a real wrong LLM hypothesis
- [ ] Synthesis / hypothesis ranking engine
- [ ] Knowledge base
- [ ] FastAPI routes
- [ ] React frontend
- [ ] Deployment (Railway/Render)
