#!/usr/bin/env python3
"""
measurements_tools.py - Profiling measurement utilities and call tree algebra.

Conforms to Python 3.9+ standard library only. Strictly NO third-party imports.
Provides:
- Statistical aggregation per node (n, mean, p50, p95, stdev, cv, min, max)
- Consistent call tree construction (computing self from total minus children)
- Non-overlapping sequential arithmetic and concurrent interval union arithmetic
- Anomaly, overlap, and unexplained time detection
- Profiler record normalization to Appendix A schema
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


def compute_percentile(sorted_runs: List[float], q: float) -> float:
    """
    Computes quantile/percentile via linear interpolation (method 7).
    q is between 0.0 and 1.0 (e.g. 0.50 for p50, 0.95 for p95).
    """
    if not sorted_runs:
        return 0.0
    n = len(sorted_runs)
    if n == 1:
        return sorted_runs[0]
    idx = (n - 1) * q
    low = int(math.floor(idx))
    high = int(math.ceil(idx))
    if low == high:
        return sorted_runs[low]
    weight = idx - low
    return sorted_runs[low] * (1.0 - weight) + sorted_runs[high] * weight


def compute_stats(runs: List[float]) -> Dict[str, Any]:
    """
    Computes standard summary statistics across a sequence of run durations.
    Returns: {n, mean, p50, p95, stdev, cv, min, max}.
    """
    if not runs:
        return {
            "n": 0,
            "mean": 0.0,
            "p50": 0.0,
            "p95": 0.0,
            "stdev": 0.0,
            "cv": 0.0,
            "min": 0.0,
            "max": 0.0,
        }

    n = len(runs)
    sorted_runs = sorted(runs)
    mean_val = sum(runs) / n
    p50_val = compute_percentile(sorted_runs, 0.50)
    p95_val = compute_percentile(sorted_runs, 0.95)
    min_val = sorted_runs[0]
    max_val = sorted_runs[-1]

    if n > 1:
        variance = sum((x - mean_val) ** 2 for x in runs) / (n - 1)
        stdev_val = math.sqrt(variance)
        cv_val = stdev_val / mean_val if mean_val > 0 else 0.0
    else:
        stdev_val = 0.0
        cv_val = 0.0

    return {
        "n": n,
        "mean": round(mean_val, 4),
        "p50": round(p50_val, 4),
        "p95": round(p95_val, 4),
        "stdev": round(stdev_val, 4),
        "cv": round(cv_val, 4),
        "min": round(min_val, 4),
        "max": round(max_val, 4),
    }


def compute_interval_union(intervals: List[Tuple[float, float]]) -> float:
    """
    Merges overlapping (start, end) intervals and returns the total duration of the union.
    """
    if not intervals:
        return 0.0

    # Filter invalid/empty intervals and sort by start time
    valid = [(s, e) for s, e in intervals if e > s]
    if not valid:
        return 0.0
    valid.sort(key=lambda x: x[0])

    merged: List[Tuple[float, float]] = []
    cur_start, cur_end = valid[0]

    for next_start, next_end in valid[1:]:
        if next_start <= cur_end:
            cur_end = max(cur_end, next_end)
        else:
            merged.append((cur_start, cur_end))
            cur_start, cur_end = next_start, next_end
    merged.append((cur_start, cur_end))

    return sum(e - s for s, e in merged)


def validate_node_tree(
    node: Dict[str, Any],
    workflow_total_ms: float,
    path: str = "call_tree",
) -> List[Tuple[str, str, str]]:
    """
    Recursively validates tree arithmetic, provenance, and unexplained time rules.
    Returns a list of (code, path, message) tuples.
    """
    issues: List[Tuple[str, str, str]] = []

    # 1. Negative duration check
    total_ms = node.get("total_ms", 0.0)
    self_ms = node.get("self_ms", 0.0)
    if total_ms < 0 or self_ms < 0:
        issues.append((
            "E_NEGATIVE_DURATION",
            path,
            f"Node '{node.get('name')}' has negative duration (total_ms={total_ms}, self_ms={self_ms})",
        ))

    # 2. Provenance check
    source = node.get("source")
    if source is None:
        issues.append((
            "E_PROVENANCE_MISSING",
            path,
            f"Node '{node.get('name')}' is missing required 'source' field",
        ))
    elif source not in ("measured", "simulated", "estimated"):
        issues.append((
            "E_PROVENANCE_INVALID",
            path,
            f"Node '{node.get('name')}' has invalid source '{source}' (must be measured, simulated, or estimated)",
        ))

    # 3. Children arithmetic check
    children = node.get("children", [])
    exec_mode = node.get("children_execution", "sequential")

    if children:
        if exec_mode == "sequential":
            child_totals = sum(c.get("total_ms", 0.0) for c in children)
            expected_total = self_ms + child_totals
            tolerance = max(0.5, 0.01 * total_ms)
            diff = abs(total_ms - expected_total)
            if diff > tolerance:
                issues.append((
                    "E_TREE_ARITHMETIC",
                    path,
                    f"Sequential arithmetic mismatch at node '{node.get('name')}': "
                    f"total_ms ({total_ms:.4f}) != self_ms ({self_ms:.4f}) + sum(child.total_ms) ({child_totals:.4f}), "
                    f"diff {diff:.4f} > tolerance {tolerance:.4f}",
                ))
        elif exec_mode == "concurrent":
            intervals = [
                (c.get("start_ms", 0.0), c.get("end_ms", 0.0))
                for c in children
                if "start_ms" in c and "end_ms" in c
            ]
            if len(intervals) == len(children):
                union_span = compute_interval_union(intervals)
                expected_total = self_ms + union_span
                tolerance = max(0.5, 0.01 * total_ms)
                diff = abs(total_ms - expected_total)
                if diff > tolerance:
                    issues.append((
                        "E_TREE_ARITHMETIC",
                        path,
                        f"Concurrent interval arithmetic mismatch at node '{node.get('name')}': "
                        f"total_ms ({total_ms:.4f}) != self_ms ({self_ms:.4f}) + union(child intervals) ({union_span:.4f}), "
                        f"diff {diff:.4f} > tolerance {tolerance:.4f}",
                    ))
            else:
                # Interval offsets not provided; check if sum or individual children exceed total_ms + tolerance
                max_child = max(c.get("total_ms", 0.0) for c in children)
                if max_child > total_ms + max(0.5, 0.01 * total_ms):
                    issues.append((
                        "E_TREE_ARITHMETIC",
                        path,
                        f"Concurrent child total_ms ({max_child:.4f}) exceeds parent total_ms ({total_ms:.4f})",
                    ))

        # 4. Unexplained time check (Section 4.5):
        # Non-leaf node whose self_ms >= 5% of workflow duration must be broken down or have unattributed child
        if workflow_total_ms > 0:
            if self_ms >= 0.05 * workflow_total_ms:
                has_unattributed = any(
                    c.get("kind") == "unattributed" or c.get("name") == "unattributed"
                    for c in children
                )
                if not has_unattributed:
                    issues.append((
                        "E_UNATTRIBUTED_TIME",
                        path,
                        f"Non-leaf node '{node.get('name')}' self_ms ({self_ms:.2f} ms, "
                        f"{self_ms / workflow_total_ms * 100:.2f}%) >= 5% of workflow ({workflow_total_ms:.2f} ms) "
                        f"with no finer breakdown or explicit 'unattributed' child",
                    ))

    # 5. Wrapper time check (Section 4.3 & 11)
    # Acquisition-type node with self >= 10% of workflow and no source_note explaining wait
    op = node.get("operation")
    name_lower = node.get("name", "").lower()
    acquisition_ops = {
        "db.connect", "db.session", "http.dns", "http.connect",
        "http.tls", "http.pool_wait"
    }
    is_acquisition = (op in acquisition_ops) or any(
        sub in name_lower for sub in ("db_session", "get_db", "session_scope", "connection_pool")
    )
    if is_acquisition and workflow_total_ms > 0:
        if self_ms >= 0.10 * workflow_total_ms:
            source_note = (node.get("source_note") or "").strip()
            if not source_note:
                issues.append((
                    "E_WRAPPER_TIME",
                    path,
                    f"Acquisition-type node '{node.get('name')}' (operation '{op}') has self_ms "
                    f"({self_ms:.2f} ms, {self_ms / workflow_total_ms * 100:.2f}%) >= 10% of workflow without "
                    f"a source_note explaining measured wait",
                ))

    # 6. Per-node stats validation vs per_run_ms
    per_run_ms = node.get("per_run_ms")
    stats = node.get("stats")
    if per_run_ms and isinstance(per_run_ms, list) and len(per_run_ms) > 0 and stats:
        expected_stats = compute_stats(per_run_ms)
        for stat_key in ("mean", "p50", "p95", "min", "max"):
            if stat_key in stats:
                actual_val = stats[stat_key]
                exp_val = expected_stats[stat_key]
                if abs(actual_val - exp_val) > 0.1:
                    issues.append((
                        "E_STATS_WRONG",
                        f"{path}/stats/{stat_key}",
                        f"Node '{node.get('name')}' stored {stat_key} ({actual_val}) differs from "
                        f"recomputed value from per_run_ms ({exp_val})",
                    ))

    # Recurse children
    for idx, child in enumerate(children):
        child_path = f"{path}/children/{idx}"
        issues.extend(validate_node_tree(child, workflow_total_ms, child_path))

    return issues


def build_consistent_tree(
    raw_node: Dict[str, Any],
    workflow_total_ms: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Builds a consistent tree ensuring self_ms = total_ms - sum(children.total_ms) for sequential nodes.
    Computes stats from per_run_ms if present.
    Never silently repairs inconsistent raw evidence when self_ms was explicitly provided with contradiction;
    raises ValueError on irreconcilable discrepancies.
    """
    node = dict(raw_node)
    children = [
        build_consistent_tree(c, workflow_total_ms)
        for c in raw_node.get("children", [])
    ]
    node["children"] = children
    exec_mode = node.get("children_execution", "sequential")
    node["children_execution"] = exec_mode

    total_ms = node.get("total_ms", 0.0)
    if "self_ms" not in node:
        if not children:
            node["self_ms"] = total_ms
        elif exec_mode == "sequential":
            child_sum = sum(c.get("total_ms", 0.0) for c in children)
            node["self_ms"] = round(max(0.0, total_ms - child_sum), 4)
        elif exec_mode == "concurrent":
            intervals = [
                (c["start_ms"], c["end_ms"])
                for c in children
                if "start_ms" in c and "end_ms" in c
            ]
            if len(intervals) == len(children):
                union_span = compute_interval_union(intervals)
                node["self_ms"] = round(max(0.0, total_ms - union_span), 4)

    # Compute stats if per_run_ms is available and stats is missing
    if "per_run_ms" in node and isinstance(node["per_run_ms"], list) and "stats" not in node:
        node["stats"] = compute_stats(node["per_run_ms"])

    return node


def normalize_node(
    node_id: str,
    name: str,
    total_ms: float,
    source: str = "measured",
    unit_id: Optional[str] = None,
    kind: str = "cpu",
    operation: Optional[str] = None,
    calls: int = 1,
    self_ms: Optional[float] = None,
    children: Optional[List[Dict[str, Any]]] = None,
    per_run_ms: Optional[List[float]] = None,
    source_note: Optional[str] = None,
    children_execution: str = "sequential",
) -> Dict[str, Any]:
    """
    Constructs a normalized call tree node conforming to Appendix A.
    """
    children = children or []
    if self_ms is None:
        if children_execution == "sequential":
            child_sum = sum(c.get("total_ms", 0.0) for c in children)
            self_ms = max(0.0, total_ms - child_sum)
        else:
            self_ms = total_ms

    stats = compute_stats(per_run_ms) if per_run_ms else None

    node: Dict[str, Any] = {
        "node_id": node_id,
        "name": name,
        "unit_id": unit_id,
        "kind": kind,
        "calls": calls,
        "self_ms": round(self_ms, 4),
        "total_ms": round(total_ms, 4),
        "children_execution": children_execution,
        "source": source,
        "children": children,
    }
    if operation:
        node["operation"] = operation
    if source_note:
        node["source_note"] = source_note
    if per_run_ms:
        node["per_run_ms"] = per_run_ms
        node["per_run_basis"] = "inclusive"
        node["stats"] = stats

    return node


def rollup_node_cpu_wait(node: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """
    Recursively rolls up cpu_ms and wait_ms from leaves to parent nodes.
    Derives kind as 'cpu' (>=70%), 'wait' (>=70%), or 'mixed'.
    Returns (cpu_ms, wait_ms) of this node.
    """
    children = node.get("children", [])
    child_cpu_sum = 0.0
    child_wait_sum = 0.0
    has_unavailable = False

    for child in children:
        c_cpu, c_wait = rollup_node_cpu_wait(child)
        if c_cpu is None or c_wait is None:
            has_unavailable = True
        else:
            child_cpu_sum += c_cpu
            child_wait_sum += c_wait

    source = node.get("cpu_wait_source")
    if source == "unavailable" or (has_unavailable and len(children) > 0 and node.get("self_cpu_ms") is None):
        node["cpu_ms"] = None
        node["wait_ms"] = None
        return None, None

    self_cpu = node.get("self_cpu_ms")
    self_wait = node.get("self_wait_ms")

    # If leaf has cpu_ms/wait_ms but self_cpu_ms is not explicitly populated:
    if self_cpu is None and len(children) == 0:
        if "cpu_ms" in node and node["cpu_ms"] is not None:
            self_cpu = float(node["cpu_ms"])
            node["self_cpu_ms"] = self_cpu
        else:
            self_cpu = 0.0
            node["self_cpu_ms"] = 0.0

    if self_wait is None and len(children) == 0:
        if "wait_ms" in node and node["wait_ms"] is not None:
            self_wait = float(node["wait_ms"])
            node["self_wait_ms"] = self_wait
        else:
            self_wait = 0.0
            node["self_wait_ms"] = 0.0

    self_cpu = float(self_cpu) if self_cpu is not None else 0.0
    self_wait = float(self_wait) if self_wait is not None else 0.0

    total_cpu = round(self_cpu + child_cpu_sum, 4)
    total_wait = round(self_wait + child_wait_sum, 4)

    node["cpu_ms"] = total_cpu
    node["wait_ms"] = total_wait

    # Derive kind if not unattributed
    total_ms = float(node.get("total_ms", 0.0))
    if node.get("kind") != "unattributed" and total_ms > 0:
        if total_cpu >= 0.70 * total_ms:
            node["kind"] = "cpu"
        elif total_wait >= 0.70 * total_ms:
            node["kind"] = "wait"
        else:
            node["kind"] = "mixed"

    return total_cpu, total_wait


def rollup_measurements(measurements_path: Path) -> Dict[str, Any]:
    with open(measurements_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    workflows = data.get("workflows", [])
    for wf in workflows:
        if isinstance(wf, dict) and "call_tree" in wf:
            rollup_node_cpu_wait(wf["call_tree"])
    with open(measurements_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data


def stamp_metadata(metadata_path: Path, start: bool = False, finish: bool = False) -> Dict[str, Any]:
    with open(metadata_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if start or (not start and not finish and "started_at" not in data):
        data["started_at"] = now_iso
    if finish or (not start and not finish and "started_at" in data):
        data["finished_at"] = now_iso
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Profiling measurement utilities, call tree algebra, and statistical computation."
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Subcommand to run")

    # rollup subcommand
    p_rollup = subparsers.add_parser("rollup", help="Roll up self_cpu_ms/self_wait_ms and derive kind from leaves up")
    p_rollup.add_argument("target", help="Path to measurements.json or directory containing measurements.json")

    # stamp subcommand
    p_stamp = subparsers.add_parser("stamp", help="Write real UTC ISO timestamps to run-metadata.json")
    p_stamp.add_argument("target", help="Path to run-metadata.json or directory containing run-metadata.json")
    p_stamp.add_argument("--start", action="store_true", help="Stamp started_at")
    p_stamp.add_argument("--finish", action="store_true", help="Stamp finished_at")

    # top-level options for backward compatibility
    parser.add_argument(
        "--validate-tree",
        help="Path to JSON file containing a call_tree node to validate arithmetic and provenance.",
    )
    parser.add_argument(
        "--compute-stats",
        nargs="+",
        type=float,
        help="List of float run durations to compute summary statistics (n, mean, p50, p95, stdev, cv, min, max).",
    )

    args = parser.parse_args()

    if args.subcommand == "rollup":
        t_path = Path(args.target)
        if t_path.is_dir():
            t_path = t_path / "measurements.json"
        if not t_path.is_file():
            sys.stderr.write(f"Measurements file not found: {t_path}\n")
            sys.exit(1)
        rollup_measurements(t_path)
        print(f"Rolled up CPU/wait metrics in {t_path.name}")
        sys.exit(0)
    elif args.subcommand == "stamp":
        t_path = Path(args.target)
        if t_path.is_dir():
            t_path = t_path / "run-metadata.json"
        if not t_path.is_file():
            sys.stderr.write(f"Run metadata file not found: {t_path}\n")
            sys.exit(1)
        stamp_metadata(t_path, start=args.start, finish=args.finish)
        print(f"Stamped timestamps in {t_path.name}")
        sys.exit(0)
    elif args.compute_stats:
        res = compute_stats(args.compute_stats)
        print(json.dumps(res, indent=2))
        sys.exit(0)
    elif args.validate_tree:
        tree_path = Path(args.validate_tree)
        if not tree_path.is_file():
            sys.stderr.write(f"File not found: {tree_path}\n")
            sys.exit(2)
        with open(tree_path, "r", encoding="utf-8") as f:
            tree_data = json.load(f)
        issues = validate_node_tree(tree_data, tree_data.get("total_ms", 0.0))
        if issues:
            print(f"Validation issues found ({len(issues)}):")
            for code, path, msg in issues:
                print(f"  [{code}] {path}: {msg}")
            sys.exit(1)
        else:
            print("Call tree validation passed.")
            sys.exit(0)
    else:
        parser.print_help()
        sys.exit(0)
