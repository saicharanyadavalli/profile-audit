# Architectural Design & Technical Specifications: profile-audit

This document specifies the internal architecture, artifact data flows, validation mechanics, safe HTML embedding model, and design decisions governing the `profile-audit` agent skill.

---

## 1. Architectural Principles

### 1.1 Report-Only Discipline
The skill strictly observes, profiles, and reports. It never alters application source code, configuration files, migration scripts, or production databases. Profiling scripts run read-only workloads or self-cleaning synthetic test drivers against disposable environments. Fix recommendations are presented as actionable engineering guides in `FIXES.md` and `PROFILING-AUDIT.md` rather than automated patches.

### 1.2 Zero External Dependencies
To ensure immediate portability across diverse host environments, containers, and agent platforms, all skill scripts (`validate.py`, `build_report.py`, `render_markdown.py`, `classify.py`, `measurements_tools.py`) utilize exclusively the Python 3.9+ standard library. External package installations are strictly prohibited. The generated HTML dashboard is 100% self-contained, inlining all CSS and JavaScript with zero external network requests (no CDNs, Google Fonts, or external scripts).

### 1.3 Measure, Never Guess & Never Fabricate
Every reported bottleneck must trace directly to deterministic measurements captured in canonical measurement records. Agents are prohibited from inferring latency solely from static code appearance. Any static pattern discovered without runtime timing is classified as a static code observation, never a verified runtime finding.

### 1.4 Complete Scope Classification & Owned Time
Performance audits often suffer from survivorship bias by reporting only the slowest functions or misattributing framework overhead. `profile-audit` evaluates execution time using **`owned_ms`**—the unit's `total_ms` minus the `total_ms` of its nearest descendant nodes that are themselves ledger units. Non-unit library descendants (SQL drivers, HTTP clients, serialization helpers) belong to the nearest unit ancestor. It accounts for 100% of discovered functional units in scope:
- `hot`: `audit_share_pct >= 10.0%` **and** `owned_ms >= 5.0 ms`.
- `warm`: `audit_share_pct >= 1.0%` **and** `owned_ms >= 1.0 ms`.
- `cold`: Executed units below warm thresholds.
- `not_exercised`: Units discovered during reconnaissance not reached by any measured workflow.
- `insufficient_data`: Execution observed but reliable function-level timing could not be obtained (reason required).

### 1.5 Provenance on Everything
Every call-tree node carries a required `source` attribute (`measured`, `simulated`, or `estimated`). Round numbers are never proof of measurement. Findings whose headline metrics derive from simulated or estimated nodes can never be verified.

---

## 2. Multi-Agent Orchestration & Execution Model

The skill operates under an adaptive orchestration model:
- **Targeted Mode (Mode B)**: A single agent executes targeted benchmark runs sequentially, computes root causes, answers directly in conversation, and writes `PROFILING-AUDIT.md`.
- **Full Audit Mode (Mode A)**: Multi-phase pipeline using specialized sub-agents where supported:
  - *Parallelism*: Permitted during Reconnaissance (Phase 1), Driver Authoring (Phase 2), Root-Cause Analysis (Phase 4), and Verification (Phase 5).
  - *Strict Sequential Execution*: **Measurement runs (Phase 3) must be strictly sequential.** Running benchmark workloads concurrently on the same machine induces CPU scheduling contention, cache eviction, and socket queue delays that invalidate timing measurements.
- **Mode Dispatch Discipline**: Never escalate Targeted Mode to Full Audit Mode silently. If the user names specific endpoints, functions, or files, the skill stays strictly in Targeted Mode unless explicitly instructed otherwise.
- **Sub-Agent Fallback**: If the host environment does not support spawning sub-agents, the primary agent executes each phase step-by-step in sequential order without failure.

---

## 3. System Data Flow & Artifact Lifecycle

```text
[ Target Application & Source Code ]
                 │
                 ▼
      Phase 1: Reconnaissance
                 │
                 ├──► .work/architecture.json
                 ├──► .work/run-metadata.json
                 └──► .work/coverage-ledger.json (units initialized with status: "pending")
                 │
                 ▼
         Phase 2: Drivers
                 │
                 └──► repeatable driver scripts & realistic seed data
                 │
                 ▼
       Phase 3: Measurement (Sequential)
                 │
                 ├──► scale & concurrency probes
                 ├──► .work/measurements.json (runs, summary stats, call trees with provenance)
                 └──► .work/coverage-ledger.json (updated to hot, warm, cold, not_exercised via classify.py)
                 │
                 ▼
        Phase 4: Analysis
                 │
                 └──► .work/findings.json (provisional findings: status needs_validation)
                 │
                 ▼
       Phase 5: Verification
                 │
                 └──► .work/findings.json (finalized: verified or needs_validation)
                 │
                 ▼
        Phase 6: Reporting & Assembly
                 │
                 ├──► validate.py (.work/ validation)
                 ├──► build_report.py ──► PROFILING-DASHBOARD.html (embeds all canonical data)
                 ├──► render_markdown.py ──► FINDINGS.md, FIXES.md
                 ├──► validate.py (PROFILING-DASHBOARD.html validation)
                 ├──► render_markdown.py --check (drift verification)
                 └──► delete .work/ on complete validation success
```

---

## 4. Measurement & Attribution Methodology

### 4.1 Repetition & Warm-up Rules
- **Warm-up**: 1 unmeasured warm-up iteration is executed and discarded prior to data collection. This primes CPU caches, resolves lazy module imports, initializes database connection pools, and triggers JIT compilation where applicable.
- **Measured Runs**: 5 sequential iterations are executed under identical parameters.
- **Summary Statistics**:
  - `n`: Number of measured runs.
  - `p50` (median): Primary ranking metric resistant to transient host spikes.
  - `p95`: Captures tail latency under load.
  - `mean`: Arithmetic average across runs.
  - `stdev`, `cv`: Standard deviation and coefficient of variation (`stdev / mean`). If $CV > 0.30$, the measurement is flagged for high variance and triggered for re-measurement.

### 4.2 Function Statistics from Function Samples
- If a function node carries `stats`, those statistics are calculated strictly from the **samples of that function across runs**, never copied from the workflow's overall duration.

### 4.3 Sequential vs. Concurrent Async Tree Arithmetic
- **Sequential Children**:
  `total_ms` must equal `self_ms + sum(child.total_ms)` within tolerance `max(0.5, 0.01 * total_ms)`.
- **Concurrent Children (`children_execution: "concurrent"`)**:
  Child durations are not summed. The parent's wall-clock contribution is the union of active intervals. Tree arithmetic satisfies:
  $$\max(\text{child.total\_ms}) \le \text{total\_ms} \le \text{self\_ms} + \sum \text{child.total\_ms}$$

### 4.4 CPU vs. Wait Separation & Measurement Protocol
Execution duration is split into:
- **`cpu_ms`**: Time spent actively computing instructions on processor cores.
- **`wait_ms`**: Time spent blocked on external I/O (database query response, socket reads, disk writes, sleep timers).
- **Measurement Protocol**: Spans measure monotonic wall time and thread/process CPU time (`time.thread_time_ns()`, `process.cpuUsage()`, etc.):
  `wait_ms = max(0.0, wall_ms - cpu_ms)`. If unavailable, `cpu_ms` and `wait_ms` are `null` and `cpu_wait_source` is `"unavailable"`. Ratios are never assumed.
- **Node Self Times & Rollup**: Nodes store `self_cpu_ms`, `self_wait_ms`, and `cpu_wait_source`. Inclusive `cpu_ms` and `wait_ms` for parent nodes are computed from the leaves up using `measurements_tools.py rollup`.
- **Derived Kind**: `kind` is derived: `"cpu"` if `cpu_ms >= 0.70 * total_ms`, `"wait"` if `wait_ms >= 0.70 * total_ms`, otherwise `"mixed"`.
- **Conservation Law**: `total_ms` must equal `cpu_ms + wait_ms` within measurement precision:
  $$\left|(\text{cpu\_ms} + \text{wait\_ms}) - \text{total\_ms}\right| \le \max(0.02 \times \text{total\_ms}, 0.5)$$

### 4.5 Leaf Attribution & Unattributed Time
- Latency is attributed to the real leaf operation (database query, network request), not to a generic outer wrapper.
- If a non-leaf node has `self_ms >= 5%` of its workflow duration without breakdown or explicit `unattributed` child, it is flagged as unattributed self time (`E_UNATTRIBUTED_TIME`).

### 4.6 Line-Level Profiling (Pass 2)
- Triggered for units with status `hot` and units owning $\ge 25\%$ of workflow duration (capped at 5 units).
- Uses runtime native facilities (`sys.settrace`, `--cpu-prof`, `pprof`) or scratch-copy probe injection in `.work/scratch/` (audited repo code is never touched).
- Captures per-line statement execution (`lines[]`) and unaccounted remainder (`residual_ms`) conforming to `measurements.schema.json`.

### 4.7 Severity Rubric & Remediation Safety
- Severity is assigned deterministically:
  - `critical`: `workflow_share_pct >= 50.0%` **and** `owned_ms >= 100.0 ms`.
  - `high`: `workflow_share_pct >= 25.0%` **and** `owned_ms >= 25.0 ms`, OR scale probe projecting $\ge 100.0$ ms at 10× data.
  - `medium`: `workflow_share_pct >= 10.0%` **or** `owned_ms >= 10.0 ms`.
  - `low`: Otherwise.
- **Hard Safety Rule**: Fixes must never recommend lowering cryptographic cost parameters or iteration counts (`E_FIX_WEAKENS_SECURITY`). Offloading or scaling is recommended instead.

---

## 5. Validation Architecture (`validate.py`)

`scripts/validate.py` enforces 33 substantive validation rules across canonical data:
1. `E_PROVENANCE_MISSING`: Call tree node missing required `source`.
2. `E_PROVENANCE_INVALID`: Node `source` is not `measured`, `simulated`, or `estimated`.
3. `E_VERIFIED_FROM_SIMULATED`: Finding is marked `verified` but derives from simulated or estimated timings.
4. `E_NEGATIVE_DURATION`: Negative timing values in runs, nodes, or metrics.
5. `E_DUPLICATE_ID`: Duplicate ID across units, workflows, or findings.
6. `E_BAD_REFERENCE`: Referenced unit ID or workflow ID does not exist.
7. `E_TREE_ARITHMETIC`: Call tree child times contradict parent duration.
8. `E_UNATTRIBUTED_TIME`: Non-leaf node has unexplained self time >= 5% of workflow without breakdown or unattributed child.
9. `E_STATS_COPIED`: Function stats are identical copy of workflow statistics.
10. `E_STATS_WRONG`: Statistical quantiles or summary calculations are mathematically incorrect.
11. `E_DENOMINATOR`: Share calculation uses wrong denominator rather than unit's own workflow.
12. `E_STATUS_MISMATCH`: Ledger unit classification contradicts actual measurements.
13. `E_NOTE_NUMBERS`: Ledger note contains unverified free-text percentage or millisecond numbers.
14. `E_UNCOVERED_HOT`: Hot leaf node consuming >= 10% is omitted from findings and ledger.
15. `E_FIX_PREREQ`: Proposed fix references database tables, migrations, or dependencies missing from prerequisites.
16. `E_UNSUPPORTED_CLAIM`: Algorithmic complexity claim lacks scale probe evidence or contains bare percentage claims without basis.
17. `E_CONCURRENCY_CLAIM`: Concurrency contention claim lacks concurrency probe evidence.
18. `E_STATIC_AS_FINDING`: Finding lacking measured evidence marked as verified bottleneck.
19. `E_PENDING_STATUS`: In strict mode, unit remains in `pending` status.
20. `E_WRAPPER_TIME`: Outer wrapper holds entire block duration while leaf child has zero duration.
21. `E_COMBINED_SUM`: Canonical data contains forbidden cross-workflow aggregate sum.
22. `E_CPU_WAIT_TREE`: Inner node `cpu_ms` or `wait_ms` contradicts sum of children plus own self times.
23. `E_RATIO_SUSPICIOUS`: Suspiciously identical CPU/wait ratios across distinct leaf operations under claimed measured source.
24. `E_FINDING_METRIC_MISMATCH`: Finding metrics contradict classifier metrics for its ledger unit.
25. `E_SEVERITY_MISMATCH`: Finding severity rating contradicts deterministic severity rubric.
26. `E_FIX_WEAKENS_SECURITY`: Fix recommends weakening cryptographic cost, rounds, or security parameters.
27. `E_PROBE_BASELINE_MISMATCH`: Concurrency probe baseline p50 diverges from workflow measured baseline p50 by > 50%.
28. `E_LINE_SUM`: Sum of line profile statement times plus residual diverges from unit owned time by > max(1.0 ms, 5%).
29. `E_LINE_CODE_MISMATCH`: Line profile code text contradicts actual source code lines.
30. `E_HOT_NO_LINE_PROFILE`: Hot unit missing line profile without explicit unavailable reason in strict mode.
31. `E_MACHINE_PLACEHOLDER`: Machine metadata contains generic placeholder strings instead of probed values.
32. `E_RUN_DURATION`: Stamped elapsed run duration is shorter than sum of recorded workflow run durations.
33. `E_HTML_EXTRACT`: Embedded JSON data in HTML report is corrupted or cannot be parsed.

---

## 6. Safe Report Generation & HTML Embedding (`build_report.py`)

`build_report.py` implements a secure sanitization filter before injecting canonical data into `assets/report-template.html`:
```python
def serialize_safe_json(payload: dict) -> str:
    raw_json = json.dumps(payload, indent=2)
    safe_json = (
        raw_json
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    return safe_json
```
The client-side template loads data from:
```html
<script id="report-data" type="application/json">
__REPORT_DATA__
</script>
```
This guarantees complete immunity to XSS injection, prevents script breakout vulnerabilities, and ensures full JSON conformity.

---

## 7. Interactive Flame Graph Architecture

### 7.1 Tree Representation
Hierarchical call trees capture execution frames with execution mode and provenance:
```json
{
  "name": "list_products_with_reviews",
  "unit_id": "unit-002",
  "kind": "wait",
  "source": "measured",
  "children_execution": "sequential",
  "calls": 50,
  "self_ms": 2.1,
  "total_ms": 84.5,
  "children": []
}
```

### 7.2 Geometric Projection & Stack Layout
The flame view renders an inverted icicle / flame layout:
- **Horizontal Width**: Represents the node's relative share of total workflow duration:
  `width_pct = (node.total_ms / root.total_ms) * 100%`
- **Vertical Depth**: Represents call stack nesting level.
- **Division-by-Zero Guard**: If `root.total_ms == 0.0`, width is clamped to 0%.

### 7.3 Visual Dichotomy: CPU vs. Wait
Nodes are visually distinguished by their `kind`:
- **CPU Nodes (`kind: "cpu"`)**: Rendered in warm amber/orange/red tones, indicating active instruction processing.
- **Wait Nodes (`kind: "wait"`)**: Rendered in cool cyan/blue/slate tones, indicating blocked I/O, database queries, network latency, or sleep timers.

---

## 8. Dashboard UI View Specifications (`PROFILING-DASHBOARD.html`)

The dashboard incorporates 8 functional views:
1. **Overview**: Headline KPIs (workflows, units, coverage, verified bottlenecks, needs validation count), method details, data volume, and top bottlenecks.
2. **Architecture**: Discovered component topology, technologies, roles, entry points, and inter-component connections.
3. **Workflows**: Waterfall timelines and execution trees for all measured workflows.
4. **Functions**: Sortable table of all cataloged functions with instant classification filters and slide-out inspector drawer.
5. **Flame Graph**: Interactive flame graph with click-to-zoom, hover tooltips, and reset controls.
6. **Resources**: Telemetry breakdown covering CPU/memory collection method, database table row counts, and external network interactions.
7. **Findings**: Full catalog divided into three explicit sections (Verified Bottlenecks, Needs Validation, Static Observations) with 4-part fix plans and status filters.
8. **Coverage**: Honest classification breakdown including proportional meter and catalog of unexercised units.

---

## 9. Design Decisions & Trade-off Resolutions

| Issue / Decision | Selected Option | Rationale |
|---|---|---|
| Helper script runtime | Python 3.9+ standard library | Available out of the box in nearly all developer and agent environments; eliminates package installation friction. |
| Single HTML dashboard | Inlined data, CSS, and JS | Guarantees complete offline functionality with zero external network requests. |
| Shared unit denominators | Own-workflow duration | Evaluating a shared function against an irrelevant workflow's duration distorts share percentages and causes classification anomalies. |
| Timing precision tolerance | $\max(0.5\text{ ms}, 0.01 \times \text{total\_ms})$ | Accommodates minor sub-millisecond clock jitter on short calls while strictly bounding measurement drift on long runs. |
| Report templating engine | Token string replacement (`__REPORT_DATA__`) | Avoids external dependencies like Jinja2; provides exact, predictable byte-level injection. |
| Working directory cleanup | Auto-delete `.work/` on success | Keeps deliverable neat (exactly 3 files) while preserving intermediate data if any validation check fails. |
