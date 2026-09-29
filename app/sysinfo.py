"""Hardware facts used for warnings and diagnostics (memory, GPU)."""
import ctypes
import os
import platform
import shutil
import sys
from typing import Optional

# what the speech models need (bf16 weights on the GPU, fp32 on the CPU)
NEED_VRAM_GB = 6.5
NEED_RAM_CPU_GB = 16.0


def ram_gb() -> Optional[float]:
    try:
        if os.name == "nt":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEMORYSTATUSEX()
            m.dwLength = ctypes.sizeof(m)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return round(m.ullTotalPhys / 2 ** 30, 1)
            return None
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30, 1)
    except (AttributeError, OSError, ValueError):
        return None


def gpu() -> dict:
    """{'name', 'vram_gb'} of the first CUDA device, or {} without one."""
    try:
        import torch
        if torch.cuda.is_available():
            p = torch.cuda.get_device_properties(0)
            return {"name": p.name, "vram_gb": round(p.total_memory / 2 ** 30, 1)}
    except Exception:
        pass
    return {}


def free_gb(path) -> Optional[float]:
    try:
        return round(shutil.disk_usage(str(path)).free / 2 ** 30, 1)
    except OSError:
        return None


def warnings(device: str) -> list:
    """Plain-language warnings when this machine is below what the models need."""
    out = []
    g = gpu()
    if device == "cuda" and g and g.get("vram_gb") and g["vram_gb"] < NEED_VRAM_GB:
        out.append(f"顯示卡記憶體只有 {g['vram_gb']} GB，語音模型約需 {NEED_VRAM_GB} GB，可能無法載入；"
                   "若載入失敗，請到「設定」改用 CPU。")
    ram = ram_gb()
    if (device == "cpu" or not g) and ram and ram < NEED_RAM_CPU_GB:
        out.append(f"電腦記憶體只有 {ram} GB，以 CPU 執行語音模型建議至少 {NEED_RAM_CPU_GB:.0f} GB，可能非常緩慢或失敗。")
    return out


def summary() -> dict:
    return {"python": sys.version.split()[0], "os": platform.platform(), "ram_gb": ram_gb(), "gpu": gpu()}
