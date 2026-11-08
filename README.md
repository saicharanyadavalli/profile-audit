# profile-audit

[![Agent Skills Spec](https://img.shields.io/badge/Agent_Skills-v1.0-blue.svg)](https://agentskills.io)
[![Python Version](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-0%20external%20(stdlib%20only)-success.svg)](https://docs.python.org/3/library/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A portable, zero-dependency agent skill that profiles applications, pinpoints where execution time is spent, and reports bottlenecks across any language or framework. Operating strictly in report-only mode, it observes and measures real workloads without altering application source code, configuration files, or databases.

---

## What It Solves

Automated coding agents often guess performance bottlenecks based on static code appearances or jump into speculative code rewrites that break application semantics. `profile-audit` introduces deterministic performance measurement:

- **Measure, Never Guess**: Every reported bottleneck traces directly to measured execution timings, sample statistics, and call tree hierarchies.
- **Report-Only Discipline**: Never edits audited code, never modifies database schemas, and never applies speculative patches.
- **Owned Time Metric**: Evaluates execution time with `owned_ms`—a function's total time minus its nearest descendant ledger units. Non-unit library descendants (SQL drivers, HTTP clients) belong to their unit caller.
- **Concurrent Interval Math**: For concurrent asynchronous children, calculates `owned_ms` using the union of overlapping execution intervals rather than simple summation.
- **Deterministic Classification**: Classifies every functional unit in scope using absolute and relative thresholds (`hot`: >=10% workflow share & >=5.0 ms owned; `warm`: >=1% workflow share & >=1.0 ms owned; `cold`; `not_exercised`).
- **Honest Coverage**: Unexercised code is explicitly identified as `not_exercised` rather than implied to be efficient.
- **Measured CPU vs. Wait Separation**: Measures wall clock and thread/process CPU clocks directly (`wait_ms = max(0, wall - cpu)`); never assumes arbitrary ratios.
- **Verified vs. Hypothesis Safety**: Unverified hypotheses are strictly marked `needs_validation` with 0 severity/impact and require concrete missing evidence before confirmation.
- **Security-First Remediation**: Enforces that fixes never recommend lowering cryptographic cost or security parameters to gain speed.
- **Provenance on Everything**: Tags every timing node as `measured`, `simulated`, or `estimated`.
- **Single Self-Contained Offline Dashboard**: Delivers an interactive HTML dashboard with embedded canonical data, waterfall views, interactive flame graph with zoom, line-level source drawer, and dark/light theme support.

---

## Installation

### One-Command Universal Install (Recommended)
Installs and registers `profile-audit` across all coding agents on your machine in one go (OpenCode `/profile-audit` autocomplete menu, Google Antigravity, Claude Code, Cursor, and Codex):

```bash
git clone https://github.com/saicharanyadavalli/profile-audit.git
cd profile-audit
python install.py
```

### Option 2: Via Skills CLI (`npx skills`)
Install globally across supported coding agents:

```bash
npx skills add saicharanyadavalli/profile-audit --skill profile-audit -g
```

### Option 3: Manual Installation
* **OpenCode**: Copy `skills/profile-audit` to `~/.config/opencode/skills/profile-audit` and `.opencode/command/*` to `~/.config/opencode/command/`.
* **Google Antigravity**: Copy `skills/profile-audit` to `~/.gemini/config/skills/profile-audit`.
* **Claude Code**: Copy `skills/profile-audit` to `~/.claude/skills/profile-audit`.
* **Cursor / Codex**: Copy `skills/profile-audit` to `~/.cursor/skills/profile-audit`.

---

## Invocation & Usage Modes

In Antigravity and modern AI coding agents, skills are **context-aware agent capabilities** (runbooks) rather than hardcoded UI menu shortcuts. The agent automatically detects when to activate the skill based on your request:

```text
/profile-audit
"Profile this application and identify where time is spent."
"Why is the /api/checkout endpoint experiencing high latency?"
"Find all database query bottlenecks in the orders service."
```

When invoked, the agent reads `SKILL.md` via progressive disclosure and guides you through deterministic measurement.

The skill evaluates the request and automatically selects the appropriate mode:

### 1. Targeted Mode (Mode B)
Triggered whenever the request names specific functions, endpoints, or a single workflow (e.g., "Profile these 3 database queries" or "Why is user signup slow?").

- **Execution**: Single-agent execution without sub-agents or multi-phase overhead.
- **Measurement**: Executes 1 warm-up run (discarded) and 5 measured runs sequentially.
- **Output**: Answers directly in conversation and writes exactly one summary file on disk:
  - `PROFILING-AUDIT.md`
- **Constraint**: Never generates HTML reports, multi-file suites, or loose JSON files.

### 2. Full Audit Mode (Mode A)
Triggered when invoked with no narrower scope (e.g. `/profile-audit`) or when the user explicitly requests an audit of the entire repository or application.

- **Execution**: Multi-phase workflow (Phases 1 through 5) utilizing parallel sub-agents for discovery and analysis where supported, with strictly sequential measurement runs.
- **Output**: Exactly three final deliverable files:
  - `PROFILING-DASHBOARD.html` (interactive self-contained offline dashboard)
  - `FINDINGS.md` (detailed verified bottlenecks & hypotheses)
  - `FIXES.md` (actionable remediation plans)
- **Data Lifecycle**: Canonical JSON is stored in `.work/` during execution, embedded into the dashboard, validated, and `.work/` is deleted upon successful verification.

---

## Mathematical Model & Accounting

`profile-audit` separates execution time into four distinct, mathematically verified metrics:

| Metric | Definition | Formula / Accounting |
|---|---|---|
| **`self_ms`** | Exclusive execution time | Wall-clock time spent directly in the function body excluding all children. |
| **`owned_ms`** | Owned execution time | Function duration minus nearest descendant ledger units. Uses interval union (`∪ [t_start, t_end]`) for concurrent async tasks. |
| **`total_ms`** | Inclusive execution time | Total wall-clock duration from entry to return. |
| **`workflow_share_pct`** | Workflow contribution | `(owned_ms / workflow_total_ms) * 100%` |

---

## Repository Structure

```text
profile-audit/
├── .editorconfig
├── .gitattributes
├── .gitignore
├── LICENSE
├── README.md
├── install.py
├── .github/
│   └── workflows/
│       └── ci.yml
├── .opencode/
│   └── command/
│       ├── profile-audit.md
│       └── profile.md
├── docs/
│   └── design.md
├── examples/
│   └── sample-run/
│       ├── PROFILING-DASHBOARD.html
│       ├── FINDINGS.md
│       └── FIXES.md
└── skills/
    └── profile-audit/
        ├── SKILL.md
        ├── RECONNAISSANCE.md
        ├── DRIVERS-AND-MEASUREMENT.md
        ├── ANALYSIS.md
        ├── VERIFICATION-AND-REPORTING.md
        ├── TARGETED-MODE.md
        ├── CLOUD-TARGET.md
        ├── assets/
        │   └── report-template.html
        ├── schemas/
        │   ├── architecture.schema.json
        │   ├── coverage-ledger.schema.json
        │   ├── findings.schema.json
        │   ├── measurements.schema.json
        │   ├── report-data.schema.json
        │   └── run-metadata.schema.json
        └── scripts/
            ├── build_report.py
            ├── classify.py
            ├── env_probe.py
            ├── measurements_tools.py
            ├── render_markdown.py
            └── validate.py
```

---

## Contract Schemas & CLI Helper Utilities

All helper scripts require **only the Python 3.9+ standard library** (zero third-party dependencies):

| Script | Purpose |
|---|---|
| [`classify.py`](skills/profile-audit/scripts/classify.py) | Computes `self_ms`, `owned_ms` (via interval unions), and assigns ledger classification. |
| [`measurements_tools.py`](skills/profile-audit/scripts/measurements_tools.py) | Call-tree algebra, leaves-up rollups, summary statistics (P50, P95, CV), and invocation counting. |
| [`env_probe.py`](skills/profile-audit/scripts/env_probe.py) | Gathers host machine hardware, OS, CPU cores, timer resolution, and Python environment facts. |
| [`validate.py`](skills/profile-audit/scripts/validate.py) | Semantic and schema validator. Enforces referential integrity, timing conservation, and schema conformance. |
| [`render_markdown.py`](skills/profile-audit/scripts/render_markdown.py) | Compiles embedded report data into GitHub-flavored Markdown deliverables (`FINDINGS.md`, `FIXES.md`). |
| [`build_report.py`](skills/profile-audit/scripts/build_report.py) | Bundles JSON telemetry into `report-template.html` to generate `PROFILING-DASHBOARD.html`. |

---

## Output Deliverables

| Output File | Mode | Description |
|---|---|---|
| `PROFILING-AUDIT.md` | Targeted | Single concise summary of measured timings, call hierarchy, root causes, and fix plans. |
| `PROFILING-DASHBOARD.html` | Full Audit | Interactive offline single-file report with 8 views (overview, architecture, workflows, functions, flame graph, resources, findings, coverage) and embedded canonical data. |
| `FINDINGS.md` | Full Audit | Executive summary, methodology, machine details, verified bottlenecks, needs-validation findings, static observations, coverage summary, and unexercised units. |
| `FIXES.md` | Full Audit | Remediation plans with concrete implementation steps, prerequisites, and labelled projected impacts. |

---

## Requirements

- **Agent Runtime**: Any coding agent supporting the open [Agent Skills](https://agentskills.io) specification.
- **Sub-Agent Support**: Optional. If the host environment supports sub-agents, discovery and analysis run in parallel. If unsupported, the agent executes all phases sequentially without failure.
- **Script Runtime**: Python 3.9 or higher (standard library only). Zero external package installations (`pip install`) are required.

---

## License

[MIT](LICENSE)
