#!/usr/bin/env python3
"""
build_report.py - Offline, self-contained interactive HTML dashboard generator.

Conforms to Python 3.9+ standard library only. Strictly NO third-party imports.
Performs pre-flight validation on audit run artifacts, calculates aggregations
(with honest separation of verified, needs-validation, and static counts),
safely escapes and embeds all canonical data into report-template.html, and emits
a 100% self-contained offline PROFILING-DASHBOARD.html file.
"""

import argparse
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

# Ensure sibling validate.py is importable
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from validate import validate_run_dir, ValidationError, extract_data_from_html
except ImportError:
    from .validate import validate_run_dir, ValidationError, extract_data_from_html


# ---------------------------------------------------------------------------
# Safe JSON Embedding Engine
# ---------------------------------------------------------------------------

def serialize_safe_json(payload: Any) -> str:
    """
    Serializes a Python object to JSON safely embedded in HTML <script> blocks.
    Replaces:
      < with \\u003c
      > with \\u003e
      & with \\u0026
      \\u2028 with \\u2028
      \\u2029 with \\u2029
    Guarantees literal </script> or <!-- strings cannot break out of HTML
    script elements, and ECMAScript line/paragraph terminators are escaped.
    """
    raw_json = json.dumps(payload, ensure_ascii=False)
    safe_json = (
        raw_json.replace("<", "\\u003c")
                .replace(">", "\\u003e")
                .replace("&", "\\u0026")
                .replace("\u2028", "\\u2028")
                .replace("\u2029", "\\u2029")
    )
    return safe_json


# ---------------------------------------------------------------------------
# Multi-Workflow Data Aggregation
# ---------------------------------------------------------------------------

IMPACT_SORT_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


def aggregate_run_data(
    metadata: Dict[str, Any],
    ledger: Dict[str, Any],
    measurements: Dict[str, Any],
    findings: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Computes cross-workflow aggregations, coverage statistics, bottleneck rankings,
    and shared function metrics without summing latencies across distinct workflows.
    """
    units = ledger.get("units", [])
    total_units = len(units)

    # Status counts
    status_counts = {
        "hot": 0,
        "warm": 0,
        "cold": 0,
        "not_exercised": 0,
        "insufficient_data": 0,
        "pending": 0,
    }
    for u in units:
        status = u.get("status", "pending")
        if status in status_counts:
            status_counts[status] += 1
        else:
            status_counts["pending"] += 1

    exercised_count = status_counts["hot"] + status_counts["warm"] + status_counts["cold"]
    coverage_pct = round((exercised_count / total_units * 100.0), 2) if total_units > 0 else 0.0

    workflows = measurements.get("workflows", [])
    total_workflows = len(workflows)

    raw_findings = findings.get("findings", [])
    total_findings = len(raw_findings)
    verified_count = 0
    needs_validation_count = 0

    for f in raw_findings:
        status = f.get("status")
        if status == "verified":
            verified_count += 1
        elif status == "needs_validation":
            needs_validation_count += 1

    observations = findings.get("observations", [])
    observations_count = len(observations)

    # Rank top bottlenecks: only verified bottlenecks, sorted by impact priority then total impact
    def _finding_rank_key(item: Dict[str, Any]) -> Tuple[int, float, float]:
        impact = item.get("impact") or "low"
        impact_prio = IMPACT_SORT_ORDER.get(impact, 99)
        metrics = item.get("metrics", {})
        total_ms = float(metrics.get("total_ms", 0.0))
        calls = float(metrics.get("calls", 1))
        # Negative for descending sort
        return (impact_prio, -(total_ms * calls), -total_ms)

    top_bottlenecks = sorted(
        [f for f in raw_findings if f.get("status") == "verified"],
        key=_finding_rank_key,
    )

    # Shared functions aggregation across workflows
    unit_stats: Dict[str, Dict[str, Any]] = {}
    for u in units:
        uid = u.get("id")
        if uid:
            unit_stats[uid] = {
                "unit_id": uid,
                "name": u.get("name", ""),
                "kind": u.get("kind", "function"),
                "file": u.get("file", ""),
                "line": u.get("line", 0),
                "status": u.get("status", "pending"),
                "total_calls": 0,
                "total_self_ms": 0.0,
                "total_total_ms": 0.0,
                "workflow_appearances": [],
            }

    for wf in workflows:
        wf_id = wf.get("id", "")

        def _walk_tree(node: Any) -> None:
            if not isinstance(node, dict):
                return
            uid = node.get("unit_id")
            if uid and uid in unit_stats:
                st = unit_stats[uid]
                calls = node.get("calls", 0)
                self_ms = node.get("self_ms", 0.0)
                total_ms = node.get("total_ms", 0.0)
                if isinstance(calls, (int, float)):
                    st["total_calls"] += int(calls)
                if isinstance(self_ms, (int, float)):
                    st["total_self_ms"] += float(self_ms)
                if isinstance(total_ms, (int, float)):
                    st["total_total_ms"] += float(total_ms)
                if wf_id and wf_id not in st["workflow_appearances"]:
                    st["workflow_appearances"].append(wf_id)
            for child in node.get("children", []):
                _walk_tree(child)

        if "call_tree" in wf:
            _walk_tree(wf["call_tree"])

    # Round aggregated floats
    for st in unit_stats.values():
        st["total_self_ms"] = round(st["total_self_ms"], 4)
        st["total_total_ms"] = round(st["total_total_ms"], 4)

    summary = {
        "total_units": total_units,
        "status_counts": status_counts,
        "coverage_pct": coverage_pct,
        "total_workflows": total_workflows,
        "total_findings": total_findings,
        "verified_findings_count": verified_count,
        "needs_validation_count": needs_validation_count,
        "observations_count": observations_count,
        "top_bottlenecks": top_bottlenecks,
        "shared_functions": list(unit_stats.values()),
    }

    return summary


# ---------------------------------------------------------------------------
# Report Builder Engine
# ---------------------------------------------------------------------------

def build_report(
    run_dir: Union[str, Path],
    out_file: Optional[Union[str, Path]] = None,
    template_file: Optional[Union[str, Path]] = None,
    schemas_dir: Optional[Union[str, Path]] = None,
    skip_validation: bool = False,
    strict_validation: bool = False,
) -> Path:
    """
    Compiles run artifacts into a single offline PROFILING-DASHBOARD.html file.

    Args:
        run_dir: Path to directory containing audit JSON artifacts (or .work/ subdirectory).
        out_file: Target output HTML path (defaults to <run-dir>/PROFILING-DASHBOARD.html).
        template_file: Custom HTML template path (defaults to assets/report-template.html).
        schemas_dir: Optional path to custom schemas directory for pre-flight validation.
        skip_validation: If True, bypasses pre-flight validation.
        strict_validation: If True, executes pre-flight validation in strict mode.

    Returns:
        Path to generated HTML file.
    """
    run_path = Path(run_dir).resolve()
    if not run_path.exists() or not run_path.is_dir():
        raise FileNotFoundError(f"Run directory not found or is not a directory: {run_path}")

    # Support .work/ subdirectory lifecycle
    work_dir = run_path / ".work" if (run_path / ".work").is_dir() else run_path

    # Determine output destination (default PROFILING-DASHBOARD.html)
    if out_file is None:
        target_out = run_path / "PROFILING-DASHBOARD.html"
    else:
        target_out = Path(out_file).resolve()

    # Determine template location
    if template_file is None:
        tpl_path = Path(__file__).resolve().parent.parent / "assets" / "report-template.html"
    else:
        tpl_path = Path(template_file).resolve()

    if not tpl_path.exists() or not tpl_path.is_file():
        raise FileNotFoundError(f"Report template not found: {tpl_path}")

    # Step 1: Pre-flight validation
    if not skip_validation:
        is_valid, errors, _ = validate_run_dir(
            work_dir,
            strict=strict_validation,
            schemas_dir=schemas_dir,
        )
        if not is_valid:
            sys.stderr.write(
                f"Validation pre-flight failed: {len(errors)} error(s) in '{work_dir}'. "
                f"Aborting report build.\n"
            )
            for err in errors[:10]:
                sys.stderr.write(f"  {err}\n")
            if len(errors) > 10:
                sys.stderr.write(f"  ... and {len(errors) - 10} more error(s).\n")
            raise ValueError(f"Run artifacts in '{work_dir}' failed pre-flight validation ({len(errors)} errors)")

    # Step 2: Read run artifacts
    if not (work_dir / "run-metadata.json").exists() and (run_path / "PROFILING-DASHBOARD.html").is_file():
        embedded, err = extract_data_from_html(run_path / "PROFILING-DASHBOARD.html")
        if err is not None:
            raise ValueError(f"Failed to extract embedded data from {run_path / 'PROFILING-DASHBOARD.html'}: {err}")
        metadata = embedded.get("run_metadata", {})
        ledger = embedded.get("coverage_ledger", {})
        measurements = embedded.get("measurements", {})
        findings = {
            "schema_version": embedded.get("schema_version", "1.0.0"),
            "findings": embedded.get("findings", []),
            "observations": embedded.get("observations", []),
            "reviewed_not_bottleneck": embedded.get("reviewed_not_bottleneck", []),
        }
        architecture = embedded.get("architecture", {})
    else:
        artifact_files = {
            "metadata": work_dir / "run-metadata.json",
            "ledger": work_dir / "coverage-ledger.json",
            "measurements": work_dir / "measurements.json",
            "findings": work_dir / "findings.json",
        }

        raw_data: Dict[str, Any] = {}
        for key, path in artifact_files.items():
            if not path.exists():
                raise FileNotFoundError(f"Required artifact '{path.name}' missing from '{work_dir}'")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw_data[key] = json.load(f)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Malformed JSON in '{path.name}': {exc}")

        metadata = raw_data["metadata"]
        ledger = raw_data["ledger"]
        measurements = raw_data["measurements"]
        findings = raw_data["findings"]

        arch_file = work_dir / "architecture.json"
        architecture = {}
        if arch_file.exists():
            try:
                with open(arch_file, "r", encoding="utf-8") as f:
                    architecture = json.load(f)
            except Exception:
                architecture = {}

    # Step 3: Compute aggregations
    summary = aggregate_run_data(metadata, ledger, measurements, findings)

    # Step 4: Package full report data payload adhering to report-data.schema.json
    raw_findings_list = findings.get("findings", []) if isinstance(findings, dict) else []
    raw_obs_list = findings.get("observations", []) if isinstance(findings, dict) else []
    raw_rev_list = findings.get("reviewed_not_bottleneck", []) if isinstance(findings, dict) else []

    report_payload = {
        "schema_version": "1.0.0",
        "run_metadata": metadata,
        "architecture": architecture,
        "coverage_ledger": ledger,
        "measurements": measurements,
        "findings": raw_findings_list,
        "observations": raw_obs_list,
        "reviewed_not_bottleneck": raw_rev_list,
        "summary": summary,
        # backward-compatibility aliases
        "metadata": metadata,
        "ledger": ledger,
    }

    # Step 5: Safely serialize JSON
    safe_embedded_json = serialize_safe_json(report_payload)

    # Step 6: Read template and substitute placeholder
    try:
        with open(tpl_path, "r", encoding="utf-8") as f:
            template_content = f.read()
    except Exception as exc:
        raise IOError(f"Failed to read report template '{tpl_path}': {exc}")

    placeholder = "__REPORT_DATA__"
    if placeholder not in template_content:
        raise ValueError(
            f"Report template '{tpl_path.name}' does not contain required placeholder token '{placeholder}'"
        )

    rendered_html = template_content.replace(placeholder, safe_embedded_json)

    # Step 7: Verify zero external network references
    external_url_pattern = re.compile(r'(?:src|href|url)\s*=\s*["\']https?://', re.IGNORECASE)
    match = external_url_pattern.search(rendered_html)
    if match:
        sys.stderr.write(
            f"Warning: Rendered HTML contains external network URL: '{match.group(0)}'. "
            f"Report self-containment check failed.\n"
        )

    # Step 8: Write to target file with explicit LF line endings
    target_out.parent.mkdir(parents=True, exist_ok=True)
    rendered_html = rendered_html.replace("\r\n", "\n")
    try:
        with open(target_out, "w", encoding="utf-8", newline="\n") as f:
            f.write(rendered_html)
    except Exception as exc:
        raise IOError(f"Failed to write output report to '{target_out}': {exc}")

    return target_out


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compile profile-audit artifacts into a self-contained offline HTML dashboard."
    )
    parser.add_argument("run_dir", help="Path to profile-audit output run directory containing artifacts.")
    parser.add_argument(
        "--out",
        default=None,
        help="Optional destination path for generated HTML report (default: <run-dir>/PROFILING-DASHBOARD.html).",
    )
    parser.add_argument(
        "--template",
        default=None,
        help="Optional path to custom report-template.html file.",
    )
    parser.add_argument(
        "--schemas-dir",
        default=None,
        help="Optional path to custom directory containing JSON schema files.",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip pre-flight validation check before compilation.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Enforce strict validation mode during pre-flight check.",
    )

    args = parser.parse_args(argv)

    try:
        out_path = build_report(
            args.run_dir,
            out_file=args.out,
            template_file=args.template,
            schemas_dir=args.schemas_dir,
            skip_validation=args.skip_validation,
            strict_validation=args.strict,
        )
        print(f"Dashboard compiled successfully: {out_path}")
        return 0
    except ValueError as val_err:
        sys.stderr.write(f"Build error: {val_err}\n")
        return 1
    except FileNotFoundError as fnf_err:
        sys.stderr.write(f"Operational error: {fnf_err}\n")
        return 2
    except Exception as exc:
        sys.stderr.write(f"Operational error: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
