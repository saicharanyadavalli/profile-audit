# Performance Profiling Audit Findings

## 1. Executive Summary
- **Repository Target**: `profile-audit-demo` (Revision `v2.1`)
- **Workflows Measured**: 5
- **Total Functions / Units In-Scope**: 11
- **Functions Exercised**: 10 (90.9% coverage)
- **Verified Bottlenecks**: 3
- **Needs Validation Findings**: 1
- **Static Code Observations**: 2

## 2. Methodology and Machine Specification
- **Operating System**: Windows
- **CPU**: 12th Gen Intel(R) Core(TM) i5-1235U (12 logical, 10 physical cores)
- **Memory**: 15.71 GB RAM
- **Runtime Environment**: Python Standard Library HTTP (3.12.10)
- **Profiling Method**: Deterministic monotonic and thread CPU timers via `In-process sequential benchmark driver`
- **Measurement Repetitions**: 5 measured iterations (1 warm-up discarded)
- **Target Classification**: `local` environment

## 3. Verified Performance Bottlenecks
### 1. [HIGH] find-001: Expensive PBKDF2 Password Hashing Execution in Request Thread
- **Category**: `expensive_compute` | **Confidence**: High
- **Code Location**: `examples/demo-app/database.py:122` in `hash_password()`
- **Workflows Affected**: wf_user_signup
- **Impact Metrics**:
  - Owned Execution Time: **235.32 ms**
  - Total Frame Duration: **235.32 ms**
  - Workflow Latency Contribution: **96.01%**
  - Audit Share: **37.29%**
  - Total Invocations: **1**
  - CPU vs Wait Split: 231.79 ms CPU / 3.53 ms Wait
  - Node Statistics: p50=235.32 ms, p95=247.09 ms (CV=0.0350)
- **Where inside the function**:
  | Line | Code | Duration | Share of Function |
  |---|---|---|---|
  | L122 | `dk = hashlib.pbkdf2_hmac(` | 234.84 ms | 99.8% |
  | L128 | `return dk.hex()` | 0.00 ms | 0.0% |
- **Root Cause**: PBKDF2-HMAC-SHA256 executes 350,000 iterations synchronously in the HTTP request thread, consuming significant CPU compute time.
- **Reproduction Context**: Triggered on every POST /api/signup registration request.
- **Severity Rationale**: PBKDF2 is an expected expensive cryptographic operation consuming 96.0% of user registration workflow latency. While single-request latency is inherently bounded by the 350,000 iterations work factor, concurrent client execution causes worker thread CPU saturation on ThreadingHTTPServer. Rated as High relative performance priority.
- **Evidence References**:
  - Call tree node 'hash_password' accounts for 235.32 ms owned time (96.0% of workflow).
  - CPU vs wait decomposition confirms compute saturation: over 95% active CPU time.
  - Concurrency probe demonstrates worker thread CPU contention on ThreadingHTTPServer: latency inflation under concurrent load while single request remains bound by the 350,000 PBKDF2 iterations.

### 2. [MEDIUM] find-002: N+1 Query Pattern in Catalog Products and Reviews Retrieval
- **Category**: `n_plus_one_query` | **Confidence**: High
- **Code Location**: `examples/demo-app/database.py:210` in `get_products_with_reviews()`
- **Workflows Affected**: wf_product_listing
- **Impact Metrics**:
  - Owned Execution Time: **12.20 ms**
  - Total Frame Duration: **12.20 ms**
  - Workflow Latency Contribution: **99.99%**
  - Audit Share: **1.93%**
  - Total Invocations: **1**
  - CPU vs Wait Split: 9.15 ms CPU / 3.06 ms Wait
  - Node Statistics: p50=12.20 ms, p95=12.81 ms (CV=0.0350)
- **Root Cause**: get_products_with_reviews executes an initial query for 50 products, followed by 50 individual sequential queries to fetch reviews per product.
- **Reproduction Context**: Triggered on GET /api/products.
- **Severity Rationale**: Produces 51 database roundtrips for 50 items, dominating endpoint latency.
- **Evidence References**:
  - Scale probe confirms N+1 query loop: call tree records 50 sequential executions of 'SELECT reviews' under node_n1_reviews (250 total calls across 5 runs, 50.0 calls/run) consuming 7.16 ms (58.7% of workflow).

### 3. [MEDIUM] find-003: Full Table Scan on Unindexed Events Table
- **Category**: `missing_index` | **Confidence**: High
- **Code Location**: `examples/demo-app/database.py:280` in `get_events_by_status()`
- **Workflows Affected**: wf_event_analytics
- **Impact Metrics**:
  - Owned Execution Time: **21.93 ms**
  - Total Frame Duration: **21.93 ms**
  - Workflow Latency Contribution: **100.00%**
  - Audit Share: **3.47%**
  - Total Invocations: **1**
  - CPU vs Wait Split: 19.27 ms CPU / 2.66 ms Wait
  - Node Statistics: p50=21.93 ms, p95=23.02 ms (CV=0.0350)
- **Root Cause**: events table contains 10,000+ records but has no index on status or created_at, forcing a full table scan and sort on every query.
- **Reproduction Context**: Triggered on GET /api/events?status=FAILURE.
- **Severity Rationale**: Latency scales linearly with table size (O(N)), degrading database performance as event volume grows.
- **Evidence References**:
  - Scale probe across 1,000, 5,000, and 10,000 rows confirms linear table scan duration growth from 0.52 ms to 2.44 ms.
  - SQLite EXPLAIN QUERY PLAN confirms unindexed execution: 'SCAN events' and 'USE TEMP B-TREE FOR ORDER BY'.

## 4. Needs-Validation Findings
### 1. find-004: Synchronous Blocking External Call in Notification Dispatch
- **Category**: `blocking_io` | **Status**: Needs Validation
- **Candidate Location**: `examples/demo-app/database.py:229` in `send_external_notification()`
- **Hypothesis**: Mock notification sleep (350 ms) introduces synthetic delay in request handler; production third-party gateway may introduce variable network latency or timeouts.
- **Available Evidence**:
  - Simulated node duration is 350.74 ms wait time occupying 100.0% of workflow duration (mock delay).
- **Missing Evidence**: ['Real external network delay vs mock sleep', 'Production gateway response time distribution under load']
- **Validation Blocker**: Production third-party gateway latency cannot be measured safely in test environment without credentialed external endpoint.
- **Safe Next Validation Step**: Trace production gateway latency via OpenTelemetry distributed tracing or instrument canary requests to measure genuine external network latency.

## 5. Static Code Observations
### 1. obs-001: Unbounded Query Limit on Events API Endpoint
- **Category**: `unbounded_query` | **Location**: `examples/demo-app/database.py:280`
- **Static Rationale**: Default query parameter allows retrieving large result sets without hard upper ceiling.
- **Suggested Action**: Enforce maximum page size limit (e.g. max limit=100) and cursor pagination.

### 2. obs-002: Unpooled SQLite Database Connections Opened Per Request
- **Category**: `unpooled_resource` | **Location**: `examples/demo-app/database.py:31`
- **Static Rationale**: A new SQLite connection is opened and closed per helper function instead of leveraging a shared thread-safe pool.
- **Suggested Action**: Implement a connection pool or reuse request-scoped database handles.

## 6. Function Coverage Summary
- **Hot Units (Audit Share >= 10% & Owned >= 5.0 ms)**: 2
- **Warm Units (Audit Share >= 1% & Owned >= 1.0 ms)**: 3
- **Cold Units (Audit Share < 1% or Owned < 1.0 ms)**: 5
- **Not Reached by Any Measured Workflow**: 1
- **Insufficient Data**: 0

## 7. Unexercised Units
| Unit ID | Name | File | Line | Reason |
|---|---|---|---|---|
| `unit-011` | `archive_stale_events` | `examples/demo-app/database.py` | 294 | Not reached by any measured workflow |

## 8. Measurement Limitations
- Node-level profiling overhead calibrated; external network dependencies simulated or isolated as documented.
