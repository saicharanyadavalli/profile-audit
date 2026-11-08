# Auxiliary Instruction: Cloud and Remote Target Profiling

## Purpose
This document provides specific instructions, safety protocols, and measurement methodologies for profiling remote or deployed environments (staging instances, preview deployments, cloud services, and shared database targets).

## Inputs
1. **Target URL and Endpoints**: Explicit remote URL, HTTP protocol, port, and specific routes or query endpoints to benchmark.
2. **Target Environment Classification**: Explicit user specification of whether the target is a remote web deployment (`deployed`) or a local service connected to a shared remote database (`shared_db`).
3. **Authentication & Request Headers**: Non-destructive test credentials, bearer tokens, or custom request headers needed to exercise target endpoints.
4. **Cloud Observability Access (Optional)**: Read-only access credentials or endpoints for cloud telemetry, distributed traces, and log metrics if available.

---

## Core Principles & Constraints

### 1. Explicit User Opt-In Only
- The skill is **local by default**. It runs against local code and local databases on the user's workstation.
- It switches to a deployed or cloud target **ONLY** when the user explicitly requests remote profiling (e.g., "profile our staging API at https://staging.example.corp", "find bottlenecks on the deployed preview server").
- Never escalate or redirect to a remote target implicitly.

### 2. Outside-In Measurement Discipline
- When profiling a remote or cloud target, the skill operates **outside-in**:
  * Measures network transit, DNS lookup, TCP connection, TLS handshake, Time-To-First-Byte (TTFB), and response transfer duration.
  * Inspects HTTP response headers (such as `Server-Timing`).
  * Ingests external cloud telemetry (cloud metrics, distributed traces, and log metrics) if credentials are provided.
- **Outside-in profiling CANNOT capture internal function-level call trees or line-level CPU/wait splits without an in-process profiler running inside the target runtime.**

### 3. Mandatory Local-Run Disclosure
Whenever cloud profiling is invoked, the agent MUST immediately output the following standardized disclosure notice in conversation and include it at the top of all generated reports:

> **Important Disclosure on Cloud Profiling**:  
> Outside-in cloud profiling measures external HTTP latency, network transit, gateway timings, and available remote telemetry, but cannot provide in-process function call trees or line-by-line CPU vs. Wait profiling. For deep function-level call trees, code line attribution, and root-cause analysis, run `/profile-audit` locally on the codebase.

### 4. Non-Destructive, Read-Only Safety Protocol
When profiling shared staging environments or deployed systems:
- Strictly prohibit data-mutating requests (POST, PUT, PATCH, DELETE) that alter persistent shared state unless targeting isolated synthetic test records.
- Use idempotent, read-only GET/HEAD requests or synthetic test credentials.
- Output one short, non-blocking safety notice before executing traffic against remote targets:
  `Notice: Executing read-only performance benchmarks against remote target {TARGET_URL}.`
- Never execute destructive reset scripts or drop tables on shared databases.

### 5. Metadata Tagging
In `.work/run-metadata.json`, record:
- `target_type`: `"deployed"` (for remote HTTP/cloud endpoints) or `"shared_db"` (for local services connected to a shared remote database).
- Note target environment details in `environment` or `scope_paths`.

---

## Steps

### Step 1: User Request Verification & Scope Lockdown
1. Confirm the user explicitly requested a remote or deployed target.
2. Extract target URL, protocol, authentication headers, and specific endpoints to measure.
3. Issue the mandatory local-run disclosure notice in conversation.

### Step 2: Safety & Non-Destructive Verification
1. Inspect the target endpoint definitions.
2. Verify that benchmarking workflows use synthetic queries or read-only operations.
3. Output the non-blocking safety notice before running traffic drivers.

### Step 3: Outside-In Latency Measurement
1. Execute sequential benchmark requests:
   - 1 warm-up request (discarded).
   - 5 measured requests executed sequentially.
2. For each request, capture detailed timing splits:
   - DNS Lookup Duration (`dns_ms`)
   - TCP Connection Duration (`tcp_ms`)
   - TLS Handshake Duration (`tls_ms`)
   - Time-To-First-Byte (`ttfb_ms` = server processing + initial transit)
   - Content Transfer Duration (`transfer_ms`)
   - Total HTTP Duration (`total_ms`)
3. Compute summary statistics across the 5 measured runs: `n`, `mean`, `p50`, `p95`, `stdev`, `cv`, `min`, `max`.

### Step 4: Header & Telemetry Extraction
1. **`Server-Timing` Inspection**:
   - Parse `Server-Timing` headers returned by the server (e.g. `Server-Timing: db;dur=45.2, render;dur=12.1`).
   - Extract component durations if published by the upstream application.
2. **Gateway & Cache Headers**:
   - Inspect cache status headers (`Age`, `X-Cache`, `Cache-Control`).
   - Note whether slow responses correlate with cache misses or cold starts.
3. **Cloud Observability Integration (Optional)**:
   - If read-only access to monitoring tools is provided, query average span durations and latency percentiles for the audited endpoints.
   - Correlate external TTFB with trace spans.

### Step 5: Recording Findings and Artifacts
1. Populate `.work/measurements.json`:
   - Create call tree nodes representing network phases (`dns`, `tcp`, `tls`, `ttfb`, `transfer`) and any extracted `Server-Timing` metrics.
   - Mark network transit nodes as `kind: "wait"` and `source: "measured"`.
2. Populate `.work/findings.json`:
   - If external TTFB is high (>500ms), record an outside-in latency finding with `status: "needs_validation"`.
   - Reiterate in the finding description that line-level root-cause confirmation requires local profiling.
3. Compile deliverables (`PROFILING-DASHBOARD.html`, `FINDINGS.md`, `FIXES.md`) with `target_type: "deployed"` and the prominent mandatory local-run disclosure.

---

## Sub-Agent Prompt Template: Cloud Target Profiling
```text
You are a cloud target profiling sub-agent for profile-audit.
Role: Perform outside-in performance benchmarking on deployed target `{TARGET_URL}`.

Inputs:
- TARGET_URL: `{TARGET_URL}`
- ENDPOINTS: `{ENDPOINTS}`
- TARGET_TYPE: `deployed` | `shared_db`

Strict Rules:
1. Issue the mandatory cloud profiling disclosure notice.
2. Perform only read-only or synthetic operations; never mutate shared data.
3. Execute 1 warm-up and 5 sequential measured requests per endpoint.
4. Record outside-in timings: DNS, TCP, TLS, TTFB, Transfer, and Total HTTP duration.
5. Do not fabricate internal function call trees. Outside-in data covers HTTP transit and Server-Timing only.

Output Format:
Return a JSON object containing outside-in measurements:
{
  "target_url": "{TARGET_URL}",
  "target_type": "deployed",
  "disclosure_issued": true,
  "endpoints": [
    {
      "endpoint": "/api/v1/health",
      "p50_ms": 125.4,
      "p95_ms": 142.1,
      "ttfb_p50_ms": 110.2,
      "server_timing": {"db": 45.2, "app": 65.0}
    }
  ]
}

Error Handling & Stop Condition:
If the remote target returns HTTP 401/403, check authentication headers. If unreachable, report network connection error. Stop when all endpoints are benchmarked.
```
