# Phase 1: Reconnaissance (Whole-Repository Discovery)

## Purpose
Reconnaissance is the initial discovery phase of a full performance audit. It explores the target codebase without modifying files or executing traffic drivers. It maps technology stacks, identifies all HTTP routes, controller actions, background jobs, documents data layers and external network dependencies, scans for static performance anti-patterns, and catalogs every functional unit in scope.

The phase outputs the system architectural map (`.work/architecture.json`), initialized run metadata (`.work/run-metadata.json`), and the inventory ledger (`.work/coverage-ledger.json`) with all discovered units marked as `pending`.

## Inputs
1. **Target Repository Root Path**: File system path to the target application.
2. **Scope Paths**: Target directories or modules to audit (defaults to entire repository excluding third-party libraries and test suites).
3. **Configuration & Manifest Files**: Package manifests (`pyproject.toml`, `package.json`, `go.mod`, `pom.xml`, `Cargo.toml`), framework configurations, routing definitions, database migrations, and schema files.

## Steps

### Step 1: Technology Stack and Environment Detection
- Identify primary programming language(s), framework(s), runtime version, and application type (monolith, API service, background worker).
- Detect database engines (e.g., PostgreSQL, SQLite, MySQL, Redis, MongoDB), ORMs, database connection libraries, and caching layers.
- Check how the application starts, configuration variables, and default port or socket bindings.

### Step 2: Parallel Reconnaissance Exploration
Start sub-agents in parallel if supported, otherwise execute each track sequentially:

#### Track A: Entry Points & Routing Reconnaissance
- Scan router definitions, controller classes, route decorators, CLI argument parsers, and job queues.
- Enumerate every external interface:
  * HTTP endpoints (method, path, handler function, file, line).
  * WebSocket handlers and RPC methods.
  * Background worker tasks, message queue consumers, and scheduled cron jobs.
- Map each entry point to an identifiable workflow name (e.g., `user_signup`, `product_search`, `order_checkout`).

#### Track B: Data Layer & Persistence Mapping
- Inspect database connection mechanisms: pooling configurations, pool min/max sizes, connection timeouts, and connection lifecycles.
- Catalog data models, table definitions, foreign keys, and indexes.
- Identify queries executed within transactions, schema migrations, and ORM relationship loading settings (lazy vs. eager).
- Flag tables or query patterns lacking indexes on frequently filtered or joined columns.

#### Track C: External Calls & Outbound Services
- Identify outbound network clients (HTTP client libraries, gRPC clients, cloud SDKs, third-party APIs, webhooks).
- Audit timeout configurations for each outbound client call. Flag calls that omit connection or read timeouts.
- Identify retry loops, exponential backoff policies, circuit breakers, and fallback handlers.

#### Track D: Static Performance Pattern Scan
Scan the codebase for the 8 known latency and throughput anti-patterns:
1. **N+1 Queries**: Database queries executed inside loop iterations over collections.
2. **Blocking Calls in Async Code**: Synchronous I/O operations (`time.sleep()`, synchronous HTTP requests, blocking disk I/O) invoked inside async event loops.
3. **Missing Indexes**: Columns used in `WHERE`, `JOIN`, or `ORDER BY` clauses that lack database indexes.
4. **Unbounded Queries**: Database queries missing `LIMIT` clauses or pagination on unbounded tables.
5. **Missing Timeouts**: Network calls or socket reads configured without explicit timeouts.
6. **Inline Heavy Compute**: CPU-heavy tasks (cryptographic operations, image transformations, large JSON parsing) executed synchronously in request-handling threads.
7. **Unpooled Resources**: New database connections, HTTP sessions, or socket connections instantiated per request instead of using shared pools.
8. **Heavy Import / Initialization**: Expensive module imports, large file loads, or heavyweight object initializations occurring inside request paths instead of application startup.

### Step 3: Code Unit Cataloging & Identifier Assignment
- Collate every discovered entry point, handler function, background job, and significant business logic function into an inventory.
- Assign a stable, unique identifier to each unit (`unit-001`, `unit-002`, ...).
- Assign unit metadata:
  * `id`: Unique string identifier.
  * `kind`: Exact enum value: `"function"`, `"endpoint"`, or `"job"`.
  * `name`: Descriptive name (e.g., `POST /signup`, `hash_password`, `order_cleanup_task`).
  * `file`: Relative file path from repository root.
  * `line`: Positive integer indicating definition line.
  * `status`: Strictly set to `"pending"`.
  * `workflows`: Array of workflow identifiers associated with this unit.
  * `note`: Optional string or empty string.

### Step 4: Architectural Synthesis, Environment Probing & Ledger Initialization
- Execute `python skills/profile-audit/scripts/env_probe.py` to probe host operating system, architecture, CPU model, core counts, and total system RAM dynamically. Never use static placeholders (`Multi-core processor`, `System RAM`, etc.).
- Stamp `started_at` timestamp via `python skills/profile-audit/scripts/measurements_tools.py stamp --start <run-dir>/.work/run-metadata.json`.
- Write `.work/architecture.json` conforming to `schemas/architecture.schema.json`.
- Write `.work/coverage-ledger.json` conforming to `schemas/coverage-ledger.schema.json`.
- Write initial `.work/run-metadata.json` conforming to `schemas/run-metadata.schema.json`.
- Confirm that all units in the ledger have `status: "pending"`.

---

## Sub-Agent Prompt Templates

### Prompt 1: Entry Points Reconnaissance Sub-Agent
```text
You are an entry point reconnaissance sub-agent for profile-audit.
Role: Discover and catalog all external entry points (HTTP routes, RPCs, CLI commands, queue consumers, background jobs) in the repository.

Inputs:
- REPO_PATH: Path to the target repository.
- SCOPE_PATHS: Directories to inspect.

Strict Rules:
1. Do not modify any files in the repository.
2. Read router configurations, route decorators, controller definitions, worker queues, and command parsers.
3. Do not fabricate file paths or line numbers; verify against actual source files.

Output Format:
Return a JSON array of discovered unit objects:
[
  {
    "id": "unit-001",
    "kind": "endpoint",
    "name": "POST /api/v1/signup",
    "file": "src/controllers/auth.py",
    "line": 42,
    "workflows": ["user_signup"]
  }
]

Error Handling & Stop Condition:
If a router definition references missing files or external libraries, note the library in "name" and omit "line" or set to 1. Stop when all entry point files have been scanned.
```

### Prompt 2: Data Layer Reconnaissance Sub-Agent
```text
You are a data layer reconnaissance sub-agent for profile-audit.
Role: Map database schemas, models, query patterns, and connection management.

Inputs:
- REPO_PATH: Path to the target repository.

Strict Rules:
1. Read-only operation; never run migrations or execute DDL.
2. Inspect models, migration files, ORM mappings, and database client initialization.

Output Format:
Return a JSON object:
{
  "engine": "sqlite" | "postgresql" | "mysql" | "other",
  "connection_pooling": {
    "enabled": true,
    "pool_size": 10
  },
  "tables": [
    {
      "name": "users",
      "columns": ["id", "email", "password_hash", "created_at"],
      "indexes": ["PRIMARY KEY (id)", "UNIQUE (email)"],
      "row_count_estimate": 1000
    }
  ],
  "potential_missing_indexes": [
    {
      "table": "sessions",
      "column": "user_id",
      "reason": "Filtered frequently in authentication lookup without index"
    }
  ]
}

Error Handling & Stop Condition:
If database config is in environment variables, check defaults in configuration files. Stop when data layer mapping is complete.
```

### Prompt 3: External Dependencies Reconnaissance Sub-Agent
```text
You are an external dependencies reconnaissance sub-agent for profile-audit.
Role: Enumerate all outbound network calls, APIs, third-party services, and background queues.

Inputs:
- REPO_PATH: Path to the target repository.

Strict Rules:
1. Never trigger external network calls.
2. Inspect source code for HTTP clients, gRPC stubs, SDK clients, and message producers.

Output Format:
Return a JSON array:
[
  {
    "name": "payment_gateway",
    "client": "requests.post",
    "timeout_configured": false,
    "retry_policy": null,
    "file": "src/services/billing.py",
    "line": 85
  }
]

Error Handling & Stop Condition:
If timeout values are imported from constants, trace the constant definition to get the numeric value. Stop when all outbound client references are cataloged.
```

### Prompt 4: Static Anti-Pattern Scan Sub-Agent
```text
You are a static code pattern scanning sub-agent for profile-audit.
Role: Scan codebase for potential latency hotspots and architectural anti-patterns.

Inputs:
- REPO_PATH: Path to the target repository.

Strict Rules:
1. Read-only inspection; do not modify files.
2. Inspect for: N+1 queries, blocking calls in async loops, missing timeouts, unbounded queries, inline heavy compute, unpooled connections.
3. Every observation must cite exact file and line number. These are static observations only, not verified runtime bottlenecks.

Output Format:
Return a JSON array:
[
  {
    "category": "n_plus_one_query",
    "file": "src/services/orders.py",
    "line": 104,
    "description": "Loop iterates over orders and queries database for customer details per order",
    "suggested_check": "Inspect query count during driver execution"
  }
]

Error Handling & Stop Condition:
Stop when all in-scope source files have been scanned.
```
