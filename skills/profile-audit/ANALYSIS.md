# Phase 4: Root-Cause Analysis

## Purpose
Phase 4 conducts detailed root-cause investigation for every hot unit identified during Phase 3 measurement (and high-impact shared warm units). It inspects source code, traces execution paths, decomposes execution time into active CPU compute versus waiting time, classifies root causes into one of 10 standardized categories, formulates actionable remediation plans with explicit prerequisites and projected impacts, merges duplicate root causes across units, and produces provisional findings.

## Inputs
1. `.work/coverage-ledger.json`: Unit classifications (`hot`, `warm`, `cold`, `not_exercised`).
2. `.work/measurements.json`: Sequential run timings, summary statistics, hierarchical call trees with provenance, and probe results.
3. `.work/architecture.json`: Discovered components, database tables, indexes, and external services.
4. Target application source code: Read-only access to files and configurations.

---

## Steps

### Step 1: Unit Extraction and Prioritization
1. Read `.work/coverage-ledger.json` and extract all units where `status == "hot"`.
2. Extract any shared `warm` units that appear in multiple workflows whose aggregate impact exceeds 10% of workflow duration.
3. Sort candidate units descending by measured impact duration.
4. Assign candidate finding IDs: `find-001`, `find-002`, etc.

### Step 2: Agent Dispatch
- If the host environment supports parallel sub-agents, start one analysis sub-agent per hot unit concurrently using the copy-ready prompt template below.
- If sub-agents are unsupported, execute analysis sequentially for each hot unit in descending impact order.

### Step 3: Source Code Inspection and Call Path Tracing
1. Open the file and line specified in the unit record (`file:line`).
2. Trace the call tree downward using `.work/measurements.json`.
3. Identify the specific lines of code that consume the majority of `self_ms` or invoke expensive downstream children.
4. Verify whether the time is spent in internal computation or external operations.

### Step 4: Timing Decomposition (CPU vs. Wait)
Evaluate the ratio of `cpu_ms` to `wait_ms` from `.work/measurements.json`:
- If `cpu_ms / total_ms >= 0.70`: The unit is compute-bound (CPU saturation, inefficient algorithms, cryptographic hashing, heavy serialization).
- If `wait_ms / total_ms >= 0.70`: The unit is wait-bound (database query latency, network I/O, synchronous socket wait, disk I/O, thread lock acquisition).
- If neither dominates: Inspect the interleaved call tree to pinpoint mixed compute and I/O phases.

### Step 5: Standardized Root-Cause Categorization
Classify the bottleneck into EXACTLY ONE of the following 10 standardized categories:

| Category Enum | Definition & Primary Indicators | Remediation Archetype |
|---|---|---|
| `expensive_compute` | High CPU consumption caused by CPU-heavy processing such as excessive iteration counts in cryptographic hashing, unoptimized compression, heavy regex backtracking, or costly serialization. | Reduce iterations, vectorize operations, use faster native primitives, or offload compute to background workers. |
| `n_plus_one_query` | Database query executed iteratively inside a loop for each item in a collection instead of issuing a single batched query or relational JOIN. | Use eager loading, joined queries, prefetching, or batched IN-queries. |
| `missing_index` | Database query performing a full table scan or unindexed sequential filter on large tables, indicated by high query wait times on filtered or sorted columns. | Add B-tree or appropriate index on filtered, sorted, or foreign key columns. |
| `blocking_io` | Synchronous blocking I/O (file read/write, sync socket, sync sleep) executed inside an asynchronous event loop or request-handling thread. | Convert to asynchronous non-blocking I/O or offload to a dedicated worker thread pool. |
| `missing_timeout` | Outbound network HTTP call, RPC, or external API invocation lacking an explicit connection or read timeout, leading to thread stalls. | Configure explicit connection and read timeouts with structured retry/backoff policies. |
| `unbounded_query` | SQL query or database retrieval lacking a `LIMIT` clause or pagination, retrieving entire tables or unbounded record sets into memory. | Add pagination, `LIMIT`/`OFFSET`, or streaming cursor queries. |
| `unpooled_resource` | Opening a new database connection, HTTP connection, or process per request instead of acquiring from a persistent connection pool. | Implement persistent connection pooling (e.g. database pool, keep-alive HTTP session). |
| `heavy_import` | Large modules, data models, or machine learning libraries imported dynamically at request time rather than during application initialization. | Hoist imports to module level or initialize heavy singletons during application startup. |
| `redundant_io` | Reading identical static files, executing identical unchanged queries, or re-fetching remote assets multiple times during a single workflow. | Introduce in-memory caching, request-scoped memoization, or local buffer reuse. |
| `inefficient_algorithm` | High algorithmic time complexity (e.g. $O(N^2)$ or $O(2^N)$ nested loops, sorting, or linear lookups where $O(N)$ or $O(1)$ set/hash lookups apply). | Refactor algorithm to optimal complexity using hash tables, sets, or divide-and-conquer logic. |

### Step 5b: Severity Rubric
Assign severity strictly based on measured latency and workflow share:
- **`critical`**: `workflow_share_pct >= 50.0%` **and** `owned_ms >= 100.0 ms`.
- **`high`**: `workflow_share_pct >= 25.0%` **and** `owned_ms >= 25.0 ms`, OR a scale probe projecting `>= 100.0 ms` at 10× data volume.
- **`medium`**: `workflow_share_pct >= 10.0%` **or** `owned_ms >= 10.0 ms`.
- **`low`**: Otherwise.

### Step 5c: Hard Rule — Never Weaken Security or Cryptographic Parameters
- **NEVER recommend lowering cryptographic cost or security parameters** (password-hash iterations/rounds, key lengths, salt sizes, TLS versions/ciphers) to gain performance.
- Remediate CPU-bound cryptographic operations by offloading: run hashing in a worker thread/process pool, background job queue, or scale CPU capacity.
- If the audited application uses iterations or key sizes below current industry guidance (e.g. PBKDF2 iterations below OWASP recommendations), flag it as a separate **security note**, not a performance optimization.
- Any fix recommending reducing, lowering, decreasing, or calibrating down iterations/cost/rounds/work factors will be rejected by validator `E_FIX_WEAKENS_SECURITY`.

### Step 6: Root Cause Merging (Avoiding Duplicates)
- Do NOT emit duplicate findings for the same underlying bottleneck.
- If multiple code units are impacted by the same root cause (for example, three distinct endpoint functions execute the same slow unindexed query on the `users` table), emit **one single finding** whose `unit_ids` array references all affected units: `["unit-001", "unit-004", "unit-007"]`.

### Step 7: Evidence Gathering & Provenance Rules
Every finding must cite concrete, verifiable evidence:
1. **Call tree citation**: Cite exact call tree node name, `total_ms`, `self_ms`, and share of workflow time.
2. **Code citation**: Cite exact file path, line number, and code snippet demonstrating the bottleneck.
3. **Provenance Rule**: Record `provenance` (`"measured"`, `"simulated"`, or `"estimated"`).
   - If the headline metric derives from simulated or estimated call tree nodes, the finding status can NEVER be `"verified"`. It MUST be marked `"needs_validation"`.
4. **Complexity Claims**: Any claim of $O(N^2)$ or algorithmic inefficiency must cite scale probe evidence with measured slope or varying input sizes.
5. **Concurrency Claims**: Any claim of lock contention or pool exhaustion must cite concurrency probe evidence comparing sequential vs. concurrent runs.

### Step 8: Structured Remediation Plan (Fix Object)
Every finding must contain a structured `fix` object:
1. `title`: Concise title of the proposed fix.
2. `explanation`: Technical description explaining how the fix resolves the root cause.
3. `steps`: Ordered list of concrete, step-by-step implementation actions citing exact files and line numbers from line-level profiles.
4. `prerequisites`: Required system changes before applying the fix. Must list every migration, data backfill, or deployment dependency mentioned in risks (e.g. schema migration, backfilling existing hashes, rehash on next login, rollout schedule).
5. `risks`: Implementation or operational risks associated with the fix.
6. `projection`: Quantitative projection of latency improvement. Must include `projection.basis` showing the arithmetic from measured numbers (e.g. "removes 149.6 of 180.5 ms = 82.9% if fully offloaded"). Bare percentage claims without basis are rejected by `E_UNSUPPORTED_CLAIM`.
7. `validation`: Concrete procedure to verify the fix after implementation.

### Step 9: Assembly of Provisional Findings
Write `.work/findings.json` conforming to `schemas/findings.schema.json`.
- Set initial `status: "needs_validation"` for all findings (provisional findings remain `needs_validation` until Phase 5 adversarial verification).
- Metrics: `owned_ms`, `total_ms`, `workflow_share_pct` (owned / own workflow total), `audit_share_pct`, `calls`, `p50_ms`, `p95_ms`, `cv`, `cpu_ms`, `wait_ms`.
- Enforce metric consistency with classifier unit metrics within `max(0.01 ms, 0.5%)`.
- Enforce arithmetic consistency: `| (cpu_ms + wait_ms) - total_ms | <= max(0.02 * total_ms, 0.5)`.

---

## Sub-Agent Prompt Template: Per-Hot-Unit Analysis
```text
You are a specialized performance analysis sub-agent for profile-audit.
Role: Conduct a detailed root-cause investigation for hot unit `{UNIT_ID}` (`{UNIT_NAME}`).

Inputs:
- UNIT_ID: `{UNIT_ID}`
- UNIT_NAME: `{UNIT_NAME}`
- LOCATION: `{FILE}:{LINE}`
- WORKFLOWS: `{WORKFLOW_NAMES}`
- MEASUREMENTS:
  * owned_ms: {OWNED_MS}
  * total_ms: {TOTAL_MS}
  * workflow_share_pct: {WORKFLOW_SHARE_PCT}
  * audit_share_pct: {AUDIT_SHARE_PCT}
  * calls: {CALLS}
  * cpu_ms: {CPU_MS}
  * wait_ms: {WAIT_MS}
  * provenance: {PROVENANCE}
- CALL_TREE_SNIPPET:
{CALL_TREE_SNIPPET}

Strict Rules:
1. Read the source code at `{FILE}:{LINE}`. Do not modify any files.
2. Determine whether the bottleneck is compute-bound (`cpu_ms`) or wait-bound (`wait_ms`).
3. Classify into EXACTLY ONE of the 10 standard root-cause categories:
   expensive_compute, n_plus_one_query, missing_index, blocking_io, missing_timeout,
   unbounded_query, unpooled_resource, heavy_import, redundant_io, inefficient_algorithm.
4. If provenance is "simulated" or "estimated", status MUST be "needs_validation".
5. If claiming an algorithmic bottleneck, cite scale probe measurements.
6. If claiming a concurrency bottleneck, cite concurrency probe measurements.
7. Provide a structured fix plan with explicit prerequisites, line-level steps, risks, and projected impact with arithmetic basis. Never recommend reducing cryptographic work factors or iterations.

Output Format:
Return a JSON object conforming to the finding item in schemas/findings.schema.json:
{
  "id": "find-001",
  "title": "Unindexed full table scan on users table during authentication",
  "unit_ids": ["{UNIT_ID}"],
  "workflows": ["{WORKFLOW_NAMES}"],
  "impact": "critical",
  "category": "missing_index",
  "status": "needs_validation",
  "provenance": "{PROVENANCE}",
  "root_cause": "SELECT query in authenticate_user filters by email without index, scanning all records",
  "evidence": [
    "Call tree node db_execute total_ms: 120.5ms (82% of workflow duration)",
    "src/db/auth.py:42: query executes SELECT * FROM users WHERE email = ?"
  ],
  "metrics": {
    "total_ms": {TOTAL_MS},
    "self_ms": {SELF_MS},
    "calls": {CALLS},
    "share_of_workflow_pct": {SHARE_PCT},
    "cpu_ms": {CPU_MS},
    "wait_ms": {WAIT_MS}
  },
  "fix": {
    "title": "Add index on users(email)",
    "explanation": "Create a unique index on the email column to replace sequential scans with index lookups",
    "steps": [
      "Create database migration adding CREATE INDEX idx_users_email ON users(email);",
      "Deploy migration before rolling out application changes"
    ],
    "prerequisites": ["Database migration permission on users table"],
    "projected_impact": "Projected: Reduces query duration from ~120ms to <1ms"
  }
}

Error Handling & Stop Condition:
If the source file cannot be read, report the file access error and recommend manual code inspection. Stop when finding analysis is complete.
```
