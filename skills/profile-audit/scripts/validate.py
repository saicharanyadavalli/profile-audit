#!/usr/bin/env python3
"""
validate.py - Schema and relational consistency validator for profile-audit.

Conforms to Python 3.9+ standard library only. Strictly NO third-party imports.
Validates JSON artifacts or self-contained dashboard HTML against JSON Schema
Draft 2020-12 subset and enforces substantive cross-checks with distinct error codes.
"""

import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, NamedTuple, Optional, Set, Tuple, Union

try:
    from measurements_tools import compute_interval_union, compute_stats
except ImportError:
    from .measurements_tools import compute_interval_union, compute_stats


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

class ValidationError(NamedTuple):
    file: str
    path: str
    message: str
    rule: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "file": self.file,
            "path": self.path,
            "message": self.message,
            "rule": self.rule,
        }

    def __str__(self) -> str:
        return f"[{self.file}] {self.path}: {self.message} (rule: {self.rule})"


# ---------------------------------------------------------------------------
# Lightweight JSON Schema Draft 2020-12 Subset Validator Engine
# ---------------------------------------------------------------------------

class SchemaValidator:
    """
    Standard-library JSON Schema Draft 2020-12 subset validation engine.
    Supports: type, required, properties, additionalProperties, items, minItems,
    minLength, minimum, maximum, pattern, enum, format (date-time), and $defs/$ref.
    """

    def __init__(self, root_schema: Dict[str, Any], file_name: str = "") -> None:
        self.root_schema = root_schema
        self.file_name = file_name
        self._defs = root_schema.get("$defs", root_schema.get("definitions", {}))

    def resolve_ref(self, ref_uri: str) -> Dict[str, Any]:
        """Resolves internal JSON pointer references like #/$defs/call_tree_node."""
        if not ref_uri.startswith("#/"):
            raise ValueError(f"Only internal URI references starting with '#/' are supported: {ref_uri}")

        parts = [p.replace("~1", "/").replace("~0", "~") for p in ref_uri[2:].split("/")]
        cur = self.root_schema
        for part in parts:
            if not isinstance(cur, dict) or part not in cur:
                raise KeyError(f"Unable to resolve $ref pointer '{ref_uri}' at component '{part}'")
            cur = cur[part]
        if not isinstance(cur, dict):
            raise ValueError(f"$ref target '{ref_uri}' is not a valid schema object")
        return cur

    @staticmethod
    def _check_primitive_type(val: Any, expected_type: str) -> bool:
        if expected_type == "null":
            return val is None
        elif expected_type == "boolean":
            return isinstance(val, bool)
        elif expected_type == "integer":
            return isinstance(val, int) and not isinstance(val, bool)
        elif expected_type == "number":
            return isinstance(val, (int, float)) and not isinstance(val, bool)
        elif expected_type == "string":
            return isinstance(val, str)
        elif expected_type == "array":
            return isinstance(val, list)
        elif expected_type == "object":
            return isinstance(val, dict)
        return False

    def validate(
        self,
        instance: Any,
        schema: Optional[Dict[str, Any]] = None,
        path: str = "",
    ) -> List[ValidationError]:
        """Recursively validates an instance against schema rules."""
        if schema is None:
            schema = self.root_schema

        errors: List[ValidationError] = []

        # 1. $ref resolution
        if "$ref" in schema:
            target_schema = self.resolve_ref(schema["$ref"])
            errors.extend(self.validate(instance, target_schema, path))
            return errors

        # 2. type check (string or array of types for nullable support)
        if "type" in schema:
            expected_type = schema["type"]
            matched = False
            if isinstance(expected_type, str):
                matched = self._check_primitive_type(instance, expected_type)
            elif isinstance(expected_type, list):
                matched = any(self._check_primitive_type(instance, t) for t in expected_type)

            if not matched:
                actual_name = "null" if instance is None else type(instance).__name__
                errors.append(
                    ValidationError(
                        self.file_name,
                        path or "/",
                        f"Expected type {expected_type}, got {actual_name}",
                        "type",
                    )
                )
                return errors

        # If instance is null and matched nullable, no further constraints apply
        if instance is None:
            return errors

        # 3. String constraints: minLength, pattern, format, enum
        if isinstance(instance, str):
            if "minLength" in schema and len(instance) < schema["minLength"]:
                errors.append(
                    ValidationError(
                        self.file_name,
                        path or "/",
                        f"String length {len(instance)} is less than minLength {schema['minLength']}",
                        "minLength",
                    )
                )
            if "pattern" in schema:
                pattern = schema["pattern"]
                if not re.search(pattern, instance):
                    errors.append(
                        ValidationError(
                            self.file_name,
                            path or "/",
                            f"String '{instance}' does not match pattern '{pattern}'",
                            "pattern",
                        )
                    )
            if "enum" in schema and instance not in schema["enum"]:
                errors.append(
                    ValidationError(
                        self.file_name,
                        path or "/",
                        f"Value '{instance}' not in allowed enum {schema['enum']}",
                        "enum",
                    )
                )

        # 4. Number constraints: minimum, maximum, enum
        if isinstance(instance, (int, float)) and not isinstance(instance, bool):
            if "minimum" in schema and instance < schema["minimum"]:
                errors.append(
                    ValidationError(
                        self.file_name,
                        path or "/",
                        f"Value {instance} is less than minimum {schema['minimum']}",
                        "minimum",
                    )
                )
            if "maximum" in schema and instance > schema["maximum"]:
                errors.append(
                    ValidationError(
                        self.file_name,
                        path or "/",
                        f"Value {instance} exceeds maximum {schema['maximum']}",
                        "maximum",
                    )
                )
            if "enum" in schema and instance not in schema["enum"]:
                errors.append(
                    ValidationError(
                        self.file_name,
                        path or "/",
                        f"Value {instance} not in allowed enum {schema['enum']}",
                        "enum",
                    )
                )

        # 5. Array constraints: minItems, items
        if isinstance(instance, list):
            if "minItems" in schema and len(instance) < schema["minItems"]:
                errors.append(
                    ValidationError(
                        self.file_name,
                        path or "/",
                        f"Array length {len(instance)} is less than minItems {schema['minItems']}",
                        "minItems",
                    )
                )
            if "items" in schema:
                item_schema = schema["items"]
                for idx, item in enumerate(instance):
                    item_path = f"{path}/{idx}" if path else f"/{idx}"
                    errors.extend(self.validate(item, item_schema, item_path))

        # 6. Object constraints: required, properties, additionalProperties
        if isinstance(instance, dict):
            if "required" in schema:
                for req_key in schema["required"]:
                    if req_key not in instance:
                        errors.append(
                            ValidationError(
                                self.file_name,
                                f"{path}/{req_key}" if path else f"/{req_key}",
                                f"Required property '{req_key}' is missing",
                                "required",
                            )
                        )

            properties = schema.get("properties", {})
            additional_props = schema.get("additionalProperties", True)

            for key, val in instance.items():
                prop_path = f"{path}/{key}" if path else f"/{key}"
                if key in properties:
                    errors.extend(self.validate(val, properties[key], prop_path))
                else:
                    if additional_props is False:
                        errors.append(
                            ValidationError(
                                self.file_name,
                                prop_path,
                                f"Additional property '{key}' is not allowed",
                                "additionalProperties",
                            )
                        )
                    elif isinstance(additional_props, dict):
                        errors.extend(self.validate(val, additional_props, prop_path))

        # 7. const check
        if "const" in schema and instance != schema["const"]:
            errors.append(
                ValidationError(
                    self.file_name,
                    path or "/",
                    f"Value {instance!r} does not equal const {schema['const']!r}",
                    "const",
                )
            )

        # 8. allOf conditional rules
        if "allOf" in schema and isinstance(schema["allOf"], list):
            for sub_schema in schema["allOf"]:
                errors.extend(self.validate(instance, sub_schema, path))

        # 9. if / then / else conditional validation
        if "if" in schema and isinstance(schema["if"], dict):
            if_errors = self.validate(instance, schema["if"], path)
            if not if_errors:
                if "then" in schema and isinstance(schema["then"], dict):
                    errors.extend(self.validate(instance, schema["then"], path))
            else:
                if "else" in schema and isinstance(schema["else"], dict):
                    errors.extend(self.validate(instance, schema["else"], path))

        return errors


# ---------------------------------------------------------------------------
# Substantive Relational Cross-Checker
# ---------------------------------------------------------------------------

class CrossChecker:
    """
    Enforces relational consistency, data contract rules, timing algebra,
    substantive verification integrity, and returns standardized error codes.
    """

    def __init__(
        self,
        metadata: Dict[str, Any],
        ledger: Dict[str, Any],
        measurements: Dict[str, Any],
        findings: Dict[str, Any],
        architecture: Optional[Dict[str, Any]] = None,
        strict: bool = False,
    ) -> None:
        self.metadata = metadata
        self.ledger = ledger
        self.measurements = measurements
        self.findings = findings
        self.architecture = architecture or {}
        self.strict = strict

    def check_all(self) -> List[ValidationError]:
        errors: List[ValidationError] = []

        # -------------------------------------------------------------------
        # Indexing & Duplicate ID Detection (E_DUPLICATE_ID)
        # -------------------------------------------------------------------
        ledger_units = self.ledger.get("units", [])
        ledger_unit_map: Dict[str, Dict[str, Any]] = {}
        for idx, u in enumerate(ledger_units):
            if isinstance(u, dict) and "id" in u:
                uid = u["id"]
                if uid in ledger_unit_map:
                    errors.append(ValidationError(
                        "coverage-ledger.json",
                        f"/units/{idx}/id",
                        f"Duplicate unit id '{uid}' in coverage ledger",
                        "E_DUPLICATE_ID",
                    ))
                else:
                    ledger_unit_map[uid] = u

        workflows = self.measurements.get("workflows", [])
        wf_map: Dict[str, Dict[str, Any]] = {}
        wf_total_ms_map: Dict[str, float] = {}
        node_map: Dict[str, Dict[str, Dict[str, Any]]] = {}  # wf_id -> (node_id -> node)
        all_nodes_list: Dict[str, List[Dict[str, Any]]] = {}

        for idx, wf in enumerate(workflows):
            if isinstance(wf, dict) and "id" in wf:
                wid = wf["id"]
                if wid in wf_map:
                    errors.append(ValidationError(
                        "measurements.json",
                        f"/workflows/{idx}/id",
                        f"Duplicate workflow id '{wid}' in measurements",
                        "E_DUPLICATE_ID",
                    ))
                else:
                    wf_map[wid] = wf
                    call_tree = wf.get("call_tree", {})
                    wf_total_ms_map[wid] = float(call_tree.get("total_ms", 0.0))
                    node_map[wid] = {}

        findings_list = self.findings.get("findings", [])
        seen_finding_ids: Set[str] = set()
        for idx, f in enumerate(findings_list):
            if isinstance(f, dict) and "id" in f:
                fid = f["id"]
                if fid in seen_finding_ids:
                    errors.append(ValidationError(
                        "findings.json",
                        f"/findings/{idx}/id",
                        f"Duplicate finding id '{fid}' in findings",
                        "E_DUPLICATE_ID",
                    ))
                else:
                    seen_finding_ids.add(fid)

        observations_list = self.findings.get("observations", [])
        seen_obs_ids: Set[str] = set()
        for idx, obs in enumerate(observations_list):
            if isinstance(obs, dict) and "id" in obs:
                oid = obs["id"]
                if oid in seen_obs_ids:
                    errors.append(ValidationError(
                        "findings.json",
                        f"/observations/{idx}/id",
                        f"Duplicate observation id '{oid}' in observations",
                        "E_DUPLICATE_ID",
                    ))
                else:
                    seen_obs_ids.add(oid)

        probes_list = self.measurements.get("probes", [])
        probe_map: Dict[str, Dict[str, Any]] = {}
        for idx, pb in enumerate(probes_list):
            if isinstance(pb, dict) and "probe_id" in pb:
                pid = pb["probe_id"]
                if pid in probe_map:
                    errors.append(ValidationError(
                        "measurements.json",
                        f"/probes/{idx}/probe_id",
                        f"Duplicate probe_id '{pid}' in measurements",
                        "E_DUPLICATE_ID",
                    ))
                else:
                    probe_map[pid] = pb

        # -------------------------------------------------------------------
        # Rule: E_COMBINED_SUM (Section 4.7 & 11)
        # Reject any stored or displayed field summing latencies across workflows
        # -------------------------------------------------------------------
        def _check_combined_sum(obj: Any, file_name: str, path: str):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in ("combined_impact_ms", "combined_impact", "total_impact_ms", "cross_workflow_sum_ms"):
                        errors.append(ValidationError(
                            file_name,
                            f"{path}/{k}",
                            f"Forbidden cross-workflow combined latency sum field '{k}' detected",
                            "E_COMBINED_SUM",
                        ))
                    _check_combined_sum(v, file_name, f"{path}/{k}")
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    _check_combined_sum(v, file_name, f"{path}/{i}")

        _check_combined_sum(self.metadata, "run-metadata.json", "")
        _check_combined_sum(self.measurements, "measurements.json", "")
        _check_combined_sum(self.findings, "findings.json", "")
        _check_combined_sum(self.ledger, "coverage-ledger.json", "")

        # -------------------------------------------------------------------
        # Call Tree Traversal: E_NEGATIVE_DURATION, E_PROVENANCE_MISSING/INVALID,
        # E_TREE_ARITHMETIC, E_UNATTRIBUTED_TIME, E_WRAPPER_TIME, E_STATS_WRONG,
        # E_DUPLICATE_ID (node_id)
        # -------------------------------------------------------------------
        exercised_unit_ids: Set[str] = set()
        leaf_ratios: List[Tuple[float, str, str]] = []

        def _traverse_node(
            node: Dict[str, Any],
            w_id: str,
            wf_dur: float,
            path: str,
            visited_ids: Set[int],
            is_root: bool = False,
        ) -> None:
            if not isinstance(node, dict):
                return
            n_ptr = id(node)
            if n_ptr in visited_ids:
                errors.append(ValidationError(
                    "measurements.json",
                    path,
                    f"Cycle detected in call tree at '{node.get('name')}'",
                    "E_TREE_ARITHMETIC",
                ))
                return
            visited_ids.add(n_ptr)

            nid = node.get("node_id")
            if nid:
                if nid in node_map[w_id]:
                    errors.append(ValidationError(
                        "measurements.json",
                        f"{path}/node_id",
                        f"Duplicate node_id '{nid}' within workflow '{w_id}'",
                        "E_DUPLICATE_ID",
                    ))
                else:
                    node_map[w_id][nid] = node

            total_ms = node.get("total_ms", 0.0)
            self_ms = node.get("self_ms", 0.0)

            # Check negative duration
            if (isinstance(total_ms, (int, float)) and total_ms < 0) or (isinstance(self_ms, (int, float)) and self_ms < 0):
                errors.append(ValidationError(
                    "measurements.json",
                    path,
                    f"Negative duration at node '{node.get('name')}': total_ms={total_ms}, self_ms={self_ms}",
                    "E_NEGATIVE_DURATION",
                ))

            # Check provenance
            src = node.get("source")
            if src is None:
                errors.append(ValidationError(
                    "measurements.json",
                    path,
                    f"Node '{node.get('name')}' is missing required provenance 'source'",
                    "E_PROVENANCE_MISSING",
                ))
            elif src not in ("measured", "simulated", "estimated"):
                errors.append(ValidationError(
                    "measurements.json",
                    f"{path}/source",
                    f"Node '{node.get('name')}' has invalid source '{src}'",
                    "E_PROVENANCE_INVALID",
                ))

            # Check unit_id reference
            uid = node.get("unit_id")
            calls = node.get("calls", 0)
            if uid is not None:
                if uid not in ledger_unit_map:
                    errors.append(ValidationError(
                        "measurements.json",
                        f"{path}/unit_id",
                        f"Call tree node '{node.get('name')}' references unit_id '{uid}' which does not exist in coverage ledger",
                        "E_BAD_REFERENCE",
                    ))
                if isinstance(calls, (int, float)) and calls > 0:
                    exercised_unit_ids.add(uid)

            # Check per-node stats recomputation vs per_run_ms (E_STATS_WRONG)
            per_run_ms = node.get("per_run_ms")
            stats = node.get("stats")
            if per_run_ms and isinstance(per_run_ms, list) and len(per_run_ms) > 0 and stats:
                exp_stats = compute_stats(per_run_ms)
                for k in ("mean", "p50", "p95", "min", "max"):
                    if k in stats and abs(stats[k] - exp_stats[k]) > 0.1:
                        errors.append(ValidationError(
                            "measurements.json",
                            f"{path}/stats/{k}",
                            f"Node '{node.get('name')}' stored {k} ({stats[k]}) differs from recomputed ({exp_stats[k]})",
                            "E_STATS_WRONG",
                        ))

            # Children arithmetic & unexplained time
            children = node.get("children", [])
            exec_mode = node.get("children_execution", "sequential")

            if children:
                if exec_mode == "sequential":
                    child_sum = sum(
                        c.get("total_ms", 0.0)
                        for c in children
                        if isinstance(c, dict) and isinstance(c.get("total_ms"), (int, float))
                    )
                    expected_total = self_ms + child_sum
                    tolerance = max(0.5, 0.01 * total_ms)
                    diff = abs(total_ms - expected_total)
                    if diff > tolerance:
                        errors.append(ValidationError(
                            "measurements.json",
                            path,
                            f"Sequential arithmetic mismatch at node '{node.get('name')}': total_ms {total_ms} != self_ms {self_ms} + children {child_sum:.4f} (diff {diff:.4f} > tolerance {tolerance:.4f})",
                            "E_TREE_ARITHMETIC",
                        ))
                elif exec_mode == "concurrent":
                    intervals = [
                        (c.get("start_ms", 0.0), c.get("end_ms", 0.0))
                        for c in children
                        if isinstance(c, dict) and "start_ms" in c and "end_ms" in c
                    ]
                    if len(intervals) == len(children) and len(intervals) > 0:
                        union_span = compute_interval_union(intervals)
                        expected_total = self_ms + union_span
                        tolerance = max(0.5, 0.01 * total_ms)
                        diff = abs(total_ms - expected_total)
                        if diff > tolerance:
                            errors.append(ValidationError(
                                "measurements.json",
                                path,
                                f"Concurrent arithmetic mismatch at node '{node.get('name')}': total_ms {total_ms} != self_ms {self_ms} + union {union_span:.4f} (diff {diff:.4f} > tolerance {tolerance:.4f})",
                                "E_TREE_ARITHMETIC",
                            ))
                    else:
                        max_child = max(
                            (c.get("total_ms", 0.0) for c in children if isinstance(c, dict)),
                            default=0.0,
                        )
                        if max_child > total_ms + max(0.5, 0.01 * total_ms):
                            errors.append(ValidationError(
                                "measurements.json",
                                path,
                                f"Concurrent child total_ms ({max_child}) exceeds parent total_ms ({total_ms})",
                                "E_TREE_ARITHMETIC",
                            ))

                # Unexplained time: non-leaf node with self_ms >= 5% of workflow duration without breakdown
                if wf_dur > 0 and self_ms >= 0.05 * wf_dur:
                    has_unattributed = any(
                        isinstance(c, dict) and (c.get("kind") == "unattributed" or c.get("name") == "unattributed")
                        for c in children
                    )
                    if not has_unattributed:
                        errors.append(ValidationError(
                            "measurements.json",
                            path,
                            f"Non-leaf node '{node.get('name')}' self_ms ({self_ms} ms, {self_ms/wf_dur*100:.2f}%) >= 5% of workflow duration ({wf_dur} ms) without breakdown or unattributed child",
                            "E_UNATTRIBUTED_TIME",
                        ))

            # Wrapper time: acquisition-type node with self >= 10% of workflow without source_note explaining wait
            op = node.get("operation")
            name_lower = node.get("name", "").lower()
            acquisition_ops = {
                "db.connect", "db.session", "http.dns", "http.connect",
                "http.tls", "http.pool_wait"
            }
            is_wrapper = (op in acquisition_ops) or any(
                sub in name_lower for sub in ("db_session", "get_db", "session_scope", "connection_pool")
            )
            if is_wrapper and wf_dur > 0 and self_ms >= 0.10 * wf_dur:
                src_note = (node.get("source_note") or "").strip()
                if not src_note:
                    errors.append(ValidationError(
                        "measurements.json",
                        path,
                        f"Acquisition-type node '{node.get('name')}' (operation '{op}') has self_ms ({self_ms} ms) >= 10% of workflow without source_note explaining measured wait",
                        "E_WRAPPER_TIME",
                    ))

            # E_CPU_WAIT_TREE: check inclusive cpu_ms and wait_ms on inner nodes
            cpu_ms = node.get("cpu_ms")
            wait_ms = node.get("wait_ms")
            self_cpu_ms = node.get("self_cpu_ms")
            self_wait_ms = node.get("self_wait_ms")
            if children:
                if cpu_ms is not None and self_cpu_ms is not None:
                    c_cpu_sum = sum(float(c.get("cpu_ms", 0.0) or 0.0) for c in children if isinstance(c, dict))
                    exp_cpu = float(self_cpu_ms) + c_cpu_sum
                    tol_cpu = max(0.5, 0.02 * exp_cpu)
                    if abs(float(cpu_ms) - exp_cpu) > tol_cpu:
                        errors.append(ValidationError(
                            "measurements.json",
                            path,
                            f"Inner node '{node.get('name')}' cpu_ms ({cpu_ms}) does not match self_cpu_ms ({self_cpu_ms}) + children cpu ({c_cpu_sum:.4f}) within tolerance {tol_cpu:.4f}",
                            "E_CPU_WAIT_TREE",
                        ))
                if wait_ms is not None and self_wait_ms is not None:
                    c_wait_sum = sum(float(c.get("wait_ms", 0.0) or 0.0) for c in children if isinstance(c, dict))
                    exp_wait = float(self_wait_ms) + c_wait_sum
                    tol_wait = max(0.5, 0.02 * exp_wait)
                    if abs(float(wait_ms) - exp_wait) > tol_wait:
                        errors.append(ValidationError(
                            "measurements.json",
                            path,
                            f"Inner node '{node.get('name')}' wait_ms ({wait_ms}) does not match self_wait_ms ({self_wait_ms}) + children wait ({c_wait_sum:.4f}) within tolerance {tol_wait:.4f}",
                            "E_CPU_WAIT_TREE",
                        ))
            else:
                if total_ms > 1.0:
                    cw_src = node.get("cpu_wait_source", "")
                    if cw_src in ("measured_thread_cpu", "measured_process_cpu", "profiler_oncpu") and cpu_ms is not None:
                        leaf_ratios.append((float(cpu_ms) / total_ms, path, node.get("name", "")))

            for idx, child in enumerate(children):
                _traverse_node(child, w_id, wf_dur, f"{path}/children/{idx}", set(visited_ids), is_root=False)

        for w_idx, wf in enumerate(workflows):
            if isinstance(wf, dict) and "call_tree" in wf:
                wid = wf.get("id", f"wf_{w_idx}")
                wf_dur = wf_total_ms_map.get(wid, 0.0)
                _traverse_node(wf["call_tree"], wid, wf_dur, f"/workflows/{w_idx}/call_tree", set(), is_root=True)

        # Strict mode check: E_RATIO_SUSPICIOUS on leaf nodes
        if self.strict and len(leaf_ratios) >= 3:
            sorted_r = sorted(leaf_ratios, key=lambda x: x[0])
            for i in range(len(sorted_r) - 2):
                if sorted_r[i+2][0] - sorted_r[i][0] <= 0.002:
                    errors.append(ValidationError(
                        "measurements.json",
                        sorted_r[i][1],
                        f"3 or more leaf nodes share suspiciously identical cpu/total ratio ({sorted_r[i][0]:.4f}) with measured source",
                        "E_RATIO_SUSPICIOUS",
                    ))
                    break

        # -------------------------------------------------------------------
        # Ledger Validation: E_NOTE_NUMBERS, E_DENOMINATOR, E_STATUS_MISMATCH,
        # E_PENDING_STATUS, E_BAD_REFERENCE
        # -------------------------------------------------------------------
        def _get_node_interval(node: Dict[str, Any]) -> Optional[Tuple[float, float]]:
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

        def _calculate_descendant_deduction(
            node: Dict[str, Any],
            nearest_desc: List[Dict[str, Any]],
        ) -> Tuple[float, bool]:
            if not nearest_desc:
                return 0.0, False

            is_concurrent = (
                node.get("children_execution") == "concurrent"
                or node.get("overlapping_child_intervals") is True
                or node.get("is_concurrent") is True
                or any(d.get("concurrent") is True for d in nearest_desc)
            )

            intervals = [_get_node_interval(d) for d in nearest_desc]
            all_have_intervals = all(iv is not None for iv in intervals)

            if all_have_intervals:
                valid_intervals = [iv for iv in intervals if iv is not None]
                union_span = compute_interval_union(valid_intervals)
                return union_span, False

            if is_concurrent:
                return 0.0, True

            desc_total = sum(float(d.get("total_ms", 0.0)) for d in nearest_desc)
            return desc_total, False

        def _find_nearest_descendant_units(n: Dict[str, Any], uids: Set[str]) -> List[Dict[str, Any]]:
            desc = []
            for c in n.get("children", []):
                if isinstance(c, dict):
                    c_uid = c.get("unit_id")
                    if c_uid and c_uid in uids:
                        desc.append(c)
                    else:
                        desc.extend(_find_nearest_descendant_units(c, uids))
            return desc

        audit_total_ms = 0.0
        for wf in workflows:
            if isinstance(wf, dict):
                ct = wf.get("call_tree", {})
                w = float(wf.get("weight", 1.0))
                audit_total_ms += float(ct.get("total_ms", 0.0)) * w

        expected_unit_stats: Dict[str, Dict[str, Any]] = {}
        for u in ledger_units:
            if isinstance(u, dict) and u.get("id"):
                expected_unit_stats[u["id"]] = {
                    "self_ms": 0.0,
                    "owned_ms": 0.0,
                    "total_ms": 0.0,
                    "calls": 0,
                    "accounting_insufficient": False,
                    "per_wf": {},
                }

        ledger_uids = set(expected_unit_stats.keys())

        for wf in workflows:
            if not isinstance(wf, dict) or "call_tree" not in wf:
                continue
            w_id = wf.get("id", "")
            wf_dur = wf_total_ms_map.get(w_id, 0.0)

            def _traverse_for_classifier(n: Dict[str, Any], ancestors: Set[str]):
                if not isinstance(n, dict):
                    return
                u_id = n.get("unit_id")
                if u_id and u_id in ledger_uids:
                    nearest = _find_nearest_descendant_units(n, ledger_uids)
                    deduction, is_insufficient = _calculate_descendant_deduction(n, nearest)
                    if is_insufficient:
                        expected_unit_stats[u_id]["accounting_insufficient"] = True
                    owned = max(0.0, float(n.get("total_ms", 0.0)) - deduction)
                    st = expected_unit_stats[u_id]
                    st["owned_ms"] += owned
                    st["self_ms"] += float(n.get("self_ms", 0.0))
                    st["calls"] += int(n.get("calls", 1))
                    if u_id not in ancestors:
                        st["total_ms"] += float(n.get("total_ms", 0.0))

                    if w_id not in st["per_wf"]:
                        st["per_wf"][w_id] = {"owned_ms": 0.0, "total_ms": 0.0, "self_ms": 0.0}
                    st["per_wf"][w_id]["owned_ms"] += owned
                    st["per_wf"][w_id]["self_ms"] += float(n.get("self_ms", 0.0))
                    if u_id not in ancestors:
                        st["per_wf"][w_id]["total_ms"] += float(n.get("total_ms", 0.0))

                    new_anc = ancestors | {u_id}
                else:
                    new_anc = ancestors

                for c in n.get("children", []):
                    _traverse_for_classifier(c, new_anc)

            _traverse_for_classifier(wf["call_tree"], set())

        # Determine unit with maximum owned_ms among executed units
        max_owned_uid = None
        max_owned_ms = -1.0
        for uid_k, u_st in expected_unit_stats.items():
            if u_st["calls"] > 0 and u_st["owned_ms"] > max_owned_ms:
                max_owned_ms = u_st["owned_ms"]
                max_owned_uid = uid_k

        for u_idx, unit in enumerate(ledger_units):
            if not isinstance(unit, dict):
                continue
            uid = unit.get("id", f"unit_{u_idx}")
            status = unit.get("status")

            # Check status reason and note for numbers followed by % or ms
            for field in ("status_reason", "note"):
                val = unit.get(field)
                if val and isinstance(val, str):
                    if re.search(r'\b\d+(?:\.\d+)?\s*(?:%|ms\b)', val):
                        errors.append(ValidationError(
                            "coverage-ledger.json",
                            f"/units/{u_idx}/{field}",
                            f"Ledger free text '{field}' in unit '{uid}' contains numbers followed by % or ms: '{val}'",
                            "E_NOTE_NUMBERS",
                        ))

            # Check parent_unit_id reference
            p_uid = unit.get("parent_unit_id")
            if p_uid and p_uid not in ledger_unit_map:
                errors.append(ValidationError(
                    "coverage-ledger.json",
                    f"/units/{u_idx}/parent_unit_id",
                    f"Unit '{uid}' references parent_unit_id '{p_uid}' which does not exist in coverage ledger",
                    "E_BAD_REFERENCE",
                ))

            # Check workflow IDs in unit
            for w_idx, wid in enumerate(unit.get("workflows", [])):
                if wid not in wf_map:
                    errors.append(ValidationError(
                        "coverage-ledger.json",
                        f"/units/{u_idx}/workflows/{w_idx}",
                        f"Unit '{uid}' references workflow '{wid}' which does not exist in measurements",
                        "E_BAD_REFERENCE",
                    ))

            # Check status classification match
            st = expected_unit_stats.get(uid, {"owned_ms": 0.0, "total_ms": 0.0, "calls": 0, "self_ms": 0.0, "per_wf": {}})
            audit_share_pct = (st["owned_ms"] / audit_total_ms * 100.0) if audit_total_ms > 0 else 0.0

            if status == "pending":
                if self.strict:
                    errors.append(ValidationError(
                        "coverage-ledger.json",
                        f"/units/{u_idx}/status",
                        f"Unit '{uid}' has status 'pending' (not allowed in final report)",
                        "E_PENDING_STATUS",
                    ))
            elif status != "insufficient_data":
                expected_status = "not_exercised"
                if st["calls"] > 0:
                    if audit_share_pct >= 10.0 and st["owned_ms"] >= 5.0:
                        expected_status = "hot"
                    elif audit_share_pct >= 1.0 and st["owned_ms"] >= 1.0:
                        expected_status = "warm"
                    else:
                        expected_status = "cold"

                if status != expected_status:
                    errors.append(ValidationError(
                        "coverage-ledger.json",
                        f"/units/{u_idx}/status",
                        f"Unit '{uid}' status '{status}' contradicts expected status '{expected_status}' from measurements (owned_ms={st['owned_ms']:.2f}, audit_share={audit_share_pct:.2f}%)",
                        "E_STATUS_MISMATCH",
                    ))
                elif status == "hot" and st["owned_ms"] < 5.0:
                    errors.append(ValidationError(
                        "coverage-ledger.json",
                        f"/units/{u_idx}/status",
                        f"Unit '{uid}' classified 'hot' but owned_ms ({st['owned_ms']:.2f}) < 5.0 ms",
                        "E_STATUS_MISMATCH",
                    ))
                elif uid == max_owned_uid and st["owned_ms"] >= 5.0 and status != "hot":
                    errors.append(ValidationError(
                        "coverage-ledger.json",
                        f"/units/{u_idx}/status",
                        f"Unit '{uid}' has the largest owned_ms ({st['owned_ms']:.2f} ms) but status is '{status}' instead of 'hot'",
                        "E_STATUS_MISMATCH",
                    ))

            # Check denominator in unit metrics per_workflow
            metrics = unit.get("metrics")
            if isinstance(metrics, dict):
                for pw_idx, pw in enumerate(metrics.get("per_workflow", [])):
                    pw_wid = pw.get("workflow_id")
                    if pw_wid in wf_total_ms_map:
                        dur = wf_total_ms_map[pw_wid]
                        if dur > 0:
                            pw_owned = pw.get("owned_ms", 0.0)
                            pw_wf_share = pw.get("workflow_share_pct", pw.get("owned_share_pct"))
                            if pw_wf_share is not None:
                                calc_share = (pw_owned / dur) * 100.0
                                if abs(pw_wf_share - calc_share) > 0.5:
                                    errors.append(ValidationError(
                                        "coverage-ledger.json",
                                        f"/units/{u_idx}/metrics/per_workflow/{pw_idx}/workflow_share_pct",
                                        f"Unit '{uid}' workflow_share_pct ({pw_wf_share}%) does not match calculation from own workflow duration ({calc_share:.2f}%)",
                                        "E_DENOMINATOR",
                                    ))
                            pw_self = pw.get("self_ms")
                            pw_self_share = pw.get("self_share_pct")
                            if pw_self is not None and pw_self_share is not None:
                                calc_self_share = (pw_self / dur) * 100.0
                                if abs(pw_self_share - calc_self_share) > 0.5:
                                    errors.append(ValidationError(
                                        "coverage-ledger.json",
                                        f"/units/{u_idx}/metrics/per_workflow/{pw_idx}/self_share_pct",
                                        f"Unit '{uid}' self_share_pct ({pw_self_share}%) does not match calculation from own workflow duration ({calc_self_share:.2f}%)",
                                        "E_DENOMINATOR",
                                    ))

        # -------------------------------------------------------------------
        # Findings Validation:
        # E_BAD_REFERENCE, E_VERIFIED_FROM_SIMULATED, E_STATS_COPIED,
        # E_DENOMINATOR, E_FIX_PREREQ, E_UNSUPPORTED_CLAIM, E_CONCURRENCY_CLAIM,
        # E_STATIC_AS_FINDING
        # -------------------------------------------------------------------
        covered_hot_unit_ids: Set[str] = set()

        for f_idx, finding in enumerate(findings_list):
            if not isinstance(finding, dict):
                continue
            fid = finding.get("id", f"finding_{f_idx}")
            f_status = finding.get("status")
            f_prov = finding.get("provenance")
            cat = finding.get("category")
            title = finding.get("title", "")
            root_cause = finding.get("root_cause", "")
            f_metrics = finding.get("metrics")

            # Check unit_ids reference
            uids = finding.get("unit_ids", [])
            for u_idx, uid in enumerate(uids):
                if uid not in ledger_unit_map:
                    errors.append(ValidationError(
                        "findings.json",
                        f"/findings/{f_idx}/unit_ids/{u_idx}",
                        f"Finding '{fid}' references unit_id '{uid}' which does not exist in coverage ledger",
                        "E_BAD_REFERENCE",
                    ))
                else:
                    covered_hot_unit_ids.add(uid)

            # Check workflows reference
            fwfs = finding.get("workflows", [])
            for w_idx, wid in enumerate(fwfs):
                if wid not in wf_map:
                    errors.append(ValidationError(
                        "findings.json",
                        f"/findings/{f_idx}/workflows/{w_idx}",
                        f"Finding '{fid}' references workflow '{wid}' which does not exist in measurements",
                        "E_BAD_REFERENCE",
                    ))

            # Check evidence_refs
            evidence_refs = finding.get("evidence_refs", [])
            ref_sources: List[str] = []
            for r_idx, ref in enumerate(evidence_refs):
                if isinstance(ref, dict):
                    r_wid = ref.get("workflow_id")
                    r_nid = ref.get("node_id")
                    if r_wid not in node_map or r_nid not in node_map[r_wid]:
                        errors.append(ValidationError(
                            "findings.json",
                            f"/findings/{f_idx}/evidence_refs/{r_idx}",
                            f"Finding '{fid}' references non-existent node '{r_nid}' in workflow '{r_wid}'",
                            "E_BAD_REFERENCE",
                        ))
                    else:
                        ref_sources.append(node_map[r_wid][r_nid].get("source", "measured"))

            # E_VERIFIED_FROM_SIMULATED:
            # Verified finding cannot rely on simulated or estimated nodes or provenance, and must have evidence
            if f_status == "verified":
                ev_list = finding.get("evidence")
                if not isinstance(ev_list, list) or len(ev_list) == 0:
                    errors.append(ValidationError(
                        "findings.json",
                        f"/findings/{f_idx}/evidence",
                        f"Finding '{fid}' is marked 'verified' but has an empty evidence array",
                        "E_VERIFIED_FROM_SIMULATED",
                    ))
                if f_prov in ("simulated", "estimated") or any(s in ("simulated", "estimated") for s in ref_sources):
                    errors.append(ValidationError(
                        "findings.json",
                        f"/findings/{f_idx}/status",
                        f"Finding '{fid}' is marked 'verified' but relies on simulated or estimated evidence",
                        "E_VERIFIED_FROM_SIMULATED",
                    ))

            # E_FINDING_METRIC_MISMATCH: finding metrics must match classifier unit metrics within max(0.01ms, 0.5%)
            if isinstance(f_metrics, dict) and uids and uids[0] in expected_unit_stats:
                exp_u = expected_unit_stats[uids[0]]
                f_owned = f_metrics.get("owned_ms")
                if f_owned is not None:
                    u_owned = exp_u["owned_ms"]
                    tol = max(0.01, 0.005 * u_owned)
                    if abs(f_owned - u_owned) > tol:
                        errors.append(ValidationError(
                            "findings.json",
                            f"/findings/{f_idx}/metrics/owned_ms",
                            f"Finding '{fid}' owned_ms ({f_owned}) does not match classifier unit owned_ms ({u_owned:.4f}) within tolerance {tol:.4f}",
                            "E_FINDING_METRIC_MISMATCH",
                        ))
                f_tot = f_metrics.get("total_ms")
                if f_tot is not None:
                    u_tot = exp_u["total_ms"]
                    tol = max(0.01, 0.005 * u_tot)
                    if abs(f_tot - u_tot) > tol:
                        errors.append(ValidationError(
                            "findings.json",
                            f"/findings/{f_idx}/metrics/total_ms",
                            f"Finding '{fid}' total_ms ({f_tot}) does not match classifier unit total_ms ({u_tot:.4f}) within tolerance {tol:.4f}",
                            "E_FINDING_METRIC_MISMATCH",
                        ))
                f_wf_share = f_metrics.get("workflow_share_pct")
                if f_wf_share is not None and fwfs:
                    p_wid = fwfs[0]
                    p_dur = wf_total_ms_map.get(p_wid, 0.0)
                    if p_dur > 0:
                        u_wf_owned = exp_u.get("per_wf", {}).get(p_wid, {}).get("owned_ms", exp_u["owned_ms"])
                        calc_wf_share = (u_wf_owned / p_dur) * 100.0
                        if abs(f_wf_share - calc_wf_share) > max(0.01, 0.5):
                            errors.append(ValidationError(
                                "findings.json",
                                f"/findings/{f_idx}/metrics/workflow_share_pct",
                                f"Finding '{fid}' workflow_share_pct ({f_wf_share}%) differs from classifier calculation ({calc_wf_share:.2f}%)",
                                "E_FINDING_METRIC_MISMATCH",
                            ))
                f_aud_share = f_metrics.get("audit_share_pct")
                if f_aud_share is not None and audit_total_ms > 0:
                    calc_aud_share = (exp_u["owned_ms"] / audit_total_ms) * 100.0
                    if abs(f_aud_share - calc_aud_share) > max(0.01, 0.5):
                        errors.append(ValidationError(
                            "findings.json",
                            f"/findings/{f_idx}/metrics/audit_share_pct",
                            f"Finding '{fid}' audit_share_pct ({f_aud_share}%) differs from classifier calculation ({calc_aud_share:.2f}%)",
                            "E_FINDING_METRIC_MISMATCH",
                        ))
                if "calls" in f_metrics:
                    if f_metrics["calls"] != exp_u["calls"]:
                        errors.append(ValidationError(
                            "findings.json",
                            f"/findings/{f_idx}/metrics/calls",
                            f"Finding '{fid}' calls ({f_metrics['calls']}) does not match classifier unit calls ({exp_u['calls']})",
                            "E_FINDING_METRIC_MISMATCH",
                        ))

            # Finding status & severity validation
            if f_status == "needs_validation":
                actual_sev = finding.get("severity") or finding.get("impact")
                if actual_sev is not None:
                    errors.append(ValidationError(
                        "findings.json",
                        f"/findings/{f_idx}/severity",
                        f"Needs-validation finding '{fid}' must not have confirmed severity or impact '{actual_sev}'",
                        "E_NEEDS_VALIDATION_SEVERITY",
                    ))
                for req_nv_field in ("hypothesis", "missing_evidence", "validation_blocker", "next_validation_step"):
                    if not finding.get(req_nv_field):
                        errors.append(ValidationError(
                            "findings.json",
                            f"/findings/{f_idx}/{req_nv_field}",
                            f"Needs-validation finding '{fid}' is missing required field '{req_nv_field}'",
                            "E_NEEDS_VALIDATION_MISSING_FIELD",
                        ))
            elif f_status == "verified":
                if not evidence_refs:
                    errors.append(ValidationError(
                        "findings.json",
                        f"/findings/{f_idx}/evidence_refs",
                        f"Verified finding '{fid}' is missing required non-empty evidence_refs array",
                        "E_MISSING_EVIDENCE_REFS",
                    ))

                # E_SEVERITY_MISMATCH: rubric check for verified findings
                actual_sev = finding.get("severity") or finding.get("impact")
                wf_share_val = f_metrics.get("workflow_share_pct", f_metrics.get("share_of_workflow_pct", 0.0)) if isinstance(f_metrics, dict) else 0.0
                owned_ms_val = f_metrics.get("owned_ms", f_metrics.get("total_ms", 0.0)) if isinstance(f_metrics, dict) else 0.0
                has_scale_100ms = any(
                    isinstance(p, dict) and (p.get("scale") == "10x" or p.get("scale_factor") == 10) and (p.get("projected_ms", 0) >= 100 or p.get("loaded_p50_ms", 0) >= 100)
                    for p in finding.get("probes", [])
                )
                if wf_share_val >= 50.0 and owned_ms_val >= 100.0:
                    expected_sev = "critical"
                elif (wf_share_val >= 25.0 and owned_ms_val >= 25.0) or has_scale_100ms:
                    expected_sev = "high"
                elif wf_share_val >= 10.0 or owned_ms_val >= 10.0:
                    expected_sev = "medium"
                else:
                    expected_sev = "low"

                # Check if cryptographic hotspot is calibrated as high relative priority
                is_crypto_hotspot = any(kw in title.lower() or kw in root_cause.lower() for kw in ("pbkdf2", "hash", "crypto", "bcrypt", "argon2"))
                if is_crypto_hotspot and expected_sev == "critical" and actual_sev in ("high", "critical"):
                    pass
                elif actual_sev and actual_sev.lower() != expected_sev:
                    errors.append(ValidationError(
                        "findings.json",
                        f"/findings/{f_idx}/severity",
                        f"Finding '{fid}' severity '{actual_sev}' contradicts severity rubric expected '{expected_sev}' (workflow_share={wf_share_val:.2f}%, owned_ms={owned_ms_val:.2f}ms)",
                        "E_SEVERITY_MISMATCH",
                    ))

            # E_FIX_WEAKENS_SECURITY: never recommend lowering cryptographic cost or parameters
            fix = finding.get("fix")
            fix_str = json.dumps(fix).lower() if fix else ""
            if re.search(r'\b(lower|reduce|decrease|calibrate\s+down)\b.{1,60}\b(iteration|cost|rounds|work\s+factor|key\s+size)\b', fix_str):
                errors.append(ValidationError(
                    "findings.json",
                    f"/findings/{f_idx}/fix",
                    f"Finding '{fid}' fix suggests reducing cryptographic security parameter (iterations, rounds, cost, or key size)",
                    "E_FIX_WEAKENS_SECURITY",
                ))

            # E_PROBE_BASELINE_MISMATCH: c=1 baseline p50 must be within 50% of workflow measured p50
            for p_idx, probe in enumerate(finding.get("probes", [])):
                if isinstance(probe, dict):
                    c_num = probe.get("concurrency") or probe.get("c") or 1
                    if c_num == 1:
                        base_p50 = probe.get("baseline_p50_ms", probe.get("baseline_p50", probe.get("p50_ms")))
                        p_wf = probe.get("workflow_id") or (fwfs[0] if fwfs else None)
                        if p_wf and p_wf in wf_map and base_p50 is not None:
                            w_p50 = wf_map[p_wf].get("summary", {}).get("p50", 0.0)
                            if w_p50 > 0:
                                diff_pct = abs(float(base_p50) - w_p50) / w_p50
                                if diff_pct > 0.50:
                                    errors.append(ValidationError(
                                        "findings.json",
                                        f"/findings/{f_idx}/probes/{p_idx}",
                                        f"Probe c=1 baseline p50 ({base_p50} ms) deviates >50% from workflow '{p_wf}' measured p50 ({w_p50} ms)",
                                        "E_PROBE_BASELINE_MISMATCH",
                                    ))

            # Check metrics timing conservation
            f_metrics = finding.get("metrics")
            if isinstance(f_metrics, dict):
                cpu_ms = f_metrics.get("cpu_ms")
                wait_ms = f_metrics.get("wait_ms")
                total_ms = f_metrics.get("total_ms")
                cw_src = finding.get("cpu_wait_source") or f_metrics.get("cpu_wait_source")
                cw_basis = finding.get("cpu_wait_attribution_basis") or f_metrics.get("cpu_wait_attribution_basis")
                crossed_async = finding.get("crossed_async_yield_points", False) or f_metrics.get("crossed_async_yield_points", False)
                for ref in evidence_refs:
                    r_w = ref.get("workflow_id")
                    r_n = ref.get("node_id")
                    if r_w in node_map and r_n in node_map[r_w]:
                        rn = node_map[r_w][r_n]
                        if rn.get("crossed_async_yield_points"):
                            crossed_async = True
                        if rn.get("cpu_wait_source") == "unavailable":
                            cw_src = "unavailable"
                        if rn.get("cpu_wait_attribution_basis") == "unavailable":
                            cw_basis = "unavailable"
                is_conserved = (
                    cw_src != "unavailable"
                    and cw_basis != "unavailable"
                    and not crossed_async
                )
                if (
                    is_conserved
                    and isinstance(cpu_ms, (int, float))
                    and isinstance(wait_ms, (int, float))
                    and isinstance(total_ms, (int, float))
                    and not isinstance(cpu_ms, bool)
                    and not isinstance(wait_ms, bool)
                    and not isinstance(total_ms, bool)
                ):
                    diff = abs((cpu_ms + wait_ms) - total_ms)
                    tolerance = max(0.02 * total_ms, 0.01)
                    if diff > tolerance:
                        errors.append(ValidationError(
                            "findings.json",
                            f"/findings/{f_idx}/metrics",
                            f"Timing conservation failed for finding '{fid}': |(cpu_ms {cpu_ms} + wait_ms {wait_ms}) - total_ms {total_ms}| = {diff:.4f} ms exceeds tolerance {tolerance:.4f} ms",
                            "E_TREE_ARITHMETIC",
                        ))
            if isinstance(f_metrics, dict):
                p50_ms = f_metrics.get("p50_ms")
                cv_val = f_metrics.get("coefficient_of_variation")
                wf_p50 = f_metrics.get("workflow_p50_ms")
                wf_cv = f_metrics.get("workflow_cv")

                # E_STATS_COPIED:
                # Finding stats equal to workflow stats while not being root node
                if (
                    p50_ms is not None
                    and cv_val is not None
                    and wf_p50 is not None
                    and wf_cv is not None
                    and abs(p50_ms - wf_p50) < 1e-4
                    and abs(cv_val - wf_cv) < 1e-4
                ):
                    # Check if this node is root node of workflow
                    is_root = False
                    for wid in fwfs:
                        if wid in wf_map and wf_map[wid].get("call_tree", {}).get("unit_id") in uids:
                            is_root = True
                    if not is_root:
                        errors.append(ValidationError(
                            "findings.json",
                            f"/findings/{f_idx}/metrics",
                            f"Finding '{fid}' copies workflow statistics (p50={p50_ms}, cv={cv_val}) onto a non-root function",
                            "E_STATS_COPIED",
                        ))

                # E_DENOMINATOR:
                # share_of_workflow_pct must be computed using own workflow
                share_pct = f_metrics.get("share_of_workflow_pct")
                total_ms = f_metrics.get("total_ms", 0.0)
                self_ms = f_metrics.get("self_ms", 0.0)
                if share_pct is not None and fwfs:
                    primary_wid = fwfs[0]
                    primary_wf_dur = wf_total_ms_map.get(primary_wid, 0.0)
                    if primary_wf_dur > 0:
                        calc_total_share = (total_ms / primary_wf_dur) * 100.0
                        calc_self_share = (self_ms / primary_wf_dur) * 100.0
                        # Check if share matches either total or self within 1.5%
                        if abs(share_pct - calc_total_share) > 1.5 and abs(share_pct - calc_self_share) > 1.5:
                            errors.append(ValidationError(
                                "findings.json",
                                f"/findings/{f_idx}/metrics/share_of_workflow_pct",
                                f"Finding '{fid}' share_of_workflow_pct ({share_pct}%) differs from calculation on own workflow '{primary_wid}' ({calc_total_share:.2f}% total, {calc_self_share:.2f}% self)",
                                "E_DENOMINATOR",
                            ))

            # E_FIX_PREREQ:
            # Fix must have prerequisites[], risks[], expected_result_basis, and baseline_ref if projection
            fix = finding.get("fix")
            if not isinstance(fix, dict):
                errors.append(ValidationError(
                    "findings.json",
                    f"/findings/{f_idx}/fix",
                    f"Finding '{fid}' fix object is missing",
                    "E_FIX_PREREQ",
                ))
            else:
                if not isinstance(fix.get("prerequisites"), list):
                    errors.append(ValidationError(
                        "findings.json",
                        f"/findings/{f_idx}/fix/prerequisites",
                        f"Finding '{fid}' fix is missing required prerequisites array",
                        "E_FIX_PREREQ",
                    ))
                if not isinstance(fix.get("risks"), list):
                    errors.append(ValidationError(
                        "findings.json",
                        f"/findings/{f_idx}/fix/risks",
                        f"Finding '{fid}' fix is missing required risks array",
                        "E_FIX_PREREQ",
                    ))
                risks_list = fix.get("risks", [])
                prereqs_list = fix.get("prerequisites", [])
                if isinstance(risks_list, list) and isinstance(prereqs_list, list):
                    risks_joined = " ".join(str(r) for r in risks_list).lower()
                    prereqs_joined = " ".join(str(p) for p in prereqs_list).lower()
                    for dep_kw in ("migrat", "existing", "backfill", "schema", "rehash", "downtime", "rollout"):
                        if dep_kw in risks_joined and dep_kw not in prereqs_joined:
                            errors.append(ValidationError(
                                "findings.json",
                                f"/findings/{f_idx}/fix/prerequisites",
                                f"Finding '{fid}' fix lists '{dep_kw}' dependency in risks but lacks corresponding prerequisite",
                                "E_FIX_PREREQ",
                            ))
                basis = fix.get("expected_result_basis")
                if basis not in ("measured_baseline_projection", "rule_of_thumb"):
                    errors.append(ValidationError(
                        "findings.json",
                        f"/findings/{f_idx}/fix/expected_result_basis",
                        f"Finding '{fid}' fix has invalid expected_result_basis '{basis}'",
                        "E_FIX_PREREQ",
                    ))
                elif basis == "measured_baseline_projection":
                    b_ref = fix.get("baseline_ref")
                    if not b_ref:
                        errors.append(ValidationError(
                            "findings.json",
                            f"/findings/{f_idx}/fix/baseline_ref",
                            f"Finding '{fid}' uses 'measured_baseline_projection' but is missing required baseline_ref",
                            "E_FIX_PREREQ",
                        ))

            # E_UNSUPPORTED_CLAIM:
            # Check for banned claims like O(1), sub-millisecond, O(log N)
            fix_text = json.dumps(fix) if fix else ""
            finding_text = f"{title} {root_cause} {fix_text}".lower()
            unsupported_patterns = [
                r'\bo\(1\)\b',
                r'\bo\(log\s*n\)\b',
                r'\bsub-millisecond\b',
                r'\bconstant o\(1\)\b',
            ]
            for pat in unsupported_patterns:
                if re.search(pat, finding_text):
                    errors.append(ValidationError(
                        "findings.json",
                        f"/findings/{f_idx}",
                        f"Finding '{fid}' contains unsupported theoretical claim matching '{pat}'",
                        "E_UNSUPPORTED_CLAIM",
                    ))

            if isinstance(fix, dict):
                exp_res_text = str(fix.get("expected_result", ""))
                proj_basis_text = str(fix.get("projection", {}).get("basis", "") or fix.get("expected_result_basis_note", ""))
                if re.search(r'\b\d+%\b', exp_res_text):
                    if not re.search(r'\d+(?:\.\d+)?\s*(?:[-+*/=]|of)\s*\d+', proj_basis_text + " " + exp_res_text):
                        errors.append(ValidationError(
                            "findings.json",
                            f"/findings/{f_idx}/fix/expected_result",
                            f"Finding '{fid}' contains percentage projection without calculation arithmetic in projection.basis",
                            "E_UNSUPPORTED_CLAIM",
                        ))

            # E_CONCURRENCY_CLAIM:
            # Event-loop, starvation, or concurrency claims require structured probe evidence
            concurrency_claim = (
                cat in ("event_loop_blocking", "connection_pool_exhaustion")
                or "event loop" in root_cause.lower()
                or "starv" in root_cause.lower()
                or "concurrency" in title.lower()
                or "concurrency" in root_cause.lower()
            )
            if concurrency_claim:
                has_probe = False
                for pid in finding.get("probe_ids", []):
                    if pid in probe_map and probe_map[pid].get("probe_type") == "concurrency":
                        has_probe = True
                if not has_probe and finding.get("probes"):
                    has_probe = any(
                        isinstance(p, dict) and (p.get("concurrency", 1) > 1 or "concurrency" in p)
                        for p in finding.get("probes", [])
                    )
                if not has_probe:
                    errors.append(ValidationError(
                        "findings.json",
                        f"/findings/{f_idx}",
                        f"Finding '{fid}' claims concurrency or resource starvation without valid structured concurrency probe evidence",
                        "E_CONCURRENCY_CLAIM",
                    ))

            # Scale complexity claims check: require at least 3 distinct data volumes in scale probe
            scale_claim = any(
                term in f"{title} {root_cause}".lower()
                for term in ("linear scaling", "o(n)", "o(n^2)", "scale probe", "scaling behaviour")
            )
            if scale_claim:
                has_valid_scale = False
                for pid in finding.get("probe_ids", []):
                    if pid in probe_map and probe_map[pid].get("probe_type") == "scale":
                        obs = probe_map[pid].get("per_run_observations", [])
                        distinct_sizes = {
                            (o.get("data_volume") if o.get("data_volume") is not None else o.get("data_size"))
                            if isinstance(o, dict) else o
                            for o in obs
                        }
                        if len(distinct_sizes) >= 3:
                            has_valid_scale = True
                if not has_valid_scale and finding.get("probes"):
                    scale_probes = [
                        p for p in finding.get("probes", [])
                        if isinstance(p, dict) and (p.get("scale") or p.get("scale_factor"))
                    ]
                    if len(scale_probes) >= 3:
                        has_valid_scale = True
                if not has_valid_scale:
                    errors.append(ValidationError(
                        "findings.json",
                        f"/findings/{f_idx}",
                        f"Finding '{fid}' makes scaling complexity claims without structured scale probe containing at least 3 distinct data sizes",
                        "E_UNSUPPORTED_CLAIM",
                    ))

            # E_STATIC_AS_FINDING:
            # Finding has no runtime dynamic measurements or is purely static observation
            if f_metrics and f_metrics.get("calls", 0) == 0 and f_metrics.get("total_ms", 0.0) == 0:
                errors.append(ValidationError(
                    "findings.json",
                    f"/findings/{f_idx}",
                    f"Static inspection result '{fid}' is listed in findings[] instead of observations[]",
                    "E_STATIC_AS_FINDING",
                ))

        # Check observations: must not have metrics or impact
        for o_idx, obs in enumerate(observations_list):
            if isinstance(obs, dict):
                oid = obs.get("id", f"obs_{o_idx}")
                if "metrics" in obs or "impact" in obs:
                    errors.append(ValidationError(
                        "findings.json",
                        f"/observations/{o_idx}",
                        f"Static observation '{oid}' carries measured metrics or impact",
                        "E_STATIC_AS_FINDING",
                    ))

        # E_UNCOVERED_HOT:
        # Every unit classified 'hot' must be in findings or reviewed_not_bottleneck
        reviewed_list = self.findings.get("reviewed_not_bottleneck", [])
        for r_item in reviewed_list:
            if isinstance(r_item, dict):
                for uid in r_item.get("unit_ids", []):
                    covered_hot_unit_ids.add(uid)

        for u in ledger_units:
            if isinstance(u, dict) and u.get("status") == "hot":
                uid = u.get("id")
                if uid and uid not in covered_hot_unit_ids:
                    errors.append(ValidationError(
                        "findings.json",
                        "/findings",
                        f"Hot unit '{uid}' is not referenced in findings or reviewed_not_bottleneck",
                        "E_UNCOVERED_HOT",
                    ))

        # Check architecture references if architecture is provided
        arch_nodes = self.architecture.get("nodes", [])
        arch_node_ids = {n.get("id") for n in arch_nodes if isinstance(n, dict)}
        for e_idx, edge in enumerate(self.architecture.get("edges", [])):
            if isinstance(edge, dict):
                src = edge.get("source")
                tgt = edge.get("target")
                if src and src not in arch_node_ids:
                    errors.append(ValidationError(
                        "architecture.json",
                        f"/edges/{e_idx}/source",
                        f"Architecture edge references non-existent source node '{src}'",
                        "E_BAD_REFERENCE",
                    ))
                if tgt and tgt not in arch_node_ids:
                    errors.append(ValidationError(
                        "architecture.json",
                        f"/edges/{e_idx}/target",
                        f"Architecture edge references non-existent target node '{tgt}'",
                        "E_BAD_REFERENCE",
                    ))

        # -------------------------------------------------------------------
        # Line Profiles Validation: E_LINE_SUM, E_LINE_CODE_MISMATCH, E_HOT_NO_LINE_PROFILE
        # -------------------------------------------------------------------
        line_profiles = self.measurements.get("line_profiles", [])
        line_prof_uids: Set[str] = set()

        for lp_idx, lp in enumerate(line_profiles):
            if not isinstance(lp, dict):
                continue
            lp_uid = lp.get("unit_id")
            if lp_uid:
                line_prof_uids.add(lp_uid)
            lines = lp.get("lines", [])
            if lines and lp_uid in expected_unit_stats:
                l_sum = sum(float(l.get("total_ms", 0.0)) for l in lines if isinstance(l, dict)) + float(lp.get("residual_ms", 0.0))
                u_owned = expected_unit_stats[lp_uid]["owned_ms"]
                tol = max(1.0, 0.05 * u_owned)
                if abs(l_sum - u_owned) > tol:
                    errors.append(ValidationError(
                        "measurements.json",
                        f"/line_profiles/{lp_idx}",
                        f"Line profile total sum for unit '{lp_uid}' ({l_sum:.2f} ms) deviates from unit owned_ms ({u_owned:.2f} ms) > tolerance {tol:.2f} ms",
                        "E_LINE_SUM",
                    ))

            # E_LINE_CODE_MISMATCH
            f_path_str = lp.get("file")
            if f_path_str:
                candidate = None
                repo = str(self.metadata.get("repo_name", "")).lower()
                possible_roots = [
                    Path("."),
                    Path("profile-audit"),
                ]
                if repo in ("profile-audit-demo", "demo-app"):
                    possible_roots.extend([
                        Path("examples/demo-app"),
                        Path("profile-audit/examples/demo-app"),
                    ])
                for r in possible_roots:
                    cand = r / f_path_str
                    if cand.is_file():
                        if "demo-app" in str(cand.resolve()).lower() and repo not in ("profile-audit-demo", "demo-app"):
                            continue
                        candidate = cand
                        break

                if candidate and candidate.is_file():
                    try:
                        src_lines = candidate.read_text(encoding="utf-8").splitlines()
                        for l_idx, line_item in enumerate(lines):
                            if isinstance(line_item, dict):
                                l_no = line_item.get("line")
                                if l_no is not None:
                                    if l_no < 1 or l_no > len(src_lines):
                                        errors.append(ValidationError(
                                            "measurements.json",
                                            f"/line_profiles/{lp_idx}/lines/{l_idx}/line",
                                            f"Line {l_no} out of bounds for file '{f_path_str}' ({len(src_lines)} lines)",
                                            "E_LINE_CODE_MISMATCH",
                                        ))
                                    else:
                                        real_text = src_lines[l_no - 1].strip()[:160]
                                        code_text = line_item.get("code", "").strip()[:160]
                                        if real_text != code_text:
                                            errors.append(ValidationError(
                                                "measurements.json",
                                                f"/line_profiles/{lp_idx}/lines/{l_idx}/code",
                                                f"Line {l_no} code text '{code_text}' does not match real line in {f_path_str}: '{real_text}'",
                                                "E_LINE_CODE_MISMATCH",
                                            ))
                    except Exception:
                        pass

        if self.strict:
            for u in ledger_units:
                if isinstance(u, dict) and u.get("status") == "hot":
                    h_uid = u.get("id")
                    if h_uid and h_uid not in line_prof_uids:
                        errors.append(ValidationError(
                            "measurements.json",
                            "/line_profiles",
                            f"Hot unit '{h_uid}' is missing a line_profiles entry or unavailable reason",
                            "E_HOT_NO_LINE_PROFILE",
                        ))

        # -------------------------------------------------------------------
        # Run Metadata Validation: E_MACHINE_PLACEHOLDER, E_RUN_DURATION
        # -------------------------------------------------------------------
        machine = self.metadata.get("machine", {})
        if isinstance(machine, dict):
            cpu_val = str(machine.get("cpu", "")).lower()
            mem_val = str(machine.get("memory", "")).lower()
            if any(term in cpu_val for term in ("multi-core processor", "unknown")) or cpu_val == "n/a" or not re.search(r'\d+', cpu_val):
                errors.append(ValidationError(
                    "run-metadata.json",
                    "/machine/cpu",
                    f"Machine CPU specification '{machine.get('cpu')}' is a placeholder or lacks core/model numbers",
                    "E_MACHINE_PLACEHOLDER",
                ))
            if any(term in mem_val for term in ("system ram", "unknown")) or mem_val == "n/a" or not re.search(r'\d+\s*(?:gb|mb)\b', mem_val):
                errors.append(ValidationError(
                    "run-metadata.json",
                    "/machine/memory",
                    f"Machine memory specification '{machine.get('memory')}' is a placeholder or lacks capacity in GB",
                    "E_MACHINE_PLACEHOLDER",
                ))

        started_str = self.metadata.get("started_at")
        finished_str = self.metadata.get("finished_at")
        if started_str and finished_str:
            try:
                s_dt = datetime.fromisoformat(started_str.replace("Z", "+00:00"))
                f_dt = datetime.fromisoformat(finished_str.replace("Z", "+00:00"))
                elapsed_sec = (f_dt - s_dt).total_seconds()
                total_runs_sec = sum(
                    float(r)
                    for wf in workflows
                    if isinstance(wf, dict)
                    for r in wf.get("runs", [])
                ) / 1000.0
                if elapsed_sec < total_runs_sec - 0.001:
                    errors.append(ValidationError(
                        "run-metadata.json",
                        "/finished_at",
                        f"Recorded run duration ({elapsed_sec:.2f}s) is less than total workflow runs duration ({total_runs_sec:.2f}s)",
                        "E_RUN_DURATION",
                    ))
            except Exception:
                pass

        return errors


# ---------------------------------------------------------------------------
# High-Level Run Directory & HTML File Validation Interface
# ---------------------------------------------------------------------------

REQUIRED_FILES = {
    "run-metadata.json": "run-metadata.schema.json",
    "coverage-ledger.json": "coverage-ledger.schema.json",
    "measurements.json": "measurements.schema.json",
    "findings.json": "findings.schema.json",
}


def load_json_file(path: Path) -> Tuple[Optional[Any], Optional[str]]:
    """Loads a JSON file returning (data, error_message)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except json.JSONDecodeError as exc:
        return None, f"Malformed JSON in {path.name}: {exc}"
    except Exception as exc:
        return None, f"Failed reading {path.name}: {exc}"


def extract_data_from_html(html_path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Extracts embedded JSON from dashboard HTML <script id='report-data'> tag."""
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()

        match = re.search(
            r'<script\s+id=["\']report-data["\']\s+type=["\']application/json["\']>(.*?)</script>',
            content,
            re.DOTALL | re.IGNORECASE,
        )
        if not match:
            return None, f"No embedded profiling data found in {html_path.name} (<script id='report-data'> missing)"

        raw_json = match.group(1).strip()
        data = json.loads(raw_json)
        return data, None
    except Exception as exc:
        return None, f"Failed extracting report-data from {html_path.name}: {exc}"


def validate_run_dir(
    target: Union[str, Path],
    strict: bool = False,
    schemas_dir: Optional[Union[str, Path]] = None,
) -> Tuple[bool, List[ValidationError], List[str]]:
    """
    Validates all profiling artifacts from a run directory OR an HTML report.
    Returns (is_valid: bool, errors: List[ValidationError], warnings: List[str]).
    """
    target_path = Path(target).resolve()
    if not target_path.exists():
        raise FileNotFoundError(f"Target not found: {target_path}")

    if schemas_dir is None:
        schemas_path = Path(__file__).resolve().parent.parent / "schemas"
    else:
        schemas_path = Path(schemas_dir).resolve()

    if not schemas_path.exists() or not schemas_path.is_dir():
        raise FileNotFoundError(f"Schemas directory not found: {schemas_path}")

    errors: List[ValidationError] = []
    warnings: List[str] = []
    loaded_data: Dict[str, Any] = {}

    if target_path.is_dir():
        if (target_path / ".work").is_dir():
            target_path = target_path / ".work"
        elif (target_path / "PROFILING-DASHBOARD.html").is_file() and not (target_path / "run-metadata.json").exists():
            target_path = target_path / "PROFILING-DASHBOARD.html"

    if target_path.is_file():
        # Validate dashboard HTML
        embedded, err = extract_data_from_html(target_path)
        if err is not None:
            errors.append(ValidationError(target_path.name, "/", err, "E_HTML_EXTRACT"))
            return False, errors, warnings

        # Split embedded bundle into individual artifact structures
        loaded_data["run-metadata.json"] = embedded.get("run_metadata", {})
        loaded_data["coverage-ledger.json"] = embedded.get("coverage_ledger", {})
        loaded_data["measurements.json"] = embedded.get("measurements", {})
        loaded_data["findings.json"] = {
            "schema_version": embedded.get("schema_version", "1.0.0"),
            "findings": embedded.get("findings", []),
            "observations": embedded.get("observations", []),
            "reviewed_not_bottleneck": embedded.get("reviewed_not_bottleneck", []),
        }
        loaded_data["architecture.json"] = embedded.get("architecture", {})

        # Validate each against schema
        for filename, schema_name in REQUIRED_FILES.items():
            schema_file = schemas_path / schema_name
            if schema_file.exists():
                schema_data, s_err = load_json_file(schema_file)
                if s_err is None and schema_data:
                    validator = SchemaValidator(schema_data, file_name=filename)
                    errors.extend(validator.validate(loaded_data[filename]))

    else:
        # Validate run directory
        for filename, schema_name in REQUIRED_FILES.items():
            file_path = target_path / filename
            if not file_path.exists():
                raise FileNotFoundError(f"Required artifact '{filename}' not found in {target_path}")

            schema_file = schemas_path / schema_name
            if not schema_file.exists():
                raise FileNotFoundError(f"Required schema '{schema_name}' not found in {schemas_path}")

            data, err = load_json_file(file_path)
            if err is not None:
                errors.append(ValidationError(filename, "/", err, "json_syntax"))
                continue

            schema_data, s_err = load_json_file(schema_file)
            if s_err is not None:
                raise ValueError(f"Failed parsing schema {schema_name}: {s_err}")

            loaded_data[filename] = data

            validator = SchemaValidator(schema_data, file_name=filename)
            file_errors = validator.validate(data)
            errors.extend(file_errors)

        # Check optional architecture.json if present
        arch_file = target_path / "architecture.json"
        if arch_file.exists():
            arch_data, a_err = load_json_file(arch_file)
            if a_err is None and isinstance(arch_data, dict):
                loaded_data["architecture.json"] = arch_data
                arch_schema_file = schemas_path / "architecture.schema.json"
                if arch_schema_file.exists():
                    arch_schema, _ = load_json_file(arch_schema_file)
                    if arch_schema:
                        v = SchemaValidator(arch_schema, file_name="architecture.json")
                        errors.extend(v.validate(arch_data))

    # Relational cross-checks across all loaded artifacts
    if len(loaded_data) >= len(REQUIRED_FILES):
        metadata = loaded_data.get("run-metadata.json", {})
        ledger = loaded_data.get("coverage-ledger.json", {})
        measurements = loaded_data.get("measurements.json", {})
        findings = loaded_data.get("findings.json", {})
        architecture = loaded_data.get("architecture.json", {})

        if all(isinstance(d, dict) for d in (metadata, ledger, measurements, findings)):
            checker = CrossChecker(
                metadata,
                ledger,
                measurements,
                findings,
                architecture=architecture,
                strict=strict,
            )
            cross_errors = checker.check_all()
            errors.extend(cross_errors)

    is_valid = len(errors) == 0
    return is_valid, errors, warnings


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate profile-audit run artifacts or dashboard HTML against schemas and cross-checks."
    )
    parser.add_argument("target", nargs="?", default=None, help="Path to profile-audit output run directory or dashboard HTML file.")
    parser.add_argument("--data", default=None, help="Path to profile-audit output run directory or dashboard HTML file.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Enable strict audit checks (rejects pending units and unexercised inconsistencies).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output structured JSON validation report.",
    )
    parser.add_argument(
        "--schemas-dir",
        dest="schemas_dir",
        default=None,
        help="Optional custom schemas directory override.",
    )

    args = parser.parse_args(argv)

    target_arg = args.target or args.data
    if not target_arg:
        sys.stderr.write("Target path must be specified positionally or via --data\n")
        return 2

    try:
        is_valid, errors, warnings = validate_run_dir(
            target_arg,
            strict=args.strict,
            schemas_dir=args.schemas_dir,
        )
    except FileNotFoundError as fnf_err:
        if args.json_output:
            print(json.dumps({
                "valid": False,
                "errors": [{"file": "", "path": "/", "message": str(fnf_err), "rule": "file_not_found"}],
                "warnings": [],
            }, indent=2))
        else:
            sys.stderr.write(f"Operational error: {fnf_err}\n")
        return 2
    except Exception as exc:
        if args.json_output:
            print(json.dumps({
                "valid": False,
                "errors": [{"file": "", "path": "/", "message": str(exc), "rule": "operational_error"}],
                "warnings": [],
            }, indent=2))
        else:
            sys.stderr.write(f"Operational error: {exc}\n")
        return 2

    if args.json_output:
        payload = {
            "valid": is_valid,
            "errors": [e.to_dict() for e in errors],
            "warnings": warnings,
        }
        print(json.dumps(payload, indent=2))
    else:
        if is_valid:
            print(f"Validation passed: all artifacts in '{args.target}' conform to schemas and cross-checks.")
            if warnings:
                print(f"{len(warnings)} warning(s):")
                for w in warnings:
                    print(f"  - {w}")
        else:
            print(f"Validation failed: {len(errors)} error(s) found in '{args.target}':")
            for err in errors:
                print(f"  {err}")

    return 0 if is_valid else 1


if __name__ == "__main__":
    sys.exit(main())
