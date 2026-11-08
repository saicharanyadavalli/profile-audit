---
name: profile-audit
description: >-
  Use when profiling applications across any language or framework to locate where
  execution time is spent, identify performance bottlenecks, diagnose why a slow endpoint
  or workflow suffers high latency, or inspect execution via a flame graph. Triggers on
  profile, bottleneck, slow endpoint, high latency, performance audit, where is time spent,
  or flame graph. Do NOT use for functional debugging, static security scanning, or general
  code refactoring.
---

# profile-audit

A portable, zero-dependency skill for profiling applications, measuring where execution time is spent, and diagnosing bottlenecks across any language, framework, or runtime.

---

## 1. Purpose & Scope

The `profile-audit` skill provides automated, non-invasive performance profiling for software applications. It discovers bottlenecks, measures execution time with high precision, classifies every discovered code unit, and produces structured markdown and offline HTML reports.

Operating strictly in report-only mode, the skill never edits audited code, never alters configuration files, and never modifies production or test data stores. It is designed for autonomous coding agents operating in any environment.

### Core Capabilities:
- Uncovers latency hotspots, database bottlenecks, and slow I/O calls.
- Classifies 100% of discovered functional units in scope (`hot`, `warm`, `cold`, `not_exercised`).
- Distinguishes CPU computation from blocked I/O waiting time.
- Categorizes root causes using 10 standardized categories (`expensive_compute`, `n_plus_one_query`, `missing_index`, `blocking_io`, `missing_timeout`, `unbounded_query`, `unpooled_resource`, `heavy_import`, `redundant_io`, `inefficient_algorithm`).
- Produces canonical data validated against relational and substantive rules.
- Delivers an offline, self-contained interactive dashboard and markdown reports.

---

## 2. Core Operating Principles

1. **Report-Only Discipline**: Never modify the target application's source files, configuration settings, migrations, unit tests, or data stores (except through driver workflows created against disposable test targets). Never create git branches or apply speculative fixes. Write all findings and recommendations into reports.
2. **Measure, Never Guess & Never Fabricate**: Every reported timing, call count, and metric must trace directly to real measured executions recorded during profiling runs. Speculative findings based solely on static code reading can only be classified as static observations, never verified runtime bottlenecks. Never fabricate timings.
3. **Classify Everything Honestly**: Every functional unit in scope must receive an explicit status classification from `classify.py`: `hot`, `warm`, `cold`, or `not_exercised`. Units not reached by any benchmark workflow must be classified as `not_exercised`. Never imply that unreached code is fast.
4. **Strictly Sequential Measurement**: Exploration and analysis may run in parallel if sub-agents are supported, but all benchmark measurement drivers run strictly sequentially. Concurrent measurements compete for CPU caches, memory bandwidth, and disk I/O, distorting timing data.
5. **Provenance on Everything**: Every call tree node has a required source tag: `measured`, `simulated`, or `estimated`. Findings derived from simulated or estimated timings can never be verified bottlenecks.
6. **Relative Machine Rankings**: Profiling metrics reflect the host machine executing the audit (CPU speed, memory speed, disk I/O, operating system). Findings represent relative performance rankings on the test hardware, not universal production throughput guarantees.
7. **Local by Default**: Benchmark against local code and local databases first. Profile remote or deployed targets only upon explicit user request.
8. **No Sandbox Mandate**: Run in whatever environment the orchestrator operates. Issue one non-blocking notice prior to write-heavy drivers without halting or refusing execution.
9. **No Mandatory Profiler Packages**: Rely on built-in runtimes, existing tools, OS facilities, and monotonic timers. Do not require installing external profiling packages.
10. **Zero External Dependencies**: All helper scripts use the Python 3.9+ standard library only. The generated HTML dashboard is self-contained with zero external CDN, font, or script requests.

---

## 3. Mode Dispatch & Selection Logic

Evaluate the user's prompt to choose the operating mode:

| Mode | Trigger Condition | Execution Model | Outputs Produced |
|---|---|---|---|
| **Conversational** | User asks a conceptual or conversational question without requesting profiling files | Single agent, direct response | Conversational answer; no files created |
| **Targeted Mode (Mode B)** | Request names specific functions, endpoints, files, or workflows (e.g., "Why is /api/signup slow?", "Profile these 4 functions") | Single agent, targeted execution (Read `TARGETED-MODE.md`) | Conversational summary + exactly one file: `PROFILING-AUDIT.md` |
| **Full Audit Mode (Mode A)** | User invokes without narrower scope (e.g. `/profile-audit`) or explicitly requests a full, entire, or whole-repository audit | Multi-phase workflow (Read phase guides) | Exactly three final files: `PROFILING-DASHBOARD.html`, `FINDINGS.md`, `FIXES.md` |

### Mode Dispatch Decision Procedure:
1. If the user asks only for a conversational answer, answer directly and create no files.
2. If the user names specific functions, endpoints, files, or workflows, dispatch to **Targeted Mode (Mode B)**.
3. If the user asks for a whole-repo audit or invokes bare `/profile-audit` with no narrower scope, dispatch to **Full Audit Mode (Mode A)**.
4. **Never escalate Targeted Mode to Full Audit Mode silently.** If the user asks about a specific endpoint, profile only that endpoint and generate only `PROFILING-AUDIT.md`.

---

## 4. Local vs. Cloud Profiling

The skill defaults to local execution against local code and local databases.

### Remote / Cloud Target Rules:
1. Switch to remote or deployed profiling only when the user explicitly requests a remote URL or deployed environment.
2. When profiling a remote target, read and follow `CLOUD-TARGET.md`.
3. Measure external latency outside-in: DNS resolution, TLS handshake, TTFB, and response headers (`Server-Timing`).
4. Query cloud observability APIs (metrics, traces) only if credentials are provided. Never infer function-level call trees or self/total times from outside-in data without an in-process profiler.
5. Issue this mandatory notice in conversation and report outputs:
   > "Outside-in cloud profiling measures external response latency and network transit, but cannot provide in-process function call trees or self/total times. Function-level detail requires running locally on the codebase."
6. Record `target_type: "deployed"` in run metadata.

---

## 5. Full Audit Lifecycle (Six Phases)

Load each phase instruction file only when that specific phase starts:

### Phase 1: Reconnaissance
- **Action**: Read `RECONNAISSANCE.md`.
- **Scope**: Detect runtime stack, entry points, background tasks, data stores, external dependencies, host machine environment (via `scripts/env_probe.py`), and static latency anti-patterns.
- **Execution**: Run discovery sub-agents in parallel if supported; otherwise scan sequentially.
- **Output**: Write `.work/architecture.json`, `.work/run-metadata.json` (with probed machine details and `started_at` timestamp via `measurements_tools.py stamp`), and initial `.work/coverage-ledger.json` with every discovered unit initialized to status `pending`.
- **Done Condition**: All in-scope code units cataloged with status `pending`.

### Phase 2: Workflow Drivers
- **Action**: Read `DRIVERS-AND-MEASUREMENT.md` section on Drivers.
- **Scope**: Author repeatable per-workflow driver scripts and seed realistic test data volumes (hundreds or thousands of records, not empty tables).
- **Execution**: Author drivers in parallel if supported.
- **Done Condition**: Repeatable driver suite ready covering all discovered application entry points and key workflows (aiming for 100% endpoint coverage).

### Phase 3: Sequential Measurement
- **Action**: Read `DRIVERS-AND-MEASUREMENT.md` section on Measurement.
- **Scope**: Execute strictly sequential runs (1 warm-up iteration discarded, 5 measured iterations). Measure wall and CPU clocks (`time.thread_time_ns()`, `process.cpuUsage()`, etc.) to isolate CPU vs wait without assuming ratios. Run leaf-up CPU/wait rollup via `measurements_tools.py rollup`. Execute Phase 3 Pass 2 line-level profiling for hot units and units owning >= 25% of their workflow. Execute scale and concurrency probes where appropriate. Stamp `finished_at` via `measurements_tools.py stamp`.
- **Execution**: Strictly sequential. Never execute benchmark measurement drivers concurrently.
- **Output**: Write `.work/measurements.json` with summary statistics (n, mean, p50, p95, stdev, cv, min, max), call trees with required `source` provenance and `cpu_wait_source`, and top-level `line_profiles[]`. Update `.work/coverage-ledger.json` unit metrics (`owned_ms`, `audit_share_pct`, `workflow_share_pct`) and statuses using `classify.py`:
  - `hot`: `audit_share_pct >= 10.0` **and** `owned_ms >= 5.0 ms`.
  - `warm`: `audit_share_pct >= 1.0` **and** `owned_ms >= 1.0 ms`.
  - `cold`: executed, below warm thresholds.
  - `not_exercised`: not reached by any measured workflow (never called dead code).
  - `insufficient_data`: execution observed but reliable function-level timing could not be obtained (reason required).
- **Done Condition**: Zero units remain `pending`; `.work/measurements.json` written and valid.

### Phase 4: Root-Cause Analysis
- **Action**: Read `ANALYSIS.md`.
- **Scope**: For every unit marked `hot` (and high-impact shared warm units), trace call trees, inspect line profiles, evaluate CPU vs. wait ratios, classify root cause into standard categories, assign severity by rubric (`critical`: >= 50% & >= 100 ms; `high`: >= 25% & >= 25 ms or scale probe; `medium`: >= 10% or >= 10 ms; `low` otherwise), and draft structured remediation plans with prerequisites, line references, and projected impacts with arithmetic basis. Hard rule: never recommend lowering cryptographic cost or security parameters to gain speed.
- **Execution**: Run analysis sub-agents in parallel per hot unit if supported; otherwise sequentially.
- **Output**: Write provisional `.work/findings.json` (status: `needs_validation`).
- **Done Condition**: Every hot unit has documented root cause and structured fix plan conforming to findings schema.

### Phase 5: Adversarial Verification
- **Action**: Read `VERIFICATION-AND-REPORTING.md` section on Verification.
- **Scope**: Independent verification checks timing conservation, causal links, tree arithmetic, and variance. If CV > 0.30, automatically re-measure to confirm stability. Assign `verified` only to findings backed by measured evidence.
- **Output**: Finalize `.work/findings.json`.
- **Done Condition**: Verified findings backed strictly by measured data; provisional findings tagged `needs_validation`.

### Phase 6: Reporting & Assembly
- **Action**: Read `VERIFICATION-AND-REPORTING.md` section on Reporting.
- **Scope**: Run validation, generate markdown reports, build interactive dashboard, and clean up temporary data.
- **Steps**:
  1. Run `python skills/profile-audit/scripts/validate.py <run-dir>/.work --strict`.
  2. Run `python skills/profile-audit/scripts/build_report.py <run-dir>/.work --out <run-dir>/PROFILING-DASHBOARD.html`.
  3. Run `python skills/profile-audit/scripts/render_markdown.py --mode full --data <run-dir>/PROFILING-DASHBOARD.html --out-dir <run-dir>`.
  4. Run `python skills/profile-audit/scripts/validate.py <run-dir>/PROFILING-DASHBOARD.html --strict`.
  5. Run `python skills/profile-audit/scripts/render_markdown.py --mode full --check --data <run-dir>/PROFILING-DASHBOARD.html --out-dir <run-dir>`.
  6. Inspect generated files. If all pass, delete `<run-dir>/.work/`. If any check fails, keep `.work/` and report error.
- **Done Condition**: Exactly three final files in `<run-dir>`: `PROFILING-DASHBOARD.html`, `FINDINGS.md`, `FIXES.md`.

---

## 6. Output Directory & Artifact Suite Rules

1. Default output directory: `~/profile-audit/<repo-name>/run-<N>/` (located outside the target repository). Inside the target repository is permitted only if confirmed to be git-ignored.
2. In Full Audit Mode, the final deliverable consists of **exactly three files**:
   - `PROFILING-DASHBOARD.html`
   - `FINDINGS.md`
   - `FIXES.md`
3. In Targeted Mode, the final deliverable consists of **exactly one file**:
   - `PROFILING-AUDIT.md` (plus conversation summary)
4. No other user-facing files (`architecture.md`, `COVERAGE.md`, `NEEDS-VALIDATION.md`, loose JSON files) may remain in the output directory after successful completion.

---

## 7. Execution Fallback Without Sub-Agents

When operating in an environment that does not support spawning sub-agents:
1. Do not fail, halt, or request intervention.
2. Execute each workflow phase sequentially using the primary agent.
3. Maintain intermediate state in `.work/` across steps.
4. Carry forward all inputs, outputs, and validation rules exactly as specified in the phase instruction files.

---

## 8. Anti-Patterns to Avoid

- **Speculative Editing**: Never modify audited application code to test a hypothesis.
- **Concurrent Benchmarks**: Never run multiple measurement drivers at the same time.
- **Silent Escalation**: Never escalate Targeted Mode to Full Audit Mode without explicit user instruction.
- **Toy Data**: Never benchmark against empty or 5-row database tables; always seed realistic volumes.
- **Skipping Warmups**: Never record timing from the first iteration without priming caches and runtime state.
- **Fabricated Numbers**: Every timing citation must exist in canonical measurements.
- **Simulated Provenance as Verified**: Never assign status `verified` to findings derived from simulated or estimated nodes.
- **Ignoring Validation Errors**: Never deliver reports if `validate.py --strict` or markdown drift check fails.
- **External Report Requests**: Never allow the HTML dashboard to request remote fonts, CDNs, or scripts.
