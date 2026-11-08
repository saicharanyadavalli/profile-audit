#!/usr/bin/env python3
"""
render_markdown.py - Deterministic Markdown generator and consistency checker.

Conforms to Python 3.9+ standard library only. Strictly NO third-party imports.
Generates:
- Mode A (Full): FINDINGS.md and FIXES.md
- Mode B (Targeted): PROFILING-AUDIT.md
Accepts either a working directory (.work/ or output dir) or a dashboard HTML file.
Supports --check flag which exits with code 1 if existing markdown files differ
from what the canonical data would generate.
"""

import argparse
import difflib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from validate import extract_data_from_html
except ImportError:
    from .validate import extract_data_from_html


def load_canonical_data(target_path: Path) -> Dict[str, Any]:
    """Loads all canonical profiling data from either a directory or an HTML file."""
    if target_path.is_file() and target_path.suffix.lower() == ".html":
        embedded, err = extract_data_from_html(target_path)
        if err or not embedded:
            raise ValueError(f"Failed to extract canonical data from HTML: {err}")
        return {
            "run_metadata": embedded.get("run_metadata", {}),
            "coverage_ledger": embedded.get("coverage_ledger", {}),
            "measurements": embedded.get("measurements", {}),
            "findings": embedded.get("findings", []),
            "observations": embedded.get("observations", []),
            "reviewed_not_bottleneck": embedded.get("reviewed_not_bottleneck", []),
            "architecture": embedded.get("architecture", {}),
        }

    # Directory input: check if .work exists inside it
    work_dir = target_path / ".work" if (target_path / ".work").is_dir() else target_path

    if not (work_dir / "run-metadata.json").exists() and (target_path / "PROFILING-DASHBOARD.html").is_file():
        embedded, err = extract_data_from_html(target_path / "PROFILING-DASHBOARD.html")
        if err is not None:
            raise ValueError(f"Failed to extract embedded JSON from {target_path / 'PROFILING-DASHBOARD.html'}: {err}")
        return {
            "run_metadata": embedded.get("run_metadata", {}),
            "coverage_ledger": embedded.get("coverage_ledger", {}),
            "measurements": embedded.get("measurements", {}),
            "findings": embedded.get("findings", []),
            "observations": embedded.get("observations", []),
            "reviewed_not_bottleneck": embedded.get("reviewed_not_bottleneck", []),
            "architecture": embedded.get("architecture", {}),
        }

    files = {
        "metadata": "run-metadata.json",
        "ledger": "coverage-ledger.json",
        "measurements": "measurements.json",
        "findings": "findings.json",
    }
    data: Dict[str, Any] = {}
    for key, fname in files.items():
        fpath = work_dir / fname
        if not fpath.exists():
            raise FileNotFoundError(f"Required file '{fname}' not found in {work_dir}")
        with open(fpath, "r", encoding="utf-8") as f:
            data[key] = json.load(f)

    arch_file = work_dir / "architecture.json"
    arch_data = {}
    if arch_file.exists():
        with open(arch_file, "r", encoding="utf-8") as f:
            arch_data = json.load(f)

    raw_findings = data["findings"]
    return {
        "run_metadata": data["metadata"],
        "coverage_ledger": data["ledger"],
        "measurements": data["measurements"],
        "findings": raw_findings.get("findings", []),
        "observations": raw_findings.get("observations", []),
        "reviewed_not_bottleneck": raw_findings.get("reviewed_not_bottleneck", []),
        "architecture": arch_data,
    }


# ---------------------------------------------------------------------------
# Full Mode Generators: FINDINGS.md and FIXES.md
# ---------------------------------------------------------------------------

def generate_findings_md(data: Dict[str, Any]) -> str:
    meta = data.get("run_metadata", {})
    ledger = data.get("coverage_ledger", {})
    units = ledger.get("units", [])
    measurements = data.get("measurements", {})
    workflows = measurements.get("workflows", [])
    all_findings = data.get("findings", [])
    observations = data.get("observations", [])
    reviewed = data.get("reviewed_not_bottleneck", [])

    verified_findings = [f for f in all_findings if f.get("status") == "verified"]
    needs_val_findings = [f for f in all_findings if f.get("status") == "needs_validation"]

    # Compute status counts
    status_counts = {"hot": 0, "warm": 0, "cold": 0, "not_exercised": 0, "insufficient_data": 0}
    for u in units:
        st = u.get("status")
        if st in status_counts:
            status_counts[st] += 1

    total_units = len(units)
    exercised_units = status_counts["hot"] + status_counts["warm"] + status_counts["cold"]
    cov_pct = (exercised_units / total_units * 100.0) if total_units > 0 else 0.0

    machine = meta.get("machine", {})
    method = meta.get("method", {})
    runtime = meta.get("runtime", {})

    lines: List[str] = [
        "# Performance Profiling Audit Findings",
        "",
        "## 1. Executive Summary",
        f"- **Repository Target**: `{meta.get('repo_name', 'target')}` (Revision `{meta.get('source_ref', 'HEAD')}`)",
        f"- **Workflows Measured**: {len(workflows)}",
        f"- **Total Functions / Units In-Scope**: {total_units}",
        f"- **Functions Exercised**: {exercised_units} ({cov_pct:.1f}% coverage)",
        f"- **Verified Bottlenecks**: {len(verified_findings)}",
        f"- **Needs Validation Findings**: {len(needs_val_findings)}",
        f"- **Static Code Observations**: {len(observations)}",
        "",
        "## 2. Methodology and Machine Specification",
        f"- **Operating System**: {machine.get('os', 'Unknown')}",
        f"- **CPU**: {machine.get('cpu', 'Unknown')}",
        f"- **Memory**: {machine.get('memory', 'Unknown')}",
        f"- **Runtime Environment**: {runtime.get('language', 'Unknown')} {runtime.get('framework', '')} ({runtime.get('versions', 'n/a')})",
        f"- **Profiling Method**: {method.get('profiler', 'Standard profiler')} via `{method.get('driver', 'automated driver')}`",
        f"- **Measurement Repetitions**: {meta.get('repetitions', 5)} measured iterations ({meta.get('warmups', 1)} warm-up discarded)",
        f"- **Target Classification**: `{meta.get('target_type', 'local')}` environment",
        "",
        "## 3. Verified Performance Bottlenecks",
    ]

    if not verified_findings:
        lines.append("No verified performance bottlenecks detected across measured workflows.")
    else:
        for idx, f in enumerate(verified_findings, 1):
            fid = f.get("id")
            title = f.get("title")
            cat = f.get("category")
            impact = f.get("impact", "medium").upper()
            confidence = f.get("confidence", "high").capitalize()
            loc = f.get("location", {})
            metrics = f.get("metrics", {})
            wfs = ", ".join(f.get("workflows", []))

            owned_ms = metrics.get("owned_ms", metrics.get("self_ms", 0.0))
            total_ms = metrics.get("total_ms", 0.0)
            wf_share = metrics.get("workflow_share_pct", metrics.get("share_of_workflow_pct", 0.0))
            audit_share = metrics.get("audit_share_pct", 0.0)
            calls = metrics.get("calls", 1)

            lines.extend([
                f"### {idx}. [{impact}] {fid}: {title}",
                f"- **Category**: `{cat}` | **Confidence**: {confidence}",
                f"- **Code Location**: `{loc.get('file')}:{loc.get('line')}` in `{loc.get('function')}()`",
                f"- **Workflows Affected**: {wfs}",
                f"- **Impact Metrics**:",
                f"  - Owned Execution Time: **{owned_ms:.2f} ms**",
                f"  - Total Frame Duration: **{total_ms:.2f} ms**",
                f"  - Workflow Latency Contribution: **{wf_share:.2f}%**",
                f"  - Audit Share: **{audit_share:.2f}%**",
                f"  - Total Invocations: **{calls}**",
            ])
            if metrics.get("cpu_ms") is not None and metrics.get("wait_ms") is not None:
                lines.append(f"  - CPU vs Wait Split: {metrics.get('cpu_ms', 0.0):.2f} ms CPU / {metrics.get('wait_ms', 0.0):.2f} ms Wait")
            if metrics.get("p50_ms") is not None and metrics.get("p95_ms") is not None:
                cv = metrics.get("cv", metrics.get("coefficient_of_variation", 0.0))
                lines.append(f"  - Node Statistics: p50={metrics.get('p50_ms', 0.0):.2f} ms, p95={metrics.get('p95_ms', 0.0):.2f} ms (CV={cv:.4f})")

            # Where inside the function table from line_profiles
            primary_uid = (f.get("unit_ids") or [None])[0]
            line_profiles = data.get("measurements", {}).get("line_profiles", [])
            matched_lp = next((lp for lp in line_profiles if lp.get("unit_id") == primary_uid), None)
            if matched_lp and matched_lp.get("lines"):
                sorted_lines = sorted(matched_lp["lines"], key=lambda l: l.get("total_ms", 0.0), reverse=True)[:5]
                lines.extend([
                    "- **Where inside the function**:",
                    "  | Line | Code | Duration | Share of Function |",
                    "  |---|---|---|---|",
                ])
                for line_item in sorted_lines:
                    l_no = line_item.get("line")
                    code_snippet = line_item.get("code", "").strip().replace("|", "\\|")
                    l_ms = line_item.get("total_ms", 0.0)
                    l_share = line_item.get("share_of_unit_pct", 0.0)
                    lines.append(f"  | L{l_no} | `{code_snippet}` | {l_ms:.2f} ms | {l_share:.1f}% |")
            elif matched_lp and matched_lp.get("line_profile_status") == "unavailable":
                lines.append(f"- **Where inside the function**: Line-level profile unavailable ({matched_lp.get('reason', 'tracing unavailable')})")

            lines.extend([
                f"- **Root Cause**: {f.get('root_cause')}",
                f"- **Reproduction Context**: {f.get('reproduction', 'Observed in baseline automated benchmark')}",
                f"- **Severity Rationale**: {f.get('severity_rationale', '')}",
                f"- **Evidence References**:",
            ])
            for ev in f.get("evidence", []):
                lines.append(f"  - {ev}")
            lines.append("")

    lines.extend([
        "## 4. Needs-Validation Findings",
    ])
    if not needs_val_findings:
        lines.append("None. All detected findings have complete runtime measurement evidence.")
    else:
        for idx, f in enumerate(needs_val_findings, 1):
            fid = f.get("id")
            title = f.get("title")
            cat = f.get("category")
            loc = f.get("location", {})
            loc_str = f"`{loc.get('file')}:{loc.get('line')}` in `{loc.get('function')}()`" if loc.get("function") else f"`{loc.get('file')}:{loc.get('line')}`"
            v_reason = f.get("missing_evidence") or f.get("verification", {}).get("reason", "Incomplete runtime verification")
            lines.extend([
                f"### {idx}. {fid}: {title}",
                f"- **Category**: `{cat}` | **Status**: Needs Validation",
                f"- **Candidate Location**: {loc_str}",
            ])
            if f.get("hypothesis"):
                lines.append(f"- **Hypothesis**: {f.get('hypothesis')}")
            
            avail_ev = f.get("available_evidence") or f.get("evidence", [])
            if avail_ev:
                lines.append("- **Available Evidence**:")
                for ev in avail_ev:
                    lines.append(f"  - {ev}")
            
            lines.append(f"- **Missing Evidence**: {v_reason}")
            if f.get("validation_blocker"):
                lines.append(f"- **Validation Blocker**: {f.get('validation_blocker')}")
            if f.get("next_validation_step"):
                lines.append(f"- **Safe Next Validation Step**: {f.get('next_validation_step')}")
            lines.append("")

    lines.extend([
        "## 5. Static Code Observations",
    ])
    if not observations:
        lines.append("No static code architecture observations recorded.")
    else:
        for idx, obs in enumerate(observations, 1):
            loc = obs.get("location", {})
            loc_str = f"`{loc.get('file')}:{loc.get('line')}`" if isinstance(loc, dict) else str(loc)
            lines.extend([
                f"### {idx}. {obs.get('id')}: {obs.get('title')}",
                f"- **Category**: `{obs.get('category')}` | **Location**: {loc_str}",
                f"- **Static Rationale**: {obs.get('rationale')}",
                f"- **Suggested Action**: {obs.get('suggested_action')}",
                "",
            ])

    lines.extend([
        "## 6. Function Coverage Summary",
        f"- **Hot Units (Audit Share >= 10% & Owned >= 5.0 ms)**: {status_counts['hot']}",
        f"- **Warm Units (Audit Share >= 1% & Owned >= 1.0 ms)**: {status_counts['warm']}",
        f"- **Cold Units (Audit Share < 1% or Owned < 1.0 ms)**: {status_counts['cold']}",
        f"- **Not Reached by Any Measured Workflow**: {status_counts['not_exercised']}",
        f"- **Insufficient Data**: {status_counts['insufficient_data']}",
        "",
        "## 7. Unexercised Units",
    ])

    unexercised = [u for u in units if u.get("status") == "not_exercised"]
    if not unexercised:
        lines.append("All discovered in-scope units were reached during execution.")
    else:
        lines.extend([
            "| Unit ID | Name | File | Line | Reason |",
            "|---|---|---|---|---|",
        ])
        for u in unexercised:
            reason = u.get("status_reason") or "Not reached by any measured workflow"
            lines.append(f"| `{u.get('id')}` | `{u.get('name')}` | `{u.get('file')}` | {u.get('line')} | {reason} |")
        lines.append("")

    lines.extend([
        "## 8. Measurement Limitations",
    ])
    limitations = meta.get("limitations", [])
    if limitations:
        for lim in limitations:
            lines.append(f"- {lim}")
    else:
        lines.append("- Node-level profiling overhead calibrated; external network dependencies simulated or isolated as documented.")
    lines.append("")

    return "\n".join(lines)


def generate_fixes_md(data: Dict[str, Any]) -> str:
    all_findings = data.get("findings", [])
    verified_findings = [f for f in all_findings if f.get("status") == "verified"]
    needs_val_findings = [f for f in all_findings if f.get("status") == "needs_validation"]

    lines: List[str] = [
        "# Remediation Plan and Fix Recommendations",
        "",
        "This document details actionable remediation plans for performance bottlenecks.",
        "Fixes are derived strictly from inspected system architecture, models, and measurement evidence.",
        "",
        "## 1. Verified Bottleneck Remediation Plans",
    ]

    if not verified_findings:
        lines.append("No verified bottleneck fixes required.")
    else:
        for idx, f in enumerate(verified_findings, 1):
            fid = f.get("id")
            title = f.get("title")
            fix = f.get("fix", {})
            prereqs = fix.get("prerequisites", [])
            risks = fix.get("risks", [])
            steps = fix.get("steps", [])

            lines.extend([
                f"### {idx}. [{fid}] {title}",
                f"- **Summary**: {fix.get('summary')}",
                f"- **Prerequisites**:",
            ])
            if prereqs:
                for p in prereqs:
                    lines.append(f"  - {p}")
            else:
                lines.append("  - None identified.")

            lines.append("- **Implementation Steps**:")
            for s_idx, s in enumerate(steps, 1):
                lines.append(f"  {s_idx}. {s}")

            lines.extend([
                f"- **Potential Risks & Regressions**:",
            ])
            if risks:
                for r in risks:
                    lines.append(f"  - {r}")
            else:
                lines.append("  - None identified under expected workloads.")

            proj = fix.get("projection")
            if isinstance(proj, dict):
                basis = proj.get("basis")
                exp = proj.get("expected_result", "Projected latency improvement")
                if basis:
                    proj_str = f"{exp} (Basis: {basis})"
                else:
                    proj_str = exp
            elif isinstance(proj, str):
                proj_str = proj
            elif fix.get("projected_impact"):
                proj_str = fix.get("projected_impact")
            elif fix.get("expected_result"):
                basis_label = "Measured Baseline Projection" if fix.get("expected_result_basis") == "measured_baseline_projection" else "Rule of Thumb"
                proj_str = f"{fix.get('expected_result')} ({basis_label})"
            else:
                proj_str = "not projected"

            lines.extend([
                f"- **Projected Improvement**: {proj_str}",
                f"- **Validation Procedure**: {fix.get('validation', fix.get('validation_method', 'Re-execute workflow driver benchmark'))}",
                f"- **Target Architectural End Goal**: {fix.get('end_goal')}",
                "",
            ])

    lines.extend([
        "## 2. Provisional Recommendations (Finding Not Yet Verified)",
    ])
    if not needs_val_findings:
        lines.append("No provisional recommendations pending validation.")
    else:
        for idx, f in enumerate(needs_val_findings, 1):
            fid = f.get("id")
            title = f.get("title")
            fix = f.get("fix", {})
            lines.extend([
                f"### {idx}. [{fid}] {title} *(Provisional)*",
                f"- **Notice**: This finding is pending runtime verification and has not been definitively proven in baseline runs.",
                f"- **Summary**: {fix.get('summary')}",
                f"- **Candidate Steps**:",
            ])
            for s_idx, s in enumerate(fix.get("steps", []), 1):
                lines.append(f"  {s_idx}. {s}")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Targeted Mode Generator: PROFILING-AUDIT.md
# ---------------------------------------------------------------------------

def generate_targeted_audit_md(data: Dict[str, Any]) -> str:
    meta = data.get("run_metadata", {})
    ledger = data.get("coverage_ledger", {})
    units = ledger.get("units", [])
    measurements = data.get("measurements", {})
    workflows = measurements.get("workflows", [])
    all_findings = data.get("findings", [])
    observations = data.get("observations", [])

    machine = meta.get("machine", {})
    method = meta.get("method", {})
    runtime = meta.get("runtime", {})

    lines: List[str] = [
        "# Targeted Profiling Audit Report",
        "",
        "## 1. Targeted Scope",
        f"- **Repository Target**: `{meta.get('repo_name', 'target')}`",
        f"- **In-Scope Units Analyzed**: {len(units)} units",
        f"- **Workflows Evaluated**: {len(workflows)}",
        f"- **Scope Criteria**: {meta.get('scope_notes', 'Targeted functions specified by user request')}",
        "",
        "## 2. Methodology, Provenance, and Machine",
        f"- **Host Machine**: {machine.get('os', 'Unknown')} | CPU: {machine.get('cpu', 'Unknown')} | RAM: {machine.get('memory', 'Unknown')}",
        f"- **Runtime & Engine**: {runtime.get('language', 'Unknown')} {runtime.get('framework', '')} ({runtime.get('versions', 'n/a')})",
        f"- **Method & Driver**: {method.get('profiler', 'In-process profiling')} via `{method.get('driver', 'Driver')}`",
        f"- **Iterations**: {meta.get('repetitions', 5)} measured iterations ({meta.get('warmups', 1)} warmups discarded)",
        "",
        "## 3. Function Measurements and Provenance",
        "| Unit ID | Function / Operation | Location | Calls | Owned (ms) | Total (ms) | Audit Share (%) | Status |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for u in units:
        metrics = u.get("metrics", {})
        calls = metrics.get("calls", 0)
        owned_ms = metrics.get("owned_ms", metrics.get("self_ms", 0.0))
        total_ms = metrics.get("total_ms", 0.0)
        share = metrics.get("audit_share_pct", metrics.get("self_share_pct", 0.0))
        status = u.get("status", "pending")
        loc = f"`{u.get('file')}:{u.get('line')}`"
        lines.append(f"| `{u.get('id')}` | `{u.get('name')}` | {loc} | {calls} | {owned_ms:.2f} | {total_ms:.2f} | {share:.2f}% | `{status}` |")

    lines.extend([
        "",
        "## 4. Call Hierarchy Breakdown",
    ])
    for wf in workflows:
        lines.append(f"### Workflow: `{wf.get('id')}` ({wf.get('name')})")
        lines.append(f"- **Workflow Duration (Mean)**: {wf.get('summary', {}).get('mean', 0.0):.2f} ms")
        lines.append("- **Call Tree Structure**:")

        def _print_node(node: Dict[str, Any], depth: int = 0):
            indent = "  " * depth
            src = node.get("source", "measured")
            s_ms = node.get("self_ms", 0.0)
            t_ms = node.get("total_ms", 0.0)
            c = node.get("calls", 1)
            lines.append(f"{indent}- `{node.get('name')}`: total {t_ms:.2f}ms, self {s_ms:.2f}ms ({c} calls) [{src}]")
            for ch in node.get("children", []):
                _print_node(ch, depth + 1)

        if "call_tree" in wf:
            _print_node(wf["call_tree"])
        lines.append("")

    lines.extend([
        "## 5. Performance Findings and Diagnostics",
    ])
    if not all_findings:
        lines.append("No performance bottlenecks identified in targeted scope.")
    else:
        for idx, f in enumerate(all_findings, 1):
            fid = f.get("id")
            title = f.get("title")
            cat = f.get("category")
            st = f.get("status", "verified")
            metrics = f.get("metrics", {})
            lines.extend([
                f"### {idx}. [{st.upper()}] {fid}: {title}",
                f"- **Category**: `{cat}` | **Self Time**: {metrics.get('self_ms', 0.0):.2f} ms ({metrics.get('share_of_workflow_pct', 0.0):.2f}%)",
                f"- **Root Cause**: {f.get('root_cause')}",
                f"- **Reproduction**: {f.get('reproduction', 'Targeted execution benchmark')}",
                "",
            ])

    if observations:
        lines.append("## 6. Static Observations")
        for idx, obs in enumerate(observations, 1):
            lines.append(f"- **{obs.get('id')}**: {obs.get('title')} ({obs.get('rationale')})")
        lines.append("")

    lines.extend([
        "## 7. Recommendations and Remediation",
    ])
    for f in all_findings:
        fix = f.get("fix", {})
        basis = "Projection" if fix.get("expected_result_basis") == "measured_baseline_projection" else "Rule of Thumb"
        lines.extend([
            f"### Fix for {f.get('id')}: {fix.get('summary')}",
            f"- **Steps**:",
        ])
        for s in fix.get("steps", []):
            lines.append(f"  - {s}")
        lines.extend([
            f"- **Prerequisites**: {', '.join(fix.get('prerequisites', [])) or 'None'}",
            f"- **Risks**: {', '.join(fix.get('risks', [])) or 'None'}",
            f"- **Projected Latency Impact ({basis})**: {fix.get('expected_result')}",
            f"- **Validation Method**: {fix.get('validation_method', 'Re-run targeted profiler')}",
            "",
        ])

    lines.extend([
        "## 8. Measurement Limitations",
    ])
    limitations = meta.get("limitations", [])
    if limitations:
        for lim in limitations:
            lines.append(f"- {lim}")
    else:
        lines.append("- Targeted profiling scope evaluated strictly on isolated benchmark execution.")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate or verify profile-audit Markdown reports from canonical JSON artifacts or dashboard HTML."
    )
    parser.add_argument("target", nargs="?", default=None, help="Path to run directory (.work/ or parent output dir) or dashboard HTML file.")
    parser.add_argument("--data", default=None, help="Path to run directory (.work/ or parent output dir) or dashboard HTML file.")
    parser.add_argument(
        "--mode",
        choices=["full", "targeted"],
        default="full",
        help="Audit mode (full generates FINDINGS.md and FIXES.md; targeted generates PROFILING-AUDIT.md).",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Output directory where Markdown files will be written (defaults to target directory).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check mode: verifies existing Markdown files match canonical data without modifying them (exits 1 on drift).",
    )

    args = parser.parse_args(argv)

    target_arg = args.target or args.data
    if not target_arg:
        sys.stderr.write("Target path must be specified positionally or via --data\n")
        return 2

    target_path = Path(target_arg).resolve()
    if not target_path.exists():
        sys.stderr.write(f"Target path does not exist: {target_path}\n")
        return 2

    try:
        canonical_data = load_canonical_data(target_path)
    except Exception as exc:
        sys.stderr.write(f"Failed to load canonical data from {target_path}: {exc}\n")
        return 2

    out_dir = Path(args.out_dir).resolve() if args.out_dir else (target_path if target_path.is_dir() else target_path.parent)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.mode == "targeted":
        expected_content = generate_targeted_audit_md(canonical_data)
        file_path = out_dir / "PROFILING-AUDIT.md"

        if args.check:
            if not file_path.exists():
                sys.stderr.write(f"Check failed: {file_path.name} does not exist in {out_dir}\n")
                return 1
            with open(file_path, "r", encoding="utf-8") as f:
                actual = f.read()
            if actual.strip() != expected_content.strip():
                sys.stderr.write(f"Check failed: {file_path.name} content drifts from canonical data\n")
                diff = difflib.unified_diff(
                    actual.splitlines(), expected_content.splitlines(),
                    fromfile="existing", tofile="canonical"
                )
                for line in list(diff)[:30]:
                    sys.stderr.write(f"  {line}\n")
                return 1
            print(f"Check passed: {file_path.name} matches canonical data.")
            return 0
        else:
            with open(file_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(expected_content)
            print(f"Successfully generated {file_path}")
            return 0

    else:
        # Full mode: FINDINGS.md and FIXES.md
        expected_findings = generate_findings_md(canonical_data)
        expected_fixes = generate_fixes_md(canonical_data)

        findings_path = out_dir / "FINDINGS.md"
        fixes_path = out_dir / "FIXES.md"

        if args.check:
            drift = False
            for path, expected in [(findings_path, expected_findings), (fixes_path, expected_fixes)]:
                if not path.exists():
                    sys.stderr.write(f"Check failed: {path.name} does not exist in {out_dir}\n")
                    drift = True
                    continue
                with open(path, "r", encoding="utf-8") as f:
                    actual = f.read()
                if actual.strip() != expected.strip():
                    sys.stderr.write(f"Check failed: {path.name} content drifts from canonical data\n")
                    diff = difflib.unified_diff(
                        actual.splitlines(), expected.splitlines(),
                        fromfile="existing", tofile="canonical"
                    )
                    for line in list(diff)[:30]:
                        sys.stderr.write(f"  {line}\n")
                    drift = True
            if drift:
                return 1
            print("Check passed: FINDINGS.md and FIXES.md match canonical data.")
            return 0
        else:
            with open(findings_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(expected_findings)
            with open(fixes_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(expected_fixes)
            print(f"Successfully generated {findings_path.name} and {fixes_path.name} in {out_dir}")
            return 0


if __name__ == "__main__":
    sys.exit(main())
