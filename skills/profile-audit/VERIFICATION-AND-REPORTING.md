# Phases 5 & 6: Verification and Reporting

## Purpose
Phases 5 and 6 provide adversarial validation and report compilation. In Phase 5, an independent verification agent evaluates findings, checks variance and tree arithmetic, verifies causal links, and promotes confirmed findings from `needs_validation` to `verified`. In Phase 6, the agent runs validation, compiles the single-file interactive dashboard (`PROFILING-DASHBOARD.html`) and two markdown reports (`FINDINGS.md` and `FIXES.md`), verifies consistency, and removes intermediate working files.

## Inputs
1. Provisional `.work/findings.json` from Phase 4.
2. `.work/measurements.json`: Benchmark timings, call trees, and variance statistics.
3. `.work/coverage-ledger.json`: Complete classification status of all units.
4. `.work/run-metadata.json`: Machine metadata, profiler method, and run counts.
5. `.work/architecture.json`: Discovered topology and data layer schema.
6. Helper scripts: `validate.py`, `build_report.py`, `render_markdown.py`.
7. Template: `assets/report-template.html`.

---

## Steps

### Phase 5: Adversarial Verification

#### Step 1: Independent Verifier Allocation
- Assign findings to an independent verifier agent that did not draft the finding during Phase 4.

#### Step 2: Adversarial Disproving Tests
The verifier must actively attempt to disprove the finding across five checks:

1. **Variance & Stability Check**:
   - Inspect `cv` (coefficient of variation) for the workflow and function.
   - If $CV > 0.30$: Flag the measurement as noisy. Check if latency was caused by transient host load rather than application code.
   - Run an automatic re-measurement round (5 additional runs). If latency cannot be reproduced consistently, the finding must NOT be marked `verified`.

2. **Causal Link Check**:
   - Inspect the code at `location.file:location.line`.
   - Verify whether the code at that location is the true causal driver of the measured duration or merely an innocent wrapper around a slow downstream dependency.
   - Ensure the bottleneck is attributed to the real leaf operation (e.g. database query), not an outer wrapper.

3. **Mathematical Conservation & Tree Arithmetic**:
   - Enforce finding conservation: $|(\text{cpu\_ms} + \text{wait\_ms}) - \text{total\_ms}| \le \max(0.02 \times \text{total\_ms}, 0.01)$.
   - Verify that `share_of_workflow_pct` is bounded within $0.0 \le \text{share} \le 100.0$ using the unit's own workflow duration as denominator.
   - Verify call tree arithmetic: sequential children sum up to total within tolerance; concurrent children do not exceed active interval union.

4. **Provenance & Evidence Grounding Check**:
   - Check the finding's `provenance`:
     * If provenance is `"simulated"` or `"estimated"`, the finding CANNOT be `"verified"`. It must remain `"needs_validation"`.
   - Verify that `evidence[]` contains non-empty, concrete citations to call tree nodes in `measurements.json` and actual code lines.

5. **Top Cost & Coverage Completeness Check**:
   - Inspect the call trees in `.work/measurements.json`. Identify the top leaf nodes by duration.
   - Ensure every leaf node consuming $\ge 10\%$ of workflow duration is represented in findings or has an explicit explanation in `coverage-ledger.json`.

6. **Metric & Classifier Consistency Check**:
   - Enforce that finding metrics (`owned_ms`, `total_ms`, `workflow_share_pct`, `audit_share_pct`, `calls`, `p50_ms`, `p95_ms`, `cv`, `cpu_ms`, `wait_ms`) match the unit's classifier metrics within tolerance `max(0.01 ms, 0.5%)` (`E_FINDING_METRIC_MISMATCH`).

7. **Severity Rubric & Security Safety Check**:
   - Verify severity matches the deterministic rubric: `critical` (>= 50% & >= 100 ms), `high` (>= 25% & >= 25 ms, or scale probe >= 100 ms at 10x), `medium` (>= 10% or >= 10 ms), `low` otherwise (`E_SEVERITY_MISMATCH`).
   - Reject any fix that recommends lowering cryptographic cost, rounds, work factor, or iterations (`E_FIX_WEAKENS_SECURITY`).
   - Reject fix text with bare percentage claims lacking mathematical basis (`E_UNSUPPORTED_CLAIM`).

8. **Line-Level Profile & Prerequisite Check**:
   - If line-level profiles are present, verify that `sum(lines.total_ms) + residual_ms` equals unit `owned_ms` within `max(1.0 ms, 5%)` (`E_LINE_SUM`).
   - Verify line code text matches actual source code lines (`E_LINE_CODE_MISMATCH`).
   - In strict mode, verify every `hot` unit contains a line profile or explicit `unavailable` reason (`E_HOT_NO_LINE_PROFILE`).
   - Verify every schema change, migration, or backfill risk is declared in fix prerequisites (`E_FIX_PREREQ`).

#### Step 3: Status Determination & Ledger Alignment
- If all checks pass and the finding is undeniable:
  * Set `status: "verified"`.
- If any check fails, variance is excessive, or evidence relies on simulated nodes:
  * Set `status: "needs_validation"`.
  * Record the exact reason or missing measurement in the finding.
- Save the updated `.work/findings.json`.

---

### Phase 6: Reporting and Artifact Compilation

Full Audit Mode delivers **exactly three final files** in the run directory:
1. `PROFILING-DASHBOARD.html`
2. `FINDINGS.md`
3. `FIXES.md`

No other user-facing files (`architecture.md`, `COVERAGE.md`, `NEEDS-VALIDATION.md`, or loose JSON files) may remain.

#### Step 4: Validate Intermediate Data
Run the strict validator against the working directory:
```bash
python skills/profile-audit/scripts/validate.py <run-dir>/.work --strict
```
If any error is reported, resolve the discrepancy in `.work/` before proceeding.

#### Step 5: Build Interactive HTML Dashboard
Compile the self-contained dashboard:
```bash
python skills/profile-audit/scripts/build_report.py <run-dir>/.work --out <run-dir>/PROFILING-DASHBOARD.html
```
- `build_report.py` embeds all canonical JSON data into `<script id="report-data" type="application/json">` inside the HTML file.
- The dashboard is the durable, self-contained carrier of all audit data.

#### Step 6: Render Standard Markdown Deliverables
Generate `FINDINGS.md` and `FIXES.md` from the dashboard:
```bash
python skills/profile-audit/scripts/render_markdown.py --mode full --data <run-dir>/PROFILING-DASHBOARD.html --out-dir <run-dir>
```
- `FINDINGS.md` contains: executive summary, methodology and host machine details, verified bottlenecks, needs-validation findings, static code observations, code unit coverage breakdown, unexercised units, and measurement limitations.
- `FIXES.md` contains: verified-finding remediation plans first, followed by provisional recommendations for unverified findings with explicit prerequisites and labelled projected impacts.

#### Step 7: Dual Validation & Drift Prevention
Run both validators against the generated files:
```bash
# 1. Verify dashboard embedded data integrity
python skills/profile-audit/scripts/validate.py <run-dir>/PROFILING-DASHBOARD.html --strict

# 2. Verify markdown reports strictly match dashboard data with zero drift
python skills/profile-audit/scripts/render_markdown.py --mode full --check --data <run-dir>/PROFILING-DASHBOARD.html --out-dir <run-dir>
```

#### Step 8: Working Directory Cleanup
- Confirm that both validation commands exited with code 0.
- Verify that `PROFILING-DASHBOARD.html`, `FINDINGS.md`, and `FIXES.md` exist and are non-empty.
- **Delete the `.work/` directory**:
  ```bash
  rm -rf <run-dir>/.work
  ```
- If validation fails at any point, do NOT delete `.work/`; retain it and report the validation error.

---

## Sub-Agent Prompt Templates

### Prompt 1: Adversarial Verification Sub-Agent
```text
You are an adversarial verification sub-agent for profile-audit.
Role: Independently review and verify or disprove candidate finding `{FINDING_ID}`.

Inputs:
- FINDING: Complete candidate finding object.
- MEASUREMENTS_PATH: Path to .work/measurements.json.
- LEDGER_PATH: Path to .work/coverage-ledger.json.
- SOURCE_CODE: Read-only access to repository files.

Strict Rules:
1. Actively attempt to disprove the finding hypothesis.
2. Check variance: if CV > 0.30, flag as unstable.
3. Check causal link: verify the code at file:line is the real bottleneck, not an innocent wrapper.
4. Check conservation: | (cpu_ms + wait_ms) - total_ms | <= max(0.02 * total_ms, 0.01).
5. Check provenance: if provenance is "simulated" or "estimated", status MUST be "needs_validation".
6. Check prerequisites: if fix involves adding an index, verify the table exists in the schema.

Output Format:
Return a verification assessment:
{
  "finding_id": "{FINDING_ID}",
  "decision": "verified" | "needs_validation",
  "reason": "Detailed explanation of verification decision",
  "checks": {
    "variance_stable": true,
    "causal_link_verified": true,
    "timing_conserved": true,
    "provenance_measured": true,
    "prerequisites_valid": true
  }
}

Error Handling & Stop Condition:
Stop when all checks are evaluated.
```

### Prompt 2: Report Compilation & Validation Sub-Agent
```text
You are a reporting compilation sub-agent for profile-audit.
Role: Compile the interactive dashboard, generate markdown reports, validate outputs, and clean up working data.

Inputs:
- RUN_DIR: Output directory containing .work/.

Strict Rules:
1. Run validate.py --strict on .work/ first.
2. Compile PROFILING-DASHBOARD.html with build_report.py.
3. Generate FINDINGS.md and FIXES.md with render_markdown.py.
4. Validate PROFILING-DASHBOARD.html with validate.py --strict.
5. Check markdown consistency with render_markdown.py --check.
6. Delete .work/ ONLY if all checks succeed. Never delete .work/ if a check fails.

Output Format:
Return a report status summary:
{
  "status": "success" | "failure",
  "files_created": [
    "PROFILING-DASHBOARD.html",
    "FINDINGS.md",
    "FIXES.md"
  ],
  "work_dir_cleaned": true,
  "validation_passed": true
}

Error Handling & Stop Condition:
If any validator fails, halt immediately, leave .work/ intact, and return the validator error log.
```
