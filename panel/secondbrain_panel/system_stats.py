"""Lectura de métricas del sistema (host Windows).

CPU/RAM via psutil (cross-platform).
GPU via `nvidia-smi --query-gpu=... --format=csv` (no requiere libs extra).
Temperaturas de CPU en Windows son fragiles — las dejamos en None y solo
reportamos las que da nvidia-smi (GPU). psutil.sensors_temperatures() devuelve
{} en Windows en la mayoría de los casos.
"""

from __future__ import annotations

import subprocess
from typing import Any

import psutil


_NVIDIA_QUERY = (
    "name,memory.total,memory.used,memory.free,utilization.gpu,utilization.memory,"
    "temperature.gpu,power.draw,power.limit,fan.speed"
)


def cpu_ram_disk() -> dict[str, Any]:
    """Snapshot rápido (~50ms) de uso de CPU/RAM/disco principal."""
    # CPU percent con intervalo cortito; el primero suele ser 0 pero a partir
    # del segundo refresh es real porque psutil usa la diferencia con el último
    # snapshot interno.
    cpu_pct = psutil.cpu_percent(interval=None)
    vm = psutil.virtual_memory()
    sm = psutil.swap_memory()
    try:
        # disco del cwd / system drive
        d = psutil.disk_usage("/" if not psutil.WINDOWS else "C:\\")
    except Exception:
        d = None
    load = psutil.getloadavg() if hasattr(psutil, "getloadavg") else None
    return {
        "cpu_pct": round(cpu_pct, 1),
        "cpu_count_logical": psutil.cpu_count(logical=True),
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "ram": {
            "total_gb": round(vm.total / 1024**3, 2),
            "used_gb": round(vm.used / 1024**3, 2),
            "available_gb": round(vm.available / 1024**3, 2),
            "pct": round(vm.percent, 1),
        },
        "swap": {
            "total_gb": round(sm.total / 1024**3, 2),
            "used_gb": round(sm.used / 1024**3, 2),
            "pct": round(sm.percent, 1),
        },
        "disk": ({
            "total_gb": round(d.total / 1024**3, 1),
            "used_gb": round(d.used / 1024**3, 1),
            "free_gb": round(d.free / 1024**3, 1),
            "pct": round(d.percent, 1),
        } if d else None),
        "load_avg_1m": (round(load[0], 2) if load else None),
    }


def gpu() -> list[dict[str, Any]] | None:
    """Llama a nvidia-smi y devuelve la lista de GPUs (una por fila).

    Devuelve None si nvidia-smi no está en PATH o falla — el caller decide
    cómo mostrarlo (placeholder "no GPU" o esconder la sección).
    """
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                f"--query-gpu={_NVIDIA_QUERY}",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if r.returncode != 0:
            return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    out: list[dict[str, Any]] = []
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 10:
            continue

        def _f(v: str) -> float | None:
            try:
                return float(v)
            except (ValueError, TypeError):
                return None

        name = parts[0]
        mem_total = _f(parts[1]) or 0
        mem_used = _f(parts[2]) or 0
        mem_free = _f(parts[3]) or 0
        util_gpu = _f(parts[4])
        util_mem = _f(parts[5])
        temp = _f(parts[6])
        power = _f(parts[7])
        power_lim = _f(parts[8])
        fan = _f(parts[9])

        out.append({
            "name": name,
            "vram_total_mb": int(mem_total),
            "vram_used_mb": int(mem_used),
            "vram_free_mb": int(mem_free),
            "vram_pct": (round(mem_used / mem_total * 100, 1) if mem_total else None),
            "util_gpu_pct": util_gpu,
            "util_mem_pct": util_mem,
            "temp_c": temp,
            "power_w": power,
            "power_limit_w": power_lim,
            "fan_pct": fan,
        })
    return out


def ollama_loaded(base_url: str = "http://localhost:11434") -> list[dict[str, Any]] | None:
    """Modelos en VRAM AHORA según Ollama /api/ps."""
    import httpx
    try:
        with httpx.Client(timeout=3) as c:
            r = c.get(f"{base_url}/api/ps")
            r.raise_for_status()
            data = r.json()
    except Exception:
        return None
    out = []
    for m in data.get("models", []):
        size = m.get("size", 0) or 0
        size_vram = m.get("size_vram", 0) or 0
        out.append({
            "name": m.get("name"),
            "size_gb": round(size / 1e9, 2),
            "vram_gb": round(size_vram / 1e9, 2),
            "vram_pct_of_model": (round(size_vram / size * 100, 1) if size else None),
            "context_length": m.get("context_length"),
            "expires_at": m.get("expires_at"),
        })
    return out


def all_stats(ollama_url: str = "http://localhost:11434") -> dict[str, Any]:
    """One-shot que junta todo. Usado por el tab Sistema."""
    return {
        "host": cpu_ram_disk(),
        "gpus": gpu(),
        "ollama_loaded": ollama_loaded(ollama_url),
    }
