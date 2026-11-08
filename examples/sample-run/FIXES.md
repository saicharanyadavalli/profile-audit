# Remediation Plan and Fix Recommendations

This document details actionable remediation plans for performance bottlenecks.
Fixes are derived strictly from inspected system architecture, models, and measurement evidence.

## 1. Verified Bottleneck Remediation Plans
### 1. [find-001] Expensive PBKDF2 Password Hashing Execution in Request Thread
- **Summary**: Offload CPU-intensive PBKDF2 password hashing from HTTP request handler threads into a background worker process pool. In a multi-threaded server architecture (ThreadingHTTPServer), CPU-bound hashing in request threads causes GIL contention and CPU core starvation under concurrent client load. Offloading isolates CPU consumption and frees HTTP worker threads to accept and serve concurrent connections. Note: PBKDF2 work factor is preserved at 350,000 iterations to maintain security compliance; the single signup request still waits for hash completion (~235 ms), but overall server concurrency and aggregate request throughput improve substantially.
- **Prerequisites**:
  - Provision ProcessPoolExecutor with worker count scaled to host CPU cores
  - Maintain existing salt and iteration parameters to avoid hash migration
- **Implementation Steps**:
  1. In database.py around line 135, wrap hashlib.pbkdf2_hmac invocation in an isolated worker function.
  2. Configure a process pool executor in server.py to manage dedicated CPU-bound worker processes.
  3. Submit password hash computations to the process pool from the request handler, awaiting the future before database insertion.
  4. Preserve 350,000 PBKDF2 iterations without lowering cryptographic security parameters.
- **Potential Risks & Regressions**:
  - Process scheduling overhead and IPC serialization latency (~1-2 ms per request)
  - Memory overhead from dedicated worker processes
- **Projected Improvement**: Improves concurrent request throughput and eliminates worker thread starvation under multi-client load; single-request latency remains ~235 ms (hash computation bound) (Measured Baseline Projection)
- **Validation Procedure**: Re-execute concurrency benchmark probe across c=1, c=5, and c=10 to confirm increased throughput without thread starvation.
- **Target Architectural End Goal**: CPU-bound hashing is isolated in a dedicated worker process pool, ensuring predictable HTTP server thread responsiveness without compromising password security.

### 2. [find-002] N+1 Query Pattern in Catalog Products and Reviews Retrieval
- **Summary**: Replace sequential N+1 review queries with a single batch JOIN or IN-clause query.
- **Prerequisites**:
  - Verify sufficient database buffer cache for joined result sets.
- **Implementation Steps**:
  1. In examples/demo-app/database.py line 210, rewrite review fetching query to use JOIN reviews ON products.id = reviews.product_id or WHERE product_id IN (...).
  2. Group returned reviews by product_id in memory.
- **Potential Risks & Regressions**:
  - Increased memory footprint if products have thousands of reviews each.
- **Projected Improvement**: Projected latency improvement (Basis: eliminates 50 sequential roundtrips saving ~7.2 ms)
- **Validation Procedure**: Execute wf_product_listing benchmark; verify sqlite statement count equals 1.
- **Target Architectural End Goal**: Batch single-roundtrip data retrieval for products and reviews.

### 3. [find-003] Full Table Scan on Unindexed Events Table
- **Summary**: Create composite index on events(status, created_at DESC).
- **Prerequisites**:
  - Sufficient disk space for B-tree index allocation (~2MB).
- **Implementation Steps**:
  1. In examples/demo-app/database.py line 75, add schema index: CREATE INDEX IF NOT EXISTS idx_events_status_created ON events(status, created_at DESC);
  2. Execute SQLite EXPLAIN QUERY PLAN to verify index search scan replaces full table scan.
- **Potential Risks & Regressions**:
  - Negligible write latency overhead during event insertion.
- **Projected Improvement**: Projected latency improvement (Basis: index lookup converts O(N) scan across 10,000 rows to O(log N) B-tree lookup)
- **Validation Procedure**: Benchmark GET /api/events with EXPLAIN QUERY PLAN confirmation.
- **Target Architectural End Goal**: Fast O(log N) indexed search and sort for event logs.

## 2. Provisional Recommendations (Finding Not Yet Verified)
### 1. [find-004] Synchronous Blocking External Call in Notification Dispatch *(Provisional)*
- **Notice**: This finding is pending runtime verification and has not been definitively proven in baseline runs.
- **Summary**: Offload notification dispatching to asynchronous background worker queue with explicit 2000 ms timeout.
- **Candidate Steps**:
  1. In examples/demo-app/database.py line 229, convert send_external_notification to async worker task.
  2. Configure explicit 2000 ms timeout on HTTP request transport.
