# Phases 2 & 3: Drivers and Deterministic Measurement

## Purpose
This specification covers Phase 2 (Driver Authoring) and Phase 3 (Deterministic Measurement) of a full performance audit.

- **Phase 2 (Drivers)** constructs repeatable, automated traffic drivers for every workflow identified during Reconnaissance. It seeds realistic data volumes so query planners, memory allocations, and network buffers reflect production behavior.
- **Phase 3 (Measurement)** executes drivers strictly sequentially using stack-appropriate profiling tools. It captures hierarchical call trees, separates active CPU time from I/O waiting time, calculates latency distribution statistics (`n`, `mean`, `p50`, `p95`, `stdev`, `cv`, `min`, `max`), records `.work/measurements.json`, and updates every unit in `.work/coverage-ledger.json` to `hot`, `warm`, `cold`, or `not_exercised` using `classify.py`.

## Inputs
1. `.work/architecture.json`: Discovered components, workflows, and data stores from Phase 1.
2. `.work/coverage-ledger.json`: Unit inventory from Phase 1 with units marked `pending`.
3. Target application source and runtime environment.

---

## Steps

### Phase 2: Driver Authoring & Realistic Seeding

#### Step 1: Workflow Identification and Grouping
- Group cataloged entry points into distinct, representative workflows:
  * Authentication workflows (e.g., `user_signup`, `user_login`).
  * Read and search workflows (e.g., `item_listing`, `product_search`, `dashboard_view`).
  * Mutation and write workflows (e.g., `checkout_order`, `submit_review`, `update_profile`).
  * Background workflows (e.g., `process_batch_queue`, `nightly_cleanup`).

#### Step 2: Realistic Data Seeding
- Seed realistic data volumes rather than trivial test stubs:
  * Do NOT test against an empty database or a table with 5 rows. Trivial data masks missing indexes, table scans, and N+1 query patterns.
  * Seed hundreds or thousands of rows (e.g., 100+ users, 50+ products, 1,000+ reviews/orders, 10,000+ log events).
  * Ensure foreign key relationships, joined tables, and filtered fields contain diverse values.
  * Record seeded table counts in `.work/run-metadata.json` under `data_volume`.

#### Step 3: Driver Script Authoring
- Author dedicated, repeatable driver scripts for each workflow.
- Requirements for drivers:
  * Self-contained and runnable via standard CLI commands or scripting languages.
  * Idempotent or self-cleaning: successive executions must not fail due to unique constraint violations (e.g., use randomized/incrementing emails for signup or reset state cleanly).
  * Realistic request payloads containing valid parameters, headers, and query parameters.
- **Safety Safeguard**: Before running write-heavy workflows, output one short, non-blocking notice:
  > "Notice: Driver workflow will execute database write operations against the configured target database."

---

### Phase 3: Deterministic Profiling and Measurement

#### Step 4: Profiling Tool Selection Hierarchy
Select the profiling approach for the target technology stack using this priority order:
1. **Priority 1: Native Sampling Profiler**
   - Python: standard library `cProfile` or sampling tools.
   - Go: Native `pprof` (`go tool pprof`).
   - Node.js: Native V8 CPU profiler (`--prof`, `--cpu-prof`, or inspector).
   - Java / JVM: `async-profiler` or standard JVM profiling hooks.
   - Rust / C / C++: `perf` or sampling profilers.
2. **Priority 2: Built-in Runtime Instrumentation / Request Hooks**
   - Web framework middleware, event hooks, or telemetry interceptors that record per-function entry/exit timestamps.
3. **Priority 3: High-Resolution Monotonic Timers**
   - High-resolution monotonic timers wrapping functions and external I/O boundaries (`time.perf_counter_ns()`, `process.hrtime.bigint()`, `System.nanoTime()`).

#### Step 5: CPU vs. Wait Time Separation & Measurement Protocol
Every measured function and call tree node must distinguish active CPU processing from waiting:
- **`cpu_ms`**: Time spent actively computing on a CPU core (cryptography, hashing, serialization, data sorting, algorithm execution).
- **`wait_ms`**: Time spent blocked or sleeping (waiting for database socket reads, network API responses, disk file I/O, lock acquisition).
- **Measurement Protocol**: Around every instrumented span record wall time and CPU time:
  `wait_ms = max(0.0, wall_ms - cpu_ms)`

| Runtime | CPU-time clock to read | Wall-time clock |
|---|---|---|
| Python | `time.thread_time_ns()` (thread) or `time.process_time_ns()` (process) | `time.perf_counter_ns()` |
| Node.js | `process.cpuUsage()` deltas (process-wide, valid because runs are sequential) | `process.hrtime.bigint()` |
| Java/Kotlin | `ThreadMXBean.getCurrentThreadCpuTime()` | `System.nanoTime()` |
| Go | CPU profile (pprof) on-CPU samples | Wall time of the span |
| .NET | `Process.TotalProcessorTime` (or per-thread) | `Stopwatch` |
| Ruby | `Process.clock_gettime(Process::CLOCK_THREAD_CPUTIME_ID)` | `CLOCK_MONOTONIC` |
| PHP | `getrusage()` deltas | `hrtime(true)` |
| C/C++/Rust | `clock_gettime(CLOCK_THREAD_CPUTIME_ID)` | `CLOCK_MONOTONIC` |

- **Verification**: The agent verifies availability by running a one-line probe before relying on a CPU clock. If no CPU clock is available: set `cpu_ms` and `wait_ms` to `null` and `cpu_wait_source` to `"unavailable"`. **Never assume ratios.**
- **Call Tree Rollup**: Each node records its own values `self_cpu_ms` and `self_wait_ms` plus `cpu_wait_source` (`measured_thread_cpu`, `measured_process_cpu`, `profiler_oncpu`, or `unavailable`). Inclusive `cpu_ms` and `wait_ms` of every parent are **computed, not typed**: use `measurements_tools.py rollup` to roll them up from the leaves before validation.
- **Derived Kind**: `kind` is derived: `"cpu"` if `cpu_ms >= 0.70 * total_ms`, `"wait"` if `wait_ms >= 0.70 * total_ms`, otherwise `"mixed"`. Flame graph and waterfall visualizations blend by real CPU/wait share.
- **Conservation of Timing**: At every node, verify `| (cpu_ms + wait_ms) - total_ms | <= max(0.02 * total_ms, 0.5)`. For every inner node, `cpu_ms` must equal `self_cpu_ms + sum(children.cpu_ms)` (tolerance `max(0.5 ms, 2%)`), same for wait.

#### Step 6: Strictly Sequential Benchmark Execution Protocol
- **Sequential Mandate**: Measurement runs must be executed **strictly sequentially**.
  * Never run multiple workflows at the same time.
  * Never run multiple iterations of the same workflow concurrently.
  * Concurrent runs compete for CPU caches and I/O bandwidth, corrupting timing data.
- **Warm-Up Run**:
  * Execute exactly **1 warm-up iteration** for the workflow.
  * Discard all warm-up timing data.
  * Purpose: Prime database connection pools, file caches, and runtime JIT optimizations.
- **Measured Runs**:
  * Execute strictly **5 measured repetitions** sequentially.
  * Record the total elapsed wall-clock duration in milliseconds for each run: `runs: [r1, r2, r3, r4, r5]`.

#### Step 7: Summary Statistics and Variance Calculation
Compute summary statistics across the 5 measured runs for each workflow using `measurements_tools.py`:
- `n`: Number of measured runs (typically 5).
- `mean`: Arithmetic average duration (`sum(runs) / n`).
- `p50`: Median duration in milliseconds (linear interpolation).
- `p95`: 95th percentile duration in milliseconds.
- `stdev`: Sample standard deviation.
- `cv`: Coefficient of variation (`stdev / mean`).
  * If `cv > 0.30`, flag the workflow as exhibiting high timing variance (suggesting host noise, GC pauses, or external network instability). Run an automatic re-measurement round to confirm stability.
- `min`, `max`: Minimum and maximum observed durations.

#### Step 8: Function Statistics from Function Samples
- If a function node contains `stats`, those statistics must be computed from **the samples of that specific function across runs**, NEVER copied from the workflow's overall duration or stats.
- Workflow statistics describe the end-to-end workflow; function statistics describe only that function's execution.

#### Step 9: Hierarchical Call Trees & Provenance
Assemble a hierarchical call tree representing the execution path of the workflow:
- Root node represents the entry point.
- Child nodes represent functions and I/O operations invoked during execution.
- Node schema requirements:
  * `name`: Descriptive function or operation name.
  * `unit_id`: Matching unit ID from `.work/coverage-ledger.json` (or `null` if framework internal / third-party library).
  * `kind`: Exactly `"cpu"` or `"wait"`.
  * `calls`: Total number of invocations in the workflow.
  * `self_ms`: Self time spent exclusively inside this node (excluding children).
  * `total_ms`: Total cumulative wall-clock time spent in this node.
  * `source`: Required provenance tag: `"measured"`, `"simulated"`, or `"estimated"`. Optional `"source_note"`.
  * `children_execution`: `"sequential"` (default) or `"concurrent"`.
  * `children`: Array of child call tree nodes.

#### Step 10: Tree Arithmetic & Overlapping Async Children
- **Sequential Children (`children_execution: "sequential"`)**:
  `total_ms` must equal `self_ms + sum(child.total_ms)` within tolerance `max(0.5, 0.01 * total_ms)`.
- **Concurrent Children (`children_execution: "concurrent"`)**:
  Do NOT sum child durations. When async tasks run concurrently, their wall-clock contribution is the **union of their active intervals**.
  Ensure: `max(child.total_ms) <= total_ms <= self_ms + sum(child.total_ms)`.

#### Step 11: Granularity & Leaf Attribution
- Attribute database queries and network calls to the actual leaf operation (e.g. `sqlite3.Cursor.execute(SELECT ...)`, `requests.post(https://...)`), not to a generic outer wrapper.
- **Unattributed Self-Time Rule**: If any non-leaf node has `self_ms > 20%` of its `total_ms` AND `self_ms > 10.0 ms`, instrument its inner operations or explain the unaccounted compute.

#### Step 12: Scale Probes (Input Size Scaling)
When evaluating algorithmic complexity or verifying scaling behavior:
- Run the workflow across at least 3 distinct input scales (e.g., $N=1, 10, 100$ items).
- Measure duration at each scale to calculate scaling slope:
  * Flat duration across scales indicates $O(1)$.
  * Linear slope ($T \propto N$) indicates $O(N)$.
  * Quadratic slope ($T \propto N^2$) indicates $O(N^2)$ or nested iteration.
- Record probe results in finding evidence to substantiate complexity claims.

#### Step 13: Concurrency Probes (Contention Testing)
When evaluating lock contention, connection pool exhaustion, or concurrency bottlenecks:
- Run baseline sequential driver ($C=1$) and record `p50` latency.
- Run concurrent driver ($C=5$ or $C=10$ parallel clients) and record `p50` latency and `wait_ms` ratio.
- Calculate wait ratio shift: if latency increase under concurrency is dominated by `wait_ms` increase (e.g., database pool wait or thread lock wait), concurrency contention is confirmed.
- Any finding claiming a concurrency bottleneck must cite concurrency probe evidence.

#### Step 13b: Phase 3, Pass 2 — Line-Level Profiling Protocol
Pass 1 establishes function-level call tree timings and unit statuses. Pass 2 pinpoints line-level bottlenecks within significant functions:
- **Scope**: Runs **only for units with status `hot`**, plus any unit whose `owned_ms` is at least 25% of its workflow. Capped at 5 units maximum (configurable).
- **Instrumentation Priority**:
  1. **Runtime Native Line Profiling**: Python `sys.settrace` or `sys.monitoring` (Python 3.12+), Node.js `--cpu-prof` (V8 per-line sample ticks), Go `pprof -list` / `-lines`, Java JFR line-level sampling.
  2. **Scratch-Copy Probe Injection**: Copy the target source file to `.work/scratch/`, add statement-level timing probes (reading CPU and monotonic clocks from Step 5) around candidate lines in the scratch copy, and run the driver against the scratch copy. **NEVER modify files in the audited repository.**
- **Execution Rules**: Same sequential rigor: 1 warm-up run discarded, 5 measured runs sequentially. Record tracing overhead honestly in `overhead_note`.
- **Data Capture**: Write to top-level `line_profiles[]` in `.work/measurements.json`:
  - `unit_id`, `workflow_id`, `file`, `method`, `repetitions`, `overhead_note`.
  - `lines[]`: `line` (1-indexed), `code` (trimmed text from source, up to 160 chars), `hits`, `total_ms`, `share_of_unit_pct`, `cpu_ms`, `wait_ms`, `source`.
  - `residual_ms`: Unaccounted or runtime dispatch time within the unit (`| sum(lines.total_ms) + residual_ms - owned_ms | <= max(1.0, 0.05 * owned_ms)`).
  - If unobtainable: `{unit_id, line_profile_status: "unavailable", reason: "..."}`. Never fabricate.

#### Step 14: Ledger Classification via `classify.py`
Run `classify_ledger()` from `skills/profile-audit/scripts/classify.py`:
- **`self_ms`**: Raw node self time (kept for detailed inspection).
- **`owned_ms`**: Unit's `total_ms` minus the `total_ms` of its **nearest descendant nodes that are themselves ledger units**. Non-unit descendants (library calls, DB drivers, HTTP clients, serialization helpers) belong to the nearest unit ancestor. This is the execution time the function is directly responsible for.
- **Recursion / Repetition Handling**: For repeated or recursive nodes of the same unit, count `total_ms` only for the **outermost** occurrence on each path (no double counting), and sum `owned_ms` over all occurrences.
- **Audit Total**: `audit_total_ms` = sum of every measured workflow's root `total_ms` (weighted if `weight` is specified).
- **Audit Share**: `audit_share_pct` = `owned_ms` (summed across workflows) / `audit_total_ms` * 100.
- **Workflow Share**: `workflow_share_pct` = `owned_ms` / `workflow_total_ms` * 100 per workflow.
- **Status Classification** (named constants in `classify.py`):
  - `hot`: `audit_share_pct >= 10.0` **and** `owned_ms >= 5.0 ms`
  - `warm`: `audit_share_pct >= 1.0` **and** `owned_ms >= 1.0 ms`
  - `cold`: otherwise, if the unit executed
  - `not_exercised`: not reached by any measured workflow
  - `insufficient_data`: execution observed but reliable function-level timing could not be obtained (reason required)
- Write updated ledger to `.work/coverage-ledger.json`. Confirm zero units remain `pending`.

---

## Sub-Agent Prompt Templates

### Prompt 1: Workflow Driver Authoring Sub-Agent
```text
You are a workflow driver authoring sub-agent for profile-audit.
Role: Create an automated, repeatable benchmark driver for workflow `{WORKFLOW_NAME}`.

Inputs:
- REPO_PATH: Path to target repository.
- WORKFLOW_NAME: Name of the workflow (e.g. user_signup, list_orders).
- ENTRY_POINT: Route or command to trigger the workflow.
- SEED_DATA: Seed volume requirements.

Strict Rules:
1. Do not modify the audited application source code.
2. The driver must be self-contained, repeatable, and idempotent.
3. Use realistic data payloads (valid emails, existing foreign keys).
4. If the workflow writes to a database, output one non-blocking notice:
   "Notice: Driver workflow will execute database write operations against the configured target database."

Output Format:
Provide a runnable driver script path and instructions on how to invoke it sequentially.

Error Handling & Stop Condition:
If the driver fails on duplicate key errors, use randomized unique keys (e.g. timestamp-based). Stop when the driver runs cleanly 3 consecutive times.
```

### Prompt 2: Sequential Measurement Sub-Agent
```text
You are a deterministic measurement sub-agent for profile-audit.
Role: Execute strictly sequential benchmark runs for workflow `{WORKFLOW_NAME}` and capture call tree timings.

Inputs:
- DRIVER_COMMAND: Command to execute the workflow driver.
- WORKFLOW_NAME: Name of the workflow.
- REPETITIONS: 1 warm-up (discarded), 5 measured runs.

Strict Rules:
1. Execute runs strictly sequentially; never run concurrent iterations.
2. Discard warm-up iteration.
3. Tag every call tree node with required "source": "measured", "simulated", or "estimated".
4. Distinguish active CPU ("self_cpu_ms") from waiting time ("self_wait_ms") using real runtime CPU clocks; record "cpu_wait_source"; run measurements_tools.py rollup.
5. Enforce conservation: | (cpu_ms + wait_ms) - total_ms | <= max(0.02 * total_ms, 0.5).
6. Calculate statistics using sample values of this specific workflow/function: n, mean, p50, p95, stdev, cv, min, max.
7. If cv > 0.30, flag high variance and perform 5 additional runs to verify stability.

Output Format:
Return a JSON object conforming to the workflow entry in schemas/measurements.schema.json:
{
  "id": "wf-001",
  "name": "user_signup",
  "runs": [45.2, 44.8, 46.1, 45.0, 45.5],
  "stats": {
    "n": 5,
    "mean": 45.32,
    "p50": 45.2,
    "p95": 46.0,
    "stdev": 0.52,
    "cv": 0.011,
    "min": 44.8,
    "max": 46.1
  },
  "call_tree": { ... }
}

Error Handling & Stop Condition:
If a run throws a runtime exception, capture the traceback, record zero duration, and halt measurement. Stop when 5 measured runs complete.
```

### Prompt 3: Scale Probe Sub-Agent
```text
You are a scale probe sub-agent for profile-audit.
Role: Measure how execution time scales across varying input sizes N to verify algorithmic complexity.

Inputs:
- DRIVER_COMMAND: Benchmark driver accepting an input size parameter N.
- SCALES: Array of input scales, e.g. [1, 10, 50, 100].

Strict Rules:
1. Run each scale sequentially with 1 warm-up and 3 measured runs.
2. Record p50 duration for each scale.
3. Compute the scaling curve and growth factor.

Output Format:
Return a JSON object:
{
  "unit_id": "unit-003",
  "scales": [
    {"n": 1, "p50_ms": 0.4},
    {"n": 10, "p50_ms": 4.1},
    {"n": 50, "p50_ms": 20.8},
    {"n": 100, "p50_ms": 41.5}
  ],
  "scaling_class": "O(N)",
  "evidence_note": "Duration grows linearly with N (slope ~0.41 ms/item)"
}

Error Handling & Stop Condition:
If large N causes timeout (>30s), record timeout and cap scale testing. Stop when all scales are measured.
```

### Prompt 4: Concurrency Probe Sub-Agent
```text
You are a concurrency probe sub-agent for profile-audit.
Role: Measure latency and wait time under concurrency to verify contention or pooling bottlenecks.

Inputs:
- DRIVER_COMMAND: Benchmark driver supporting concurrent requests.
- CONCURRENCY_LEVELS: Baseline (C=1) and stress level (e.g. C=5 or C=10).

Strict Rules:
1. Measure baseline sequential duration (C=1).
2. Measure concurrent duration (C=5 or C=10) across 10 total requests.
3. Separate total duration increase into CPU increase vs. wait increase.

Output Format:
Return a JSON object:
{
  "workflow": "checkout_order",
  "baseline_c1": {"p50_ms": 50.0, "wait_ms": 30.0, "cpu_ms": 20.0},
  "concurrent_c5": {"p50_ms": 180.0, "wait_ms": 155.0, "cpu_ms": 25.0},
  "contention_confirmed": true,
  "contention_type": "database_connection_pool_wait",
  "evidence_note": "Wait time increased from 30ms to 155ms (5.1x) while CPU remained constant"
}

Error Handling & Stop Condition:
Stop when baseline and concurrent measurements are completed.
```
