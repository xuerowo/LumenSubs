"""LumenSubs one-click setup & launcher (called by start.bat).

First run:  creates .venv, installs PyTorch (CUDA build matched to the GPU
driver, or CPU), installs requirements, makes sure FFmpeg is available and
downloads the models. Later runs only verify and start the app (fast).
Everything that is installed is also logged to workspace/logs/setup.log.

Options (pass to start.bat):  --cpu        use CPU PyTorch (remembered; --reinstall resets it)
                              --reinstall  re-detect the GPU, reinstall PyTorch and update
                                           all other packages
                              --no-browser start the server only
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VPY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STATE = VENV / "lumen_setup.json"
REQ = ROOT / "requirements.txt"
LOCK = ROOT / "requirements.lock"
LOG = ROOT / "workspace" / "logs" / "setup.log"
PY_MIN, PY_MAX = (3, 10), (3, 13)
OPTIONS = {"--cpu", "--reinstall", "--no-browser"}
TORCH_SPEC = "torch>=2.6,<2.15"          # keep in step with requirements.txt

MODELS = ["Qwen/Qwen3-ASR-1.7B", "Qwen/Qwen3-ForcedAligner-0.6B"]
MODELS_GB = 6.5                          # download size of both models
VENV_GB = 6.0                            # PyTorch + other packages, incl. pip's cache
# CUDA wheel tags published by PyTorch, newest first, with the minimum
# driver-reported CUDA version each needs
TORCH_CUDA = [("cu130", 13.0), ("cu129", 12.9), ("cu128", 12.8), ("cu126", 12.6), ("cu124", 12.4), ("cu121", 12.1), ("cu118", 11.8)]
# minimum NVIDIA driver (major version) per CUDA release, from NVIDIA's CUDA
# toolkit release notes ("CUDA Toolkit and Corresponding Driver Versions"),
# used when nvidia-smi does not print the CUDA version
DRIVER_CUDA = ((580, 13.0), (570, 12.8), (560, 12.6), (550, 12.4), (530, 12.1), (520, 11.8))
NEEDED_MODULES = ["fastapi", "uvicorn", "multipart", "openai", "numpy", "PIL", "onnxruntime", "qwen_asr",
                  "transformers", "soundfile", "librosa", "huggingface_hub", "torch"]
# runs inside .venv: PyTorch must really compute on the GPU (a GPU that is too
# old for this build reports "available" but fails with "no kernel image")
GPU_CHECK = ("import torch,sys\n"
             "if not torch.cuda.is_available(): sys.exit(1)\n"
             "x = (torch.ones(64, device='cuda') * 2).sum().item()\n"
             "sys.exit(0 if x == 128 else 2)")


def say(msg=""):
    print(msg, flush=True)
    _log(msg)


def step(msg):
    say(f"\n▶ {msg}")


def fail(msg, code=1):
    say(f"\n✖ {msg}")
    if LOG.exists():
        say(f"  （完整安裝紀錄：{LOG}）")
    sys.exit(code)


def _log(text: str):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(text.rstrip("\n") + "\n")
    except OSError:
        pass


def run(cmd, **kw):
    return subprocess.run(cmd, **kw)


def run_logged(cmd) -> tuple:
    """Run cmd showing its output live and appending it to setup.log;
    returns (exit code, last lines of output) so failures can be explained."""
    _log(f"$ {' '.join(map(str, cmd))}")
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                             errors="replace", bufsize=1)
    except OSError as e:
        _log(str(e))
        return 1, str(e)
    tail = []
    for line in p.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        _log(line)
        tail = (tail + [line])[-60:]
    p.wait()
    return p.returncode, "".join(tail)


def explain(output: str) -> str:
    """Turn the tail of a pip / download error into advice a non-expert can act on."""
    t = output.lower()
    if "no space left" in t or "not enough space" in t or "errno 28" in t or "磁碟空間不足" in t:
        return "磁碟空間不足。請清出空間（C 槽與專案所在磁碟各需數 GB）後重新執行 start.bat。"
    if "certificate" in t or "ssl" in t:
        return ("連線被攔截（SSL 憑證錯誤），常見於公司網路或防毒軟體的 HTTPS 檢查。"
                "請改用其他網路，或請 IT 提供代理伺服器設定（環境變數 HTTPS_PROXY）。")
    if "proxy" in t or "407" in t:
        return "代理伺服器拒絕連線。請設定環境變數 HTTPS_PROXY（例如 http://帳號:密碼@proxy:8080）後重試。"
    if "filename or extension is too long" in t or "path too long" in t or "winerror 206" in t:
        return "資料夾路徑太長。請把 LumenSubs 移到較短的路徑（例如 D:\\LumenSubs）後重新執行 start.bat。"
    if "access is denied" in t or "permission denied" in t or "winerror 5" in t or "winerror 32" in t:
        return "檔案被鎖住或沒有權限（常見原因：防毒軟體正在掃描、另一個 LumenSubs 還開著）。請關閉後重試。"
    if "no matching distribution" in t or "could not find a version" in t:
        return f"找不到適用於 Python {sys.version.split()[0]} 的套件版本。建議改用 Python 3.12。"
    if any(w in t for w in ("timed out", "timeout", "connection", "temporary failure", "getaddrinfo", "network",
                            "remote end closed", "reset by peer", "read error")):
        return "網路連線中斷或不穩定。請確認網路後重新執行 start.bat（已下載的部分不必重來）。"
    return "請查看上方訊息；若無法解決，請把安裝紀錄檔一併回報。"


def is_env_error(output: str) -> bool:
    """A download or machine problem (network, disk, path, lock) rather than a
    package that does not fit: trying other versions would not help."""
    return explain(output).startswith(("網路", "連線被攔截", "代理", "磁碟", "資料夾路徑", "檔案被鎖"))


def pip(*args) -> tuple:
    return run_logged([str(VPY), "-m", "pip", *args, "--disable-pip-version-check"])


def free_gb(path: Path) -> float:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(str(p)).free / 2 ** 30
    except OSError:
        return 1e9


def need_space(path: Path, gb: float, what: str):
    free = free_gb(path)
    if free < gb:
        drive = Path(path).anchor or str(path)
        fail(f"{drive} 空間不足：{what}需要約 {gb:.0f} GB，目前只剩 {free:.1f} GB。請清出空間後重新執行 start.bat。")


# ---------------------------------------------------------------- checks
def check_args(args):
    unknown = [a for a in args if a not in OPTIONS]
    if unknown:
        fail(f"不認得的參數：{' '.join(unknown)}\n  可用參數：--cpu（改用 CPU）、--reinstall（重新安裝）、--no-browser（只啟動伺服器）")


def check_location():
    p = str(ROOT)
    if len(p) > 90:
        say(f"  ⚠ 專案路徑很長（{len(p)} 字元），安裝 PyTorch 時可能超過 Windows 路徑長度上限。建議移到較短的路徑，例如 D:\\LumenSubs")
    if "onedrive" in p.lower():
        say("  ⚠ 專案位於 OneDrive 同步資料夾：數 GB 的程式與影片會被上傳雲端，同步中也可能鎖住檔案。建議移到 OneDrive 以外的資料夾。")


def check_tk():
    if run([str(VPY), "-c", "import tkinter"], capture_output=True).returncode != 0:
        say("  ⚠ 這個 Python 沒有 tcl/tk，「選擇資料夾」視窗將無法使用。重新安裝 Python 時請勾選「tcl/tk and IDLE」。")


def gpu_memory_gb() -> float:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return 0.0
    try:
        q = run([exe, "--query-gpu=memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=20)
        return float(q.stdout.strip().splitlines()[0]) / 1024 if q.returncode == 0 and q.stdout.strip() else 0.0
    except Exception:
        return 0.0


def hardware_warnings(cpu: bool):
    """The speech models need ~6.5 GB of GPU memory, or ~13 GB of RAM on the CPU."""
    sys.path.insert(0, str(ROOT))
    try:
        from app.sysinfo import NEED_RAM_CPU_GB, NEED_VRAM_GB, ram_gb
    except Exception:
        return
    vram = 0.0 if cpu else gpu_memory_gb()
    if vram and vram < NEED_VRAM_GB:
        say(f"  ⚠ 顯示卡記憶體 {vram:.1f} GB，語音模型約需 {NEED_VRAM_GB} GB，可能無法載入（屆時可在「設定」改用 CPU）。")
    ram = ram_gb() or 0
    if (cpu or not vram) and ram and ram < NEED_RAM_CPU_GB:
        say(f"  ⚠ 電腦記憶體 {ram:.0f} GB，以 CPU 轉錄建議至少 {NEED_RAM_CPU_GB:.0f} GB，可能非常緩慢或失敗。")


# ---------------------------------------------------------------- venv
def ensure_venv():
    """Re-launch this script inside .venv (creating it if needed)."""
    if Path(sys.prefix).resolve() == VENV.resolve():
        return
    if not (PY_MIN <= sys.version_info[:2] <= PY_MAX):
        fail(f"需要 Python {PY_MIN[0]}.{PY_MIN[1]}–{PY_MAX[0]}.{PY_MAX[1]}，目前是 {sys.version.split()[0]}。"
             "\n  請安裝 Python 3.12：https://www.python.org/downloads/release/python-31210/"
             "\n  （或在命令提示字元執行：winget install Python.Python.3.12）")
    broken = VPY.exists() and run([str(VPY), "-c", "import sys"], capture_output=True).returncode != 0
    if broken:
        say("  .venv 已損壞（例如原本的 Python 已移除），將重新建立")
    if not VPY.exists() or broken:
        need_space(ROOT, VENV_GB, "安裝程式套件")
        step(f"建立虛擬環境 .venv（Python {sys.version.split()[0]}）")
        venv.EnvBuilder(with_pip=True, clear=VENV.exists()).create(VENV)
        run([str(VPY), "-m", "pip", "install", "-q", "--upgrade", "pip", "--disable-pip-version-check"])
    sys.exit(run([str(VPY), str(Path(__file__).resolve()), *sys.argv[1:]]).returncode)


# ---------------------------------------------------------------- setup state
def load_state() -> dict:
    try:
        return json.loads(STATE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(**kw):
    st = load_state()
    st.update(kw)
    st = {k: v for k, v in st.items() if v is not None}
    STATE.write_text(json.dumps(st), encoding="utf-8")


# ---------------------------------------------------------------- torch
def parse_cuda(smi_output: str) -> float:
    # "CUDA Version: 12.8" (older drivers) or "CUDA UMD Version: 13.3" (newer drivers)
    m = re.search(r"CUDA(?:\s+UMD)?\s+Version:\s*([\d.]+)", smi_output or "")
    return float(m.group(1)) if m else 0.0


def cuda_for_driver(driver_major: float) -> float:
    for need, cuda in DRIVER_CUDA:
        if driver_major >= need:
            return cuda
    return 11.8 if driver_major else 0.0


def driver_cuda() -> float:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return 0.0
    try:
        v = parse_cuda(run([exe], capture_output=True, text=True, timeout=20).stdout or "")
        if v:
            return v
        q = run([exe, "--query-gpu=driver_version", "--format=csv,noheader"], capture_output=True, text=True, timeout=20)
        drv = float(q.stdout.strip().splitlines()[0].split(".")[0]) if q.returncode == 0 and q.stdout.strip() else 0
        return cuda_for_driver(drv)
    except Exception:
        return 0.0


def torch_status():
    """(installed, cuda_build) for the torch inside the venv."""
    r = run([str(VPY), "-c", "import torch,sys;sys.stdout.write(torch.__version__)"], capture_output=True, text=True)
    if r.returncode != 0:
        return None, False
    return r.stdout.strip(), "+cu" in r.stdout


def install_torch(tag: str, replace: bool) -> tuple:
    args = ["install", "--upgrade", TORCH_SPEC, "--index-url", f"https://download.pytorch.org/whl/{tag}"]
    if replace:             # builds (+cpu, +cu128, +cu130…) share a version number: pip would keep the old one
        args.insert(1, "--force-reinstall")
    return pip(*args)


def gpu_works() -> bool:
    return run([str(VPY), "-c", GPU_CHECK], capture_output=True).returncode == 0


def ensure_torch(force_cpu: bool, reinstall: bool) -> str:
    if force_cpu:
        save_state(cpu=True)
    elif reinstall:
        save_state(cpu=None, cuda_failed=None)
    st = load_state()
    ver, is_cuda = torch_status()
    cuda = 0.0 if st.get("cpu") else driver_cuda()
    if ver and not reinstall:
        if is_cuda and not st.get("cpu"):
            return ver
        # CPU build: fine without a GPU, or when no CUDA build works with this driver
        if not is_cuda and (cuda == 0.0 or st.get("cuda_failed") == cuda):
            return ver
    need_space(VENV, VENV_GB if not ver else 3, "安裝 PyTorch")
    if cuda:
        step(f"安裝 PyTorch（偵測到 NVIDIA 顯示卡，驅動支援 CUDA {cuda}）— 約 2–3 GB，請稍候")
        for tag, need in TORCH_CUDA:
            if cuda + 1e-6 < need:
                continue
            say(f"  嘗試 {tag} …")
            rc, out = install_torch(tag, replace=bool(ver))
            if rc != 0:
                if is_env_error(out):
                    # a download problem says nothing about the GPU: stop instead of
                    # falling back to (and remembering) the slow CPU build
                    fail("PyTorch 下載失敗：" + explain(out))
                say("  此版本無法安裝，改試下一個…")
                continue
            if gpu_works():
                return torch_status()[0]
            say("  此版本無法在這張顯示卡上運算（顯示卡可能較舊），改試下一個…")
            ver = torch_status()[0]
        save_state(cuda_failed=cuda)       # don't retry on every start with this driver
        say("  ⚠ 沒有可在這張顯示卡上執行的 CUDA 版 PyTorch，改用 CPU 版（轉錄會很慢）。"
            "\n    更新顯示卡驅動後，可執行 start.bat --reinstall 再試一次。")
    else:
        step("安裝 PyTorch（CPU 版）")
        if st.get("cpu"):
            say("  （已指定使用 CPU；要改回 GPU 請執行 start.bat --reinstall）")
        else:
            say("  ⚠ 未偵測到 NVIDIA 顯示卡，轉錄會非常慢（建議使用 NVIDIA GPU；AMD／Intel 顯示卡目前只能使用 CPU）。")
    ver, is_cuda = torch_status()
    rc, out = install_torch("cpu", replace=is_cuda)
    if rc != 0:
        fail("PyTorch 安裝失敗：" + explain(out))
    return torch_status()[0]


# ---------------------------------------------------------------- requirements
def req_hash() -> str:
    h = hashlib.sha256(REQ.read_bytes())
    if LOCK.exists():
        h.update(LOCK.read_bytes())
    return h.hexdigest()[:16]


def modules_ok() -> bool:
    code = "import importlib.util,sys;sys.exit(0 if all(importlib.util.find_spec(m) for m in %r) else 1)" % NEEDED_MODULES
    return run([str(VPY), "-c", code]).returncode == 0


def ensure_packages(torch_ver: str, reinstall: bool):
    state = load_state()
    if not reinstall and state.get("req") == req_hash() and state.get("torch") == torch_ver and modules_ok():
        return
    step("安裝其他套件")
    cons = VENV / "constraints.txt"
    cons.write_text(f"torch=={torch_ver}\n", encoding="utf-8")        # never let pip swap the CUDA build
    lines = [l for l in REQ.read_text("utf-8").splitlines() if l.strip() and not l.lstrip().startswith("#")]
    core = [l.split("#")[0].strip() for l in lines if not l.lower().startswith(("torch", "opencc"))]
    up = ["--upgrade"] if reinstall else []
    rc, out = 1, ""
    if LOCK.exists():
        # known-good versions first; a pin that does not exist for this Python falls through
        rc, out = pip("install", *up, "-c", str(cons), "-c", str(LOCK), *core)
        if rc != 0 and not is_env_error(out):
            say("  （已知可用的版本組合不適用於這個 Python，改為安裝相容的最新版本）")
    if rc != 0 and (not LOCK.exists() or not is_env_error(out)):
        rc, out = pip("install", *up, "-c", str(cons), *core)
    if rc != 0:
        fail("套件安裝失敗：" + explain(out) + "\n  （可加上 --reinstall 重新安裝）")
    opencc = next((l.split("#")[0].strip() for l in lines if l.lower().startswith("opencc")), "opencc")
    if pip("install", "-q", "-c", str(cons), opencc)[0] != 0:                # optional
        say("  （opencc 未安裝，繁簡轉換保險機制將略過，不影響使用）")
    save_state(req=req_hash(), torch=torch_ver)


# ---------------------------------------------------------------- ffmpeg
def find_ffmpeg():
    """Folder that holds both ffmpeg and ffprobe, or None."""
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    exe = ".exe" if os.name == "nt" else ""
    cands = [ROOT / "ffmpeg" / "bin" / f"ffmpeg{exe}"]
    if shutil.which("ffmpeg"):
        cands.append(Path(shutil.which("ffmpeg")))
    cands += [local / "Microsoft/WinGet/Links/ffmpeg.exe"]
    cands += sorted((local / "Microsoft/WinGet/Packages").glob("*FFmpeg*/**/bin/ffmpeg.exe"))
    for c in cands:
        if c.exists() and (c.parent / f"ffprobe{exe}").exists():
            return str(c.parent)
    return None


def ensure_ffmpeg():
    folder = find_ffmpeg()
    if not folder and os.name == "nt" and shutil.which("winget"):
        step("找不到 FFmpeg（匯入與匯出影片需要）")
        ans = input("  要用 winget 自動安裝 FFmpeg 嗎？[Y/n] ").strip().lower()
        if ans in ("", "y", "yes"):
            rc, out = run_logged(["winget", "install", "-e", "--id", "Gyan.FFmpeg", "--accept-source-agreements",
                                  "--accept-package-agreements"])
            folder = find_ffmpeg()
            if not folder and rc != 0:
                say("  winget 安裝失敗：" + explain(out))
    if not folder:
        fail("需要 FFmpeg（含 ffprobe）。最簡單的方式："
             "\n  1. 從 https://www.gyan.dev/ffmpeg/builds/ 下載「ffmpeg-release-essentials.zip」並解壓縮"
             f"\n  2. 把裡面的 bin 資料夾整個複製到 {ROOT / 'ffmpeg'}（成為 {ROOT / 'ffmpeg' / 'bin' / 'ffmpeg.exe'}）"
             "\n  3. 重新執行 start.bat")
    os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")


# ---------------------------------------------------------------- models
def ensure_models():
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    sys.path.insert(0, str(ROOT))
    from app import config
    todo = [m for m in MODELS if not config.model_snapshot(m)]
    if not config.vad_model_path():
        todo.append("silero-vad")
    if not todo:
        return
    from huggingface_hub import hf_hub_download, snapshot_download
    need_space(config.hf_hub_dir(), MODELS_GB + 1 if len(todo) > 1 else 3, "下載語音模型")
    step(f"下載語音模型（首次約 {MODELS_GB:.1f} GB，存放於 {config.hf_hub_dir()}；中斷後重新執行會接續下載）")
    for m in todo:
        say(f"  {m}")
        for attempt in range(3):
            try:
                if m == "silero-vad":
                    hf_hub_download(config.VAD_MODEL, "onnx/model.onnx")
                else:
                    snapshot_download(m)          # resumes partial files, fetches only what is missing
                break
            except Exception as e:
                if attempt == 2:
                    fail(f"模型下載失敗：{explain(str(e))}\n  （{str(e)[:200]}）"
                         "\n  無法連上 huggingface.co 時，可設定環境變數 HF_ENDPOINT 使用你信任的鏡像站。")
                say(f"  下載中斷，{5 * (attempt + 1)} 秒後重試…")
                time.sleep(5 * (attempt + 1))
        if m != "silero-vad" and not config.model_snapshot(m):
            fail(f"{m} 下載後仍不完整，請刪除 {config.hf_hub_dir() / ('models--' + m.replace('/', '--'))} 後重新執行 start.bat")


# ---------------------------------------------------------------- fonts
def ensure_fonts():
    """UI + default subtitle fonts, cached locally so the app also works offline
    (other fonts are fetched the first time they are used)."""
    sys.path.insert(0, str(ROOT))
    try:
        from app import fonts
    except Exception as e:
        say(f"  （無法載入字型模組：{e}，第一次使用時再下載）")
        return
    if all((fonts.FONT_DIR / fonts.file_name(f, w)).exists() for f, w in fonts.DEFAULTS):
        return
    step("下載介面與字幕字型（約 22 MB，之後可離線使用）")
    if not fonts.prefetch(say=say):
        say("  ⚠ 字型未能全部下載，會在第一次使用時再試（離線時改用系統字型）")


# ---------------------------------------------------------------- main
def main():
    args = sys.argv[1:]
    check_args(args)
    ensure_venv()
    _log(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start.bat {' '.join(args)} (Python {sys.version.split()[0]})")
    force_cpu, reinstall = "--cpu" in args, "--reinstall" in args
    passthrough = [a for a in args if a not in ("--cpu", "--reinstall")]
    say("LumenSubs 環境檢查中…")
    check_location()
    tv = ensure_torch(force_cpu, reinstall)
    ensure_packages(tv, reinstall)
    ensure_ffmpeg()
    ensure_models()
    ensure_fonts()
    check_tk()
    hardware_warnings(force_cpu or load_state().get("cpu") or "+cu" not in tv)
    say("✔ 環境就緒，啟動 LumenSubs…")
    sys.exit(run([str(VPY), str(ROOT / "run.py"), *passthrough]).returncode)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
