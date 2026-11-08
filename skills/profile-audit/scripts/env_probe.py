#!/usr/bin/env python3
"""
env_probe.py - Standard-library-only environment probe.

Outputs JSON with:
- os
- os_version
- arch
- cpu_model
- logical_cores
- physical_cores (when obtainable, or null)
- ram_gb
- python_version
"""

import ctypes
import json
import os
import platform
import re
import subprocess
import sys
from typing import Any, Dict, Optional


def probe_windows() -> Dict[str, Any]:
    os_name = "Windows"
    os_version = platform.version() or platform.win32_ver()[0]
    arch = platform.machine()
    logical_cores = os.cpu_count() or 1
    physical_cores = None
    cpu_model = platform.processor() or "Unknown CPU"

    # Memory via GlobalMemoryStatusEx
    ram_gb = 0.0
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            ram_gb = round(stat.ullTotalPhys / (1024 ** 3), 2)
    except Exception:
        pass

    # CPU Model & Physical Cores via PowerShell
    try:
        ps_cmd = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$p = Get-CimInstance Win32_Processor; [PSCustomObject]@{Name=$p.Name; Cores=$p.NumberOfCores} | ConvertTo-Json -Compress",
        ]
        out = subprocess.check_output(ps_cmd, stderr=subprocess.DEVNULL, text=True, timeout=5)
        parsed = json.loads(out.strip())
        if isinstance(parsed, list) and len(parsed) > 0:
            parsed = parsed[0]
        if isinstance(parsed, dict):
            if parsed.get("Name"):
                cpu_model = str(parsed["Name"]).strip()
            if parsed.get("Cores"):
                physical_cores = int(parsed["Cores"])
    except Exception:
        # Fallback to wmic
        try:
            wmic_out = subprocess.check_output(
                ["wmic", "cpu", "get", "Name,NumberOfCores", "/format:list"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            )
            for line in wmic_out.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip().lower(), v.strip()
                    if k == "name" and v:
                        cpu_model = v
                    elif k == "numberofcores" and v.isdigit():
                        physical_cores = int(v)
        except Exception:
            pass

    return {
        "os": os_name,
        "os_version": os_version,
        "arch": arch,
        "cpu_model": cpu_model,
        "logical_cores": logical_cores,
        "physical_cores": physical_cores,
        "ram_gb": ram_gb,
        "python_version": platform.python_version(),
    }


def probe_linux() -> Dict[str, Any]:
    os_name = "Linux"
    os_version = platform.release()
    arch = platform.machine()
    logical_cores = os.cpu_count() or 1
    physical_cores = None
    cpu_model = "Unknown CPU"
    ram_gb = 0.0

    if os.path.exists("/proc/cpuinfo"):
        try:
            with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
                content = f.read()
            match = re.search(r"model name\s*:\s*(.+)", content)
            if match:
                cpu_model = match.group(1).strip()
            cores_match = re.search(r"cpu cores\s*:\s*(\d+)", content)
            if cores_match:
                physical_cores = int(cores_match.group(1))
        except Exception:
            pass

    if os.path.exists("/proc/meminfo"):
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                content = f.read()
            match = re.search(r"MemTotal:\s*(\d+)\s*kB", content)
            if match:
                total_kb = int(match.group(1))
                ram_gb = round(total_kb / (1024 * 1024), 2)
        except Exception:
            pass

    return {
        "os": os_name,
        "os_version": os_version,
        "arch": arch,
        "cpu_model": cpu_model,
        "logical_cores": logical_cores,
        "physical_cores": physical_cores,
        "ram_gb": ram_gb,
        "python_version": platform.python_version(),
    }


def probe_darwin() -> Dict[str, Any]:
    os_name = "macOS"
    os_version = platform.mac_ver()[0] or platform.release()
    arch = platform.machine()
    logical_cores = os.cpu_count() or 1
    physical_cores = None
    cpu_model = "Apple Silicon" if "arm" in arch.lower() else "Intel CPU"
    ram_gb = 0.0

    try:
        out = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True, stderr=subprocess.DEVNULL)
        if out.strip():
            cpu_model = out.strip()
    except Exception:
        pass

    try:
        out = subprocess.check_output(["sysctl", "-n", "hw.physicalcpu"], text=True, stderr=subprocess.DEVNULL)
        if out.strip().isdigit():
            physical_cores = int(out.strip())
    except Exception:
        pass

    try:
        out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True, stderr=subprocess.DEVNULL)
        if out.strip().isdigit():
            ram_gb = round(int(out.strip()) / (1024 ** 3), 2)
    except Exception:
        pass

    return {
        "os": os_name,
        "os_version": os_version,
        "arch": arch,
        "cpu_model": cpu_model,
        "logical_cores": logical_cores,
        "physical_cores": physical_cores,
        "ram_gb": ram_gb,
        "python_version": platform.python_version(),
    }


def probe_environment() -> Dict[str, Any]:
    sys_plat = sys.platform.lower()
    if sys_plat.startswith("win"):
        return probe_windows()
    elif sys_plat.startswith("darwin"):
        return probe_darwin()
    else:
        return probe_linux()


def main() -> int:
    env_info = probe_environment()
    print(json.dumps(env_info, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
