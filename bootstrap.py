"""LumenSubs one-click setup & launcher (called by start.bat).

First run:  creates .venv, installs PyTorch (CUDA build matched to the GPU
driver, or CPU), installs requirements, makes sure FFmpeg is available and
downloads the models. Later runs only verify and start the app (fast).

Options (pass to start.bat):  --cpu        force CPU PyTorch
                              --reinstall  reinstall all packages
                              --no-browser start the server only
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VPY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STATE = VENV / "lumen_setup.json"
REQ = ROOT / "requirements.txt"
PY_MIN, PY_MAX = (3, 10), (3, 13)

MODELS = ["Qwen/Qwen3-ASR-1.7B", "Qwen/Qwen3-ForcedAligner-0.6B"]
# CUDA wheel tags published by PyTorch, newest first, with the minimum
# driver-reported CUDA version each needs
TORCH_CUDA = [("cu130", 13.0), ("cu129", 12.9), ("cu128", 12.8), ("cu126", 12.6), ("cu124", 12.4), ("cu121", 12.1), ("cu118", 11.8)]
NEEDED_MODULES = ["fastapi", "uvicorn", "multipart", "openai", "numpy", "PIL", "onnxruntime", "qwen_asr",
                  "transformers", "soundfile", "librosa", "huggingface_hub", "torch"]


def say(msg=""):
    print(msg, flush=True)


def step(msg):
    say(f"\n▶ {msg}")


def fail(msg, code=1):
    say(f"\n✖ {msg}")
    sys.exit(code)


def run(cmd, **kw):
    return subprocess.run(cmd, **kw)


def pip(*args):
    r = run([str(VPY), "-m", "pip", *args, "--disable-pip-version-check"])
    return r.returncode == 0


# ---------------------------------------------------------------- venv
def ensure_venv():
    """Re-launch this script inside .venv (creating it if needed)."""
    if Path(sys.prefix).resolve() == VENV.resolve():
        return
    if not (PY_MIN <= sys.version_info[:2] <= PY_MAX):
        fail(f"需要 Python {PY_MIN[0]}.{PY_MIN[1]}–{PY_MAX[0]}.{PY_MAX[1]}，目前是 {sys.version.split()[0]}。"
             "\n  請安裝 Python 3.12：https://www.python.org/downloads/")
    if not VPY.exists():
        step(f"建立虛擬環境 .venv（Python {sys.version.split()[0]}）")
        venv.EnvBuilder(with_pip=True, clear=VENV.exists()).create(VENV)
        run([str(VPY), "-m", "pip", "install", "-q", "--upgrade", "pip", "--disable-pip-version-check"])
    sys.exit(run([str(VPY), str(Path(__file__).resolve()), *sys.argv[1:]]).returncode)


# ---------------------------------------------------------------- torch
def driver_cuda() -> float:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return 0.0
    try:
        out = run([exe], capture_output=True, text=True, timeout=20).stdout or ""
        # "CUDA Version: 12.8" (older drivers) or "CUDA UMD Version: 13.3" (newer drivers)
        m = re.search(r"CUDA(?:\s+UMD)?\s+Version:\s*([\d.]+)", out)
        if m:
            return float(m.group(1))
        # fall back to the minimum driver version each CUDA release needs
        q = run([exe, "--query-gpu=driver_version", "--format=csv,noheader"], capture_output=True, text=True, timeout=20)
        drv = float(q.stdout.strip().splitlines()[0].split(".")[0]) if q.returncode == 0 and q.stdout.strip() else 0
        for need, cuda in ((580, 13.0), (570, 12.8), (560, 12.6), (550, 12.4), (530, 12.1), (520, 11.8)):
            if drv >= need:
                return cuda
        return 11.8 if drv else 0.0
    except Exception:
        return 0.0


def torch_status():
    """(installed, cuda_build) for the torch inside the venv."""
    r = run([str(VPY), "-c", "import torch,sys;sys.stdout.write(torch.__version__)"], capture_output=True, text=True)
    if r.returncode != 0:
        return None, False
    return r.stdout.strip(), "+cu" in r.stdout


def ensure_torch(force_cpu: bool, reinstall: bool) -> str:
    ver, is_cuda = torch_status()
    cuda = 0.0 if force_cpu else driver_cuda()
    if ver and not reinstall and (is_cuda or cuda == 0.0):
        return ver
    if cuda:
        step(f"安裝 PyTorch（偵測到 NVIDIA 顯示卡，驅動支援 CUDA {cuda}）— 約 2–3 GB，請稍候")
        for tag, need in TORCH_CUDA:
            if cuda + 1e-6 < need:
                continue
            say(f"  嘗試 {tag} …")
            if pip("install", "--upgrade", "torch", "--index-url", f"https://download.pytorch.org/whl/{tag}"):
                ok = run([str(VPY), "-c", "import torch,sys;sys.exit(0 if torch.cuda.is_available() else 1)"]).returncode == 0
                if ok:
                    return torch_status()[0]
                say("  此版本無法使用 GPU，改試下一個…")
        say("  ⚠ 無法安裝可用的 CUDA 版 PyTorch，改用 CPU 版（轉錄會很慢）。可更新顯示卡驅動後以 start.bat --reinstall 重試。")
    else:
        step("安裝 PyTorch（CPU 版）")
        say("  ⚠ 未偵測到 NVIDIA 顯示卡，轉錄會非常慢（建議使用 NVIDIA GPU）。")
    if not pip("install", "--upgrade", "torch", "--index-url", "https://download.pytorch.org/whl/cpu"):
        fail("PyTorch 安裝失敗，請檢查網路連線後重新執行 start.bat")
    return torch_status()[0]


# ---------------------------------------------------------------- requirements
def req_hash() -> str:
    return hashlib.sha256(REQ.read_bytes()).hexdigest()[:16]


def modules_ok() -> bool:
    code = "import importlib.util,sys;sys.exit(0 if all(importlib.util.find_spec(m) for m in %r) else 1)" % NEEDED_MODULES
    return run([str(VPY), "-c", code]).returncode == 0


def ensure_packages(torch_ver: str, reinstall: bool):
    state = json.loads(STATE.read_text("utf-8")) if STATE.exists() else {}
    if not reinstall and state.get("req") == req_hash() and state.get("torch") == torch_ver and modules_ok():
        return
    step("安裝其他套件")
    cons = VENV / "constraints.txt"
    cons.write_text(f"torch=={torch_ver}\n", encoding="utf-8")        # never let pip swap the CUDA build
    lines = [l for l in REQ.read_text("utf-8").splitlines() if l.strip() and not l.lstrip().startswith("#")]
    core = [l.split("#")[0].strip() for l in lines if not l.lower().startswith(("torch", "opencc"))]
    if not pip("install", "-c", str(cons), *core):
        fail("套件安裝失敗，請檢查網路連線後重新執行 start.bat（或加上 --reinstall）")
    if not pip("install", "-q", "opencc"):                               # optional
        say("  （opencc 未安裝，繁簡轉換保險機制將略過，不影響使用）")
    STATE.write_text(json.dumps({"req": req_hash(), "torch": torch_ver}), encoding="utf-8")


# ---------------------------------------------------------------- ffmpeg
def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    cands = [local / "Microsoft/WinGet/Links/ffmpeg.exe", ROOT / "ffmpeg/bin/ffmpeg.exe"]
    cands += sorted((local / "Microsoft/WinGet/Packages").glob("*FFmpeg*/**/bin/ffmpeg.exe"))
    return next((str(c) for c in cands if c.exists()), None)


def ensure_ffmpeg():
    exe = find_ffmpeg()
    if not exe and os.name == "nt" and shutil.which("winget"):
        step("找不到 FFmpeg（匯入與匯出影片需要）")
        ans = input("  要用 winget 自動安裝 FFmpeg 嗎？[Y/n] ").strip().lower()
        if ans in ("", "y", "yes"):
            run(["winget", "install", "-e", "--id", "Gyan.FFmpeg", "--accept-source-agreements", "--accept-package-agreements"])
            exe = find_ffmpeg()
    if not exe:
        fail("需要 FFmpeg。請從 https://www.gyan.dev/ffmpeg/builds/ 下載，解壓後把 bin 資料夾加入 PATH，"
             "\n  或放到本專案的 ffmpeg\\bin\\ffmpeg.exe，再重新執行 start.bat")
    os.environ["PATH"] = str(Path(exe).parent) + os.pathsep + os.environ.get("PATH", "")


# ---------------------------------------------------------------- models
def ensure_models():
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    from huggingface_hub import hf_hub_download, snapshot_download
    todo = []
    for m in MODELS:
        try:
            snapshot_download(m, local_files_only=True)
        except Exception:
            todo.append(m)
    try:
        hf_hub_download("onnx-community/silero-vad", "onnx/model.onnx", local_files_only=True)
    except Exception:
        todo.append("silero-vad")
    if not todo:
        return
    step("下載語音模型（首次約 4.5 GB，之後不需要再下載）")
    for m in todo:
        say(f"  {m}")
        try:
            if m == "silero-vad":
                hf_hub_download("onnx-community/silero-vad", "onnx/model.onnx")
            else:
                snapshot_download(m)
        except Exception as e:
            fail(f"模型下載失敗：{e}\n  請檢查網路連線後重新執行 start.bat"
                 "\n  （無法連上 huggingface.co 時，可先設定環境變數 HF_ENDPOINT 使用鏡像站）")


# ---------------------------------------------------------------- main
def main():
    ensure_venv()
    args = sys.argv[1:]
    force_cpu, reinstall = "--cpu" in args, "--reinstall" in args
    passthrough = [a for a in args if a not in ("--cpu", "--reinstall")]
    say("LumenSubs 環境檢查中…")
    tv = ensure_torch(force_cpu, reinstall)
    ensure_packages(tv, reinstall)
    ensure_ffmpeg()
    ensure_models()
    say("✔ 環境就緒，啟動 LumenSubs…")
    sys.exit(run([str(VPY), str(ROOT / "run.py"), *passthrough]).returncode)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
