#!/usr/bin/env python3
"""
classify.py - Deterministic coverage ledger classification and metric calculation.

Conforms to Python 3.9+ standard library only. Strictly NO third-party imports.
Reads measurements.json and coverage-ledger.json, computes per-unit:
- self_ms: raw node self time (kept for drawer inspection)
- owned_ms: unit's total_ms minus total_ms of nearest descendant nodes that are ledger units
- total_ms: outermost occurrence per path (no recursion double-counting)
- calls: total invocations
- audit_total_ms: sum of every workflow's root total_ms (multiplied by optional weight)
- audit_share_pct: owned_ms (summed over workflows) / audit_total_ms * 100
- workflow_share_pct: owned_ms in workflow / workflow root total_ms * 100
- per_workflow: breakdown per workflow
- Deterministically assigns status:
  - hot: audit_share_pct >= 10.0 and owned_ms >= 5.0
  - warm: audit_share_pct >= 1.0 and owned_ms >= 1.0
  - cold: otherwise (if executed)
  - not_exercised / insufficient_data: if calls == 0
Guarantees ledger free text (status_reason, note) contains no numbers followed by '%' or 'ms'.
"""

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

HOT_AUDIT_SHARE_PCT = 10.0
HOT_OWNED_MS = 5.0
WARM_AUDIT_SHARE_PCT = 1.0
WARM_OWNED_MS = 1.0


def sanitize_text(text: Optional[str]) -> Optional[str]:
    """Removes any numbers followed by '%' or 'ms' from ledger free text."""
    if not text:
        return text
    # Replace e.g. "12.5%", "200ms", "50 ms"
    cleaned = re.sub(r'\b\d+(?:\.\d+)?\s*(?:%|ms\b)', '[redacted]', text)
    return cleaned


def get_node_interval(node: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """Extracts (start, end) time interval in milliseconds if available."""
    if not isinstance(node, dict):
        return None
    if "start_ms" in node and "end_ms" in node:
        return (float(node["start_ms"]), float(node["end_ms"]))
    if "start_ns" in node and "end_ns" in node:
        return (float(node["start_ns"]) / 1e6, float(node["end_ns"]) / 1e6)
    if "start_time" in node and "end_time" in node:
        return (float(node["start_time"]), float(node["end_time"]))
    if "start" in node and "end" in node:
        return (float(node["start"]), float(node["end"]))
    if "offset_ms" in node and "total_ms" in node:
        off = float(node["offset_ms"])
        return (off, off + float(node["total_ms"]))
    if "interval" in node and isinstance(node["interval"], (list, tuple)) and len(node["interval"]) == 2:
        return (float(node["interval"][0]), float(node["interval"][1]))
    return None


def compute_interval_union(intervals: List[Tuple[float, float]]) -> float:
    """Computes total duration of the union of intervals."""
    if not intervals:
        return 0.0
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


def calculate_descendant_deduction(
    node: Dict[str, Any],
    nearest_desc: List[Dict[str, Any]],
) -> Tuple[float, bool]:
    """
    Calculates the time deduction for nearest descendant ledger units.
    Returns (deduction_ms, is_insufficient).
    When correlated intervals exist, uses the union of descendant intervals.
    When concurrent descendants lack interval evidence, marks accounting as insufficient.
    """
    if not nearest_desc:
        return 0.0, False

    is_concurrent = (
        node.get("children_execution") == "concurrent"
        or node.get("overlapping_child_intervals") is True
        or node.get("is_concurrent") is True
        or any(d.get("concurrent") is True for d in nearest_desc)
    )

    intervals = [get_node_interval(d) for d in nearest_desc]
    all_have_intervals = all(iv is not None for iv in intervals)

    if all_have_intervals:
        valid_intervals = [iv for iv in intervals if iv is not None]
        union_span = compute_interval_union(valid_intervals)
        return union_span, False

    if is_concurrent:
        # Concurrent execution without interval evidence cannot be precisely owned
        return 0.0, True

    # Sequential execution
    desc_total = sum(float(d.get("total_ms", 0.0)) for d in nearest_desc)
    return desc_total, False


def find_nearest_descendant_units(
    node: Dict[str, Any],
    ledger_unit_ids: Set[str],
) -> List[Dict[str, Any]]:
    """
    Finds the nearest descendant nodes that have a unit_id belonging to ledger_unit_ids.
    Does not search beneath an encountered ledger unit descendant.
    """
    descendants: List[Dict[str, Any]] = []
    for child in node.get("children", []):
        if not isinstance(child, dict):
            continue
        c_uid = child.get("unit_id")
        if c_uid and c_uid in ledger_unit_ids:
            descendants.append(child)
        else:
            descendants.extend(find_nearest_descendant_units(child, ledger_unit_ids))
    return descendants


def traverse_workflow_call_tree(
    node: Dict[str, Any],
    ledger_unit_ids: Set[str],
    ancestor_unit_ids: Set[str],
    wf_unit_stats: Dict[str, Dict[str, Any]],
) -> None:
    """
    Traverses call tree accumulating self_ms, owned_ms, total_ms, and calls.
    - self_ms: raw node self time (exclusive execution)
    - owned_ms: node total_ms minus nearest descendant ledger units deduction
                (using interval union for concurrent descendants)
    - total_ms: outermost occurrence per path (recursive calls excluded from total_ms)
    """
    if not isinstance(node, dict):
        return

    uid = node.get("unit_id")
    calls = node.get("calls", 0)
    self_ms = float(node.get("self_ms", 0.0))
    total_ms = float(node.get("total_ms", 0.0))

    if uid and uid in ledger_unit_ids:
        if uid not in wf_unit_stats:
            wf_unit_stats[uid] = {
                "self_ms": 0.0,
                "owned_ms": 0.0,
                "total_ms": 0.0,
                "calls": 0,
                "accounting_insufficient": False,
            }

        # Calculate owned_ms: total_ms minus descendant deduction
        nearest_desc = find_nearest_descendant_units(node, ledger_unit_ids)
        deduction, is_insufficient = calculate_descendant_deduction(node, nearest_desc)
        if is_insufficient:
            wf_unit_stats[uid]["accounting_insufficient"] = True
        owned_ms = max(0.0, total_ms - deduction)

        wf_unit_stats[uid]["self_ms"] += self_ms
        wf_unit_stats[uid]["owned_ms"] += owned_ms
        wf_unit_stats[uid]["calls"] += int(calls) if isinstance(calls, (int, float)) else 1

        # Outermost occurrence per path gets counted toward total_ms
        if uid not in ancestor_unit_ids:
            wf_unit_stats[uid]["total_ms"] += total_ms

        new_ancestors = ancestor_unit_ids | {uid}
    else:
        new_ancestors = ancestor_unit_ids

    for child in node.get("children", []):
        traverse_workflow_call_tree(child, ledger_unit_ids, new_ancestors, wf_unit_stats)


def classify_ledger(
    ledger_data: Dict[str, Any],
    measurements_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Computes metrics and statuses for all units in ledger_data from measurements_data.
    Returns the updated ledger_data dictionary.
    """
    updated_ledger = dict(ledger_data)
    units: List[Dict[str, Any]] = updated_ledger.get("units", [])
    workflows: List[Dict[str, Any]] = measurements_data.get("workflows", [])

    ledger_unit_ids = {u.get("id") for u in units if isinstance(u, dict) and u.get("id")}

    # Step 1: Compute workflow durations, weights, and total audit duration
    wf_durations: Dict[str, float] = {}
    wf_weights: Dict[str, float] = {}
    wf_unit_stats: Dict[str, Dict[str, Dict[str, Any]]] = {}
    audit_total_ms = 0.0

    for wf in workflows:
        if not isinstance(wf, dict):
            continue
        w_id = wf.get("id") or wf.get("workflow_id")
        if not w_id:
            continue
        call_tree = wf.get("call_tree", {})
        wf_dur = float(call_tree.get("total_ms", 0.0))
        weight = float(wf.get("weight", 1.0))

        wf_durations[w_id] = wf_dur
        wf_weights[w_id] = weight
        audit_total_ms += wf_dur * weight

        wf_unit_stats[w_id] = {}
        traverse_workflow_call_tree(call_tree, ledger_unit_ids, set(), wf_unit_stats[w_id])

    # Step 2: Compute per unit metrics and status
    for unit in units:
        if not isinstance(unit, dict):
            continue
        uid = unit.get("id")
        if not uid:
            continue

        per_wf_list: List[Dict[str, Any]] = []
        highest_self_share_pct = 0.0
        highest_owned_share_pct = 0.0
        highest_inclusive_share_pct = 0.0
        sum_self_ms = 0.0
        sum_owned_ms = 0.0
        sum_total_ms = 0.0
        sum_calls = 0
        executed_wfs: List[str] = []
        has_insufficient_accounting = False

        for w_id, dur in wf_durations.items():
            if uid in wf_unit_stats[w_id]:
                u_stats = wf_unit_stats[w_id][uid]
                s_ms = u_stats["self_ms"]
                o_ms = u_stats["owned_ms"]
                t_ms = u_stats["total_ms"]
                c_calls = u_stats["calls"]
                if u_stats.get("accounting_insufficient"):
                    has_insufficient_accounting = True

                self_share = (s_ms / dur * 100.0) if dur > 0 else 0.0
                owned_share = (o_ms / dur * 100.0) if dur > 0 else 0.0
                inclusive_share = (t_ms / dur * 100.0) if dur > 0 else 0.0

                highest_self_share_pct = max(highest_self_share_pct, self_share)
                highest_owned_share_pct = max(highest_owned_share_pct, owned_share)
                highest_inclusive_share_pct = max(highest_inclusive_share_pct, inclusive_share)

                sum_self_ms += s_ms
                sum_owned_ms += o_ms
                sum_total_ms += t_ms
                sum_calls += c_calls
                executed_wfs.append(w_id)

                per_wf_list.append({
                    "workflow_id": w_id,
                    "self_ms": round(s_ms, 4),
                    "owned_ms": round(o_ms, 4),
                    "total_ms": round(t_ms, 4),
                    "self_share_pct": round(self_share, 4),
                    "owned_share_pct": round(owned_share, 4),
                    "workflow_share_pct": round(owned_share, 4),
                    "inclusive_share_pct": round(inclusive_share, 4),
                    "total_share_pct": round(inclusive_share, 4),
                })

        audit_share_pct = (sum_owned_ms / audit_total_ms * 100.0) if audit_total_ms > 0 else 0.0

        # Set calculated metrics
        unit["metrics"] = {
            "self_ms": round(sum_self_ms, 4),
            "owned_ms": round(sum_owned_ms, 4),
            "total_ms": round(sum_total_ms, 4),
            "calls": sum_calls,
            "audit_share_pct": round(audit_share_pct, 4),
            "self_share_pct": round(highest_self_share_pct, 4),
            "owned_share_pct": round(highest_owned_share_pct, 4),
            "workflow_share_pct": round(highest_owned_share_pct, 4),
            "inclusive_share_pct": round(highest_inclusive_share_pct, 4),
            "total_share_pct": round(highest_inclusive_share_pct, 4),
            "per_workflow": per_wf_list,
            "aggregate_ms": round(sum_owned_ms, 4),
        }
        unit["workflows"] = executed_wfs

        # Status assignment
        cur_status = unit.get("status")
        if has_insufficient_accounting:
            unit["status"] = "insufficient_data"
            unit["status_reason"] = "Ownership accounting insufficient due to concurrent descendants without interval evidence"
        elif sum_calls == 0:
            if cur_status == "insufficient_data":
                unit["status"] = "insufficient_data"
                if not unit.get("status_reason"):
                    unit["status_reason"] = "Execution observed but reliable function-level timing could not be obtained"
            else:
                unit["status"] = "not_exercised"
                unit["status_reason"] = "Not reached by any measured workflow"
        else:
            if audit_share_pct >= HOT_AUDIT_SHARE_PCT and sum_owned_ms >= HOT_OWNED_MS:
                unit["status"] = "hot"
                unit["status_reason"] = "Owned execution time and audit share meet hot threshold"
            elif audit_share_pct >= WARM_AUDIT_SHARE_PCT and sum_owned_ms >= WARM_OWNED_MS:
                unit["status"] = "warm"
                unit["status_reason"] = "Owned execution time and audit share meet warm threshold"
            else:
                unit["status"] = "cold"
                unit["status_reason"] = "Owned execution time or audit share below warm threshold"

        # Sanitize free text to guarantee no numbers followed by % or ms
        if "status_reason" in unit and unit["status_reason"]:
            unit["status_reason"] = sanitize_text(unit["status_reason"])
        if "note" in unit and unit["note"]:
            unit["note"] = sanitize_text(unit["note"])

    return updated_ledger


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Classify coverage ledger units and compute metrics from measurements."
    )
    parser.add_argument("target_dir", nargs="?", help="Path to run or work directory containing artifacts.")
    parser.add_argument("--ledger", help="Explicit path to coverage-ledger.json.")
    parser.add_argument("--measurements", help="Explicit path to measurements.json.")
    parser.add_argument("--out", help="Optional output path for updated coverage ledger.")

    args = parser.parse_args(argv)

    if args.ledger and args.measurements:
        ledger_path = Path(args.ledger)
        measurements_path = Path(args.measurements)
    elif args.target_dir:
        base = Path(args.target_dir)
        ledger_path = base / "coverage-ledger.json"
        measurements_path = base / "measurements.json"
    else:
        parser.error("Must specify target_dir or both --ledger and --measurements")

    if not ledger_path.exists():
        sys.stderr.write(f"Error: coverage ledger not found at {ledger_path}\n")
        return 1
    if not measurements_path.exists():
        sys.stderr.write(f"Error: measurements not found at {measurements_path}\n")
        return 1

    with open(ledger_path, "r", encoding="utf-8") as f:
        ledger_data = json.load(f)
    with open(measurements_path, "r", encoding="utf-8") as f:
        measurements_data = json.load(f)

    updated = classify_ledger(ledger_data, measurements_data)

    out_path = Path(args.out) if args.out else ledger_path
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(updated, f, indent=2)

    print(f"Successfully classified {len(updated.get('units', []))} units in {out_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
