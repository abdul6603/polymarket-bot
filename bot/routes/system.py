"""System/Infrastructure routes — real-time OS metrics, processes, ports, LaunchAgents."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from flask import Blueprint, jsonify

system_bp = Blueprint("system", __name__)

ET = ZoneInfo("America/New_York")

# Agent process patterns for identification
AGENT_PROCESS_MAP = {
    "garves": {"pattern": "bot.main", "launch_agent": "com.garves.bot"},
    "dashboard": {"pattern": "bot.live_dashboard", "launch_agent": "com.commandcenter.dashboard"},
    "shelby": {"pattern": "shelby", "launch_agent": None},
    "atlas": {"pattern": "atlas", "launch_agent": None},
    "thor": {"pattern": "thor", "launch_agent": None},
    "robotox": {"pattern": "sentinel", "launch_agent": "com.robotox.agent"},
    "hawk": {"pattern": "hawk", "launch_agent": "com.hawk.agent"},
    "viper": {"pattern": "viper", "launch_agent": "com.viper.agent"},
}

WATCHED_PORTS = {
    8877: "Dashboard",
    7777: "Shelby",
}


def _get_memory_info() -> dict:
    """Get memory usage via vm_stat + sysctl."""
    try:
        total_bytes = int(subprocess.check_output(
            ["sysctl", "-n", "hw.memsize"], timeout=5
        ).strip())
        total_gb = total_bytes / (1024 ** 3)

        vm_stat = subprocess.check_output(["vm_stat"], timeout=5).decode()
        page_size = 16384  # macOS ARM default
        m = re.search(r"page size of (\d+) bytes", vm_stat)
        if m:
            page_size = int(m.group(1))

        def _pages(label):
            m2 = re.search(rf"{label}:\s+(\d+)", vm_stat)
            return int(m2.group(1)) if m2 else 0

        free = _pages("Pages free") * page_size
        active = _pages("Pages active") * page_size
        inactive = _pages("Pages inactive") * page_size
        wired = _pages("Pages wired down") * page_size
        compressed = _pages("Pages occupied by compressor") * page_size

        used = active + wired + compressed
        used_gb = used / (1024 ** 3)
        pct = round(used / total_bytes * 100, 1)

        return {"total_gb": round(total_gb, 1), "used_gb": round(used_gb, 1), "percent": pct}
    except Exception as e:
        return {"total_gb": 0, "used_gb": 0, "percent": 0, "error": str(e)[:100]}


def _get_cpu_info() -> dict:
    """Get CPU load average."""
    try:
        load1, load5, load15 = os.getloadavg()
        cpu_count = os.cpu_count() or 1
        # Approximate CPU % from load average
        pct = round(load1 / cpu_count * 100, 1)
        return {
            "percent": min(pct, 100),
            "load_1m": round(load1, 2),
            "load_5m": round(load5, 2),
            "load_15m": round(load15, 2),
            "cores": cpu_count,
        }
    except Exception as e:
        return {"percent": 0, "cores": 0, "error": str(e)[:100]}


def _get_disk_info() -> dict:
    """Get disk usage."""
    try:
        usage = shutil.disk_usage("/")
        return {
            "total_gb": round(usage.total / (1024 ** 3), 1),
            "used_gb": round(usage.used / (1024 ** 3), 1),
            "free_gb": round(usage.free / (1024 ** 3), 1),
            "percent": round(usage.used / usage.total * 100, 1),
        }
    except Exception as e:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0, "error": str(e)[:100]}


def _get_uptime() -> dict:
    """Get system uptime."""
    try:
        out = subprocess.check_output(["sysctl", "-n", "kern.boottime"], timeout=5).decode()
        m = re.search(r"sec = (\d+)", out)
        if m:
            boot_ts = int(m.group(1))
            uptime_s = int(time.time() - boot_ts)
            days = uptime_s // 86400
            hours = (uptime_s % 86400) // 3600
            mins = (uptime_s % 3600) // 60
            if days > 0:
                text = f"{days}d {hours}h"
            elif hours > 0:
                text = f"{hours}h {mins}m"
            else:
                text = f"{mins}m"
            return {"seconds": uptime_s, "text": text}
        return {"seconds": 0, "text": "unknown"}
    except Exception as e:
        return {"seconds": 0, "text": "error", "error": str(e)[:100]}


def _get_python_processes() -> list:
    """Get all running Python processes with details."""
    try:
        out = subprocess.check_output(
            ["ps", "-eo", "pid,pcpu,pmem,rss,etime,command"],
            timeout=5,
        ).decode()
        processes = []
        for line in out.strip().split("\n")[1:]:
            parts = line.split(None, 5)
            if len(parts) < 6:
                continue
            pid, cpu, mem, rss, etime, cmd = parts
            if "python" not in cmd.lower():
                continue
            # Try to identify which agent this is
            agent = "unknown"
            for name, info in AGENT_PROCESS_MAP.items():
                if info["pattern"] in cmd:
                    agent = name
                    break
            processes.append({
                "pid": int(pid),
                "cpu_percent": float(cpu),
                "mem_percent": float(mem),
                "mem_mb": round(int(rss) / 1024, 1),
                "uptime": etime.strip(),
                "command": cmd[:120],
                "agent": agent,
            })
        return sorted(processes, key=lambda p: p["agent"])
    except Exception as e:
        return [{"error": str(e)[:200]}]


def _get_listening_ports() -> list:
    """Get listening TCP ports."""
    try:
        out = subprocess.check_output(
            ["lsof", "-iTCP", "-sTCP:LISTEN", "-nP"],
            timeout=5, stderr=subprocess.DEVNULL,
        ).decode()
        ports = []
        seen = set()
        for line in out.strip().split("\n")[1:]:
            parts = line.split()
            if len(parts) < 9:
                continue
            name = parts[0]
            pid = parts[1]
            addr = parts[8]
            m = re.search(r":(\d+)$", addr)
            if not m:
                continue
            port = int(m.group(1))
            if port in seen:
                continue
            seen.add(port)
            service = WATCHED_PORTS.get(port, name)
            ports.append({
                "port": port,
                "pid": int(pid),
                "process": name,
                "service": service,
            })
        return sorted(ports, key=lambda p: p["port"])
    except Exception as e:
        return [{"error": str(e)[:200]}]


BROTHERHOOD_LABELS = {
    "com.commandcenter.dashboard", "com.garves.bot",
    "com.robotox.agent", "com.shelby.assistant",
    "com.thor.agent", "com.hawk.agent", "com.viper.agent",
}


def _get_launchagents() -> list:
    """Get Brotherhood LaunchAgent statuses."""
    home = Path.home()
    la_dir = home / "Library" / "LaunchAgents"
    agents = []
    try:
        for plist in sorted(la_dir.glob("com.*.plist")):
            # Only show Brotherhood agents (skip BlueStacks, Google, etc.)
            if plist.stem not in BROTHERHOOD_LABELS:
                continue
            label = plist.stem
            # Check if loaded
            try:
                result = subprocess.run(
                    ["launchctl", "list", label],
                    capture_output=True, timeout=5,
                )
                loaded = result.returncode == 0
                # Parse PID and exit status from output
                pid = None
                exit_code = None
                if loaded:
                    for out_line in result.stdout.decode().split("\n"):
                        if '"PID"' in out_line:
                            m = re.search(r"(\d+)", out_line.split("=")[-1])
                            if m:
                                pid = int(m.group(1))
                        if '"LastExitStatus"' in out_line:
                            m = re.search(r"(\d+)", out_line.split("=")[-1])
                            if m:
                                exit_code = int(m.group(1))
            except Exception:
                loaded = False
                pid = None
                exit_code = None

            agents.append({
                "label": label,
                "loaded": loaded,
                "pid": pid,
                "exit_code": exit_code,
                "plist": str(plist),
            })
    except Exception:
        pass
    return agents


def _get_recent_errors() -> list:
    """Get recent errors from Robotox log watcher."""
    try:
        home = Path.home()
        alerts_file = home / "sentinel" / "data" / "log_alerts.json"
        if not alerts_file.exists():
            return []
        data = json.loads(alerts_file.read_text())
        if isinstance(data, list):
            return data[-10:]
        return data.get("alerts", [])[-10:]
    except Exception:
        return []


BROTHERHOOD_DIRS = {
    "polymarket-bot": "Garves/Hawk/Viper/Dashboard",
    "shelby": "Shelby",
    "atlas": "Atlas",
    "sentinel": "Robotox",
    "mercury": "Lisa",
    "thor": "Thor",
    "soren-content": "Soren",
    "shared": "Shared",
    "odin": "Odin",
}

CODE_EXTENSIONS = {".py", ".html", ".js", ".css", ".json", ".sh", ".yml", ".yaml", ".toml", ".cfg", ".md"}
SKIP_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".git", ".mypy_cache", ".pytest_cache", "egg-info"}


def _get_codebase_stats() -> dict:
    """Scan all Brotherhood directories for file count, lines, and size."""
    home = Path.home()
    total_files = 0
    total_lines = 0
    total_bytes = 0
    by_project = {}
    by_ext = {}

    for dirname, label in BROTHERHOOD_DIRS.items():
        proj_dir = home / dirname
        if not proj_dir.is_dir():
            continue
        proj_files = 0
        proj_lines = 0
        proj_bytes = 0
        for root, dirs, files in os.walk(proj_dir):
            # Prune skipped directories in-place
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for fname in files:
                fpath = Path(root) / fname
                ext = fpath.suffix.lower()
                if ext not in CODE_EXTENSIONS:
                    continue
                try:
                    size = fpath.stat().st_size
                    if size > 2_000_000:  # skip files > 2MB
                        continue
                    proj_files += 1
                    proj_bytes += size
                    lines = fpath.read_text(errors="replace").count("\n")
                    proj_lines += lines
                    by_ext[ext] = by_ext.get(ext, 0) + lines
                except Exception:
                    continue
        total_files += proj_files
        total_lines += proj_lines
        total_bytes += proj_bytes
        by_project[dirname] = {
            "label": label,
            "files": proj_files,
            "lines": proj_lines,
            "size_kb": round(proj_bytes / 1024, 1),
        }

    # Format total size
    if total_bytes >= 1024 * 1024:
        size_str = f"{total_bytes / (1024 * 1024):.1f} MB"
    else:
        size_str = f"{total_bytes / 1024:.1f} KB"

    # Top extensions
    top_ext = sorted(by_ext.items(), key=lambda x: -x[1])[:6]

    return {
        "total_files": total_files,
        "total_lines": total_lines,
        "total_bytes": total_bytes,
        "size_formatted": size_str,
        "by_project": by_project,
        "top_extensions": [{"ext": e, "lines": l} for e, l in top_ext],
    }


_codebase_stats_cache = {"data": None, "ts": 0}

@system_bp.route("/api/system/codebase-stats")
def api_codebase_stats():
    """Codebase statistics across all Brotherhood projects (cached 5 min)."""
    import time as _t
    now = _t.time()
    if _codebase_stats_cache["data"] and now - _codebase_stats_cache["ts"] < 300:
        return jsonify(_codebase_stats_cache["data"])
    stats = _get_codebase_stats()
    _codebase_stats_cache["data"] = stats
    _codebase_stats_cache["ts"] = now
    return jsonify(stats)


@system_bp.route("/api/system/metrics")
def api_system_metrics():
    """Full system metrics snapshot."""
    return jsonify({
        "timestamp": datetime.now(ET).isoformat(),
        "cpu": _get_cpu_info(),
        "memory": _get_memory_info(),
        "disk": _get_disk_info(),
        "uptime": _get_uptime(),
        "processes": _get_python_processes(),
        "ports": _get_listening_ports(),
        "launchagents": _get_launchagents(),
        "errors": _get_recent_errors(),
    })


@system_bp.route("/api/system/processes")
def api_system_processes():
    """Just the Python process list."""
    return jsonify({"processes": _get_python_processes()})


@system_bp.route("/api/system/ports")
def api_system_ports():
    """Just the listening ports."""
    return jsonify({"ports": _get_listening_ports()})


@system_bp.route("/api/system/launchagents")
def api_system_launchagents():
    """LaunchAgent status list."""
    return jsonify({"launchagents": _get_launchagents()})


@system_bp.route("/api/system/action/<action>", methods=["POST"])
def api_system_action(action: str):
    """Execute a system action (restart agent, check ports, etc.)."""
    allowed = {
        "restart-dashboard": ["launchctl", "stop", "com.commandcenter.dashboard"],
        "restart-shelby": ["launchctl", "stop", "com.shelby.assistant"],
        "restart-thor": ["launchctl", "stop", "com.thor.agent"],
        "flush-events": None,  # handled separately
        "force-atlas-cycle": None,
    }

    if action not in allowed:
        return jsonify({"error": f"Unknown action: {action}"}), 400

    try:
        if action == "flush-events":
            events_file = Path.home() / "shared" / "events.jsonl"
            if events_file.exists():
                events_file.write_text("")
            return jsonify({"success": True, "message": "Event bus flushed"})

        if action == "force-atlas-cycle":
            # Trigger via the existing API
            import requests
            resp = requests.post("http://localhost:8877/api/atlas/background/start", timeout=5)
            return jsonify({"success": True, "message": "Atlas cycle triggered"})

        cmd = allowed[action]
        if cmd:
            subprocess.run(cmd, timeout=10, check=False)
            # For launchctl stop, the agent restarts automatically
            return jsonify({"success": True, "message": f"Executed: {action}"})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)[:200]})
