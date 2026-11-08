# Targeted Mode: Single-Agent Targeted Profiling

## Purpose
Targeted Mode provides a rapid, focused profiling workflow for targeted performance inquiries without running the full six-phase repository audit. It investigates specifically identified endpoints, functions, or single workflows, measures execution time deterministically, isolates root causes, and delivers actionable recommendations.

Targeted Mode is the default mode when specific targets are mentioned. It runs as a single agent, provides a direct summary in conversation, and writes **exactly one file** to disk: `PROFILING-AUDIT.md`.

## Inputs
1. **Target Specification**: Names of specific functions, endpoint routes, background jobs, or single workflow specified by the user (e.g., `POST /api/v1/checkout`, `calculate_hash()`, `process_order`).
2. **Target Repository Path**: Path to the local project repository.
3. **Target Source Files**: The files containing the target functions and their immediate call chains.
4. **Execution Context**: Parameters, environment variables, or seed data needed to exercise the target.

---

## Steps

### Step 1: Scope Lockdown and Mode Confirmation
- Verify that the user requested profiling for specific units or workflows, rather than a full repository audit.
- Confine all code reading and measurement exclusively to the requested targets and their direct dependencies.
- **Never escalate Targeted Mode to Full Audit Mode silently.** If the user asks for whole-repo analysis later, instruct them to explicitly request a full audit.

### Step 2: Target Code Inspection and Driver Setup
- Locate target functions or endpoints in the source code. Record the exact file paths and line numbers.
- Construct a minimal, repeatable driver script or invocation command to exercise the target.
- If data is required, ensure realistic input parameters or database state are used rather than empty stubs.
- Issue one short non-blocking notice if write operations will be performed against a database.

### Step 3: Sequential Benchmark Execution
- Select the profiler following the standard hierarchy:
  1. Native sampling profiler (e.g. `cProfile`, native runtime profiler).
  2. Runtime instrumentation or request middleware.
  3. High-resolution monotonic timers (`time.perf_counter_ns()`, `process.hrtime.bigint()`).
- Execute **1 warm-up run** to prime JIT compilation, database connection pools, and file caches. Discard warm-up timings.
- Execute strictly **5 measured repetitions** sequentially. Never run iterations concurrently.
- Record the duration of each measured run in milliseconds.

### Step 4: Metric Extraction & Provenance
- Compute summary statistics across the 5 measured runs using sample values:
  * `n`: Number of runs (5).
  * `mean`, `p50`, `p95`, `stdev`, `cv`, `min`, `max`.
- Separate CPU execution time from waiting time using real CPU clocks (`time.thread_time_ns()`, `process.cpuUsage()`, etc.):
  * `cpu_ms`: Active CPU execution (computation, parsing, hashing, sorting).
  * `wait_ms`: Blocked I/O waiting (database queries, network socket reads, disk reads, sleeps): `wait_ms = max(0, wall_ms - cpu_ms)`.
  * Ensure conservation: `| (cpu_ms + wait_ms) - total_ms | <= max(0.02 * total_ms, 0.5)`.
- Derive `kind`: `"cpu"` if `cpu_ms >= 0.70 * total_ms`, `"wait"` if `wait_ms >= 0.70 * total_ms`, otherwise `"mixed"`.
- Tag all nodes with required `source`: `"measured"`, `"simulated"`, or `"estimated"`.

### Step 5: Root Cause Diagnosis & Fix Formulation
- Inspect the source code at the hotspot line numbers to determine why time is spent there. Optionally run Phase 3 Pass 2 line-level profiling for deep statement attribution.
- Classify the primary root cause into one of the 10 standard categories:
  * `expensive_compute`, `n_plus_one_query`, `missing_index`, `blocking_io`, `missing_timeout`,
    `unbounded_query`, `unpooled_resource`, `heavy_import`, `redundant_io`, `inefficient_algorithm`.
- Assign severity strictly using the rubric: `critical` (>= 50% & >= 100 ms), `high` (>= 25% & >= 25 ms or scale probe >= 100 ms at 10x), `medium` (>= 10% or >= 10 ms), `low` otherwise.
- Formulate a concrete remediation plan:
  * Title and explanation.
  * Step-by-step implementation actions citing line numbers.
  * Prerequisites (migrations, configuration flags, client updates).
  * Projected impact with arithmetic basis (e.g. "removes 149.6 of 180.5 ms = 82.9% if fully offloaded"). Bare percentages are prohibited.
  * **Hard Security Rule**: Never recommend lowering cryptographic cost or iteration counts. Offload or add capacity instead.

### Step 6: Temporary Canonical Data & Validation
Targeted Mode enforces the same evidence standards as Full Mode:
1. Write temporary canonical data (`run-metadata.json`, `coverage-ledger.json`, `measurements.json`, `findings.json`) into a temporary working directory `<run-dir>/.work/`.
2. Run `python skills/profile-audit/scripts/validate.py <run-dir>/.work` to verify data consistency.
3. Generate `PROFILING-AUDIT.md`:
   ```bash
   python skills/profile-audit/scripts/render_markdown.py --mode targeted --data <run-dir>/.work --out-dir <run-dir>
   ```
4. Delete the temporary `<run-dir>/.work/` directory.

### Step 7: Deliver Deliverables
- Present a concise tabular summary directly in the conversation:
  * Target function/endpoint and location (`file:line`).
  * Measured latency (`p50`, `p95`, `mean`), CPU vs. wait split, and provenance badge.
  * Primary root-cause category and summary of recommended fix.
- Verify that **exactly one file** exists on disk: `PROFILING-AUDIT.md`.
- Verify that NO other files (`PROFILING-DASHBOARD.html`, `FINDINGS.md`, `FIXES.md`, `coverage-ledger.json`, etc.) were created.

---

## Sub-Agent Prompt Template: Targeted Profiling
```text
You are a targeted performance profiling sub-agent for profile-audit.
Goal: Profile the targeted function or endpoint requested by the user, isolate bottlenecks using real measurements, report results in conversation, and write PROFILING-AUDIT.md.

Context:
- Target: {TARGET_NAME_OR_ENDPOINT}
- Repository Path: {REPO_PATH}
- Scope Files: {TARGET_FILES}

Strict Rules:
1. Do not modify the audited application code.
2. Locate target source code and record exact file paths and line numbers.
3. Formulate a minimal, repeatable invocation driver for the target.
4. Profile the target:
   - Run 1 warm-up iteration (discard timing).
   - Run 5 measured iterations strictly sequentially.
   - Record run durations and calculate n, mean, p50, p95, stdev, cv, min, max.
   - Measure self_ms, total_ms, call count, and CPU vs wait split.
   - Tag provenance as "measured", "simulated", or "estimated".
5. Diagnose root cause into one of the 10 standard categories.
6. Assemble temporary canonical data in .work/, run validate.py, render PROFILING-AUDIT.md with render_markdown.py --mode targeted, and delete .work/.
7. Do not create HTML files or full audit markdown files. Output exactly PROFILING-AUDIT.md.

Output Format:
Provide a concise conversation summary containing:
- Target Name and Location
- Measured p50, p95, and mean duration (ms)
- CPU vs. Wait time breakdown
- Root Cause Category and explanation
- Recommended Fix with prerequisites and labelled projected impact
- Path to PROFILING-AUDIT.md
```
