#!/usr/bin/env python3
"""
Chrome Extension Source Inspector

Downloads a Chrome extension by ID, unpacks it, and opens the source
in your editor. Works on Linux, WSL, and Windows

Usage:
    python crxreader.py <extension_id>
    python crxreader.py <extension_id> --ide cursor --output ~/extensions
    python crxreader.py --save-defaults --env wsl --output ~/extensions --ide code

Get the extension ID from the Chrome Web Store URL:
    https://chromewebstore.google.com/detail/<name>/<id>

Dependencies:
    pip install requests
"""

import argparse
import configparser
import json
import platform
import re
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

import requests


# ── constants ─────────────────────────────────────────────────────────────────

CONFIG_PATH = Path.home() / ".crxreader.ini"
TEMP_DIR    = Path.home() / ".crxreader_tmp"

CRX_DOWNLOAD_URL = (
    "https://clients2.google.com/service/update2/crx"
    "?response=redirect&prodversion=120.0.0.0&acceptformat=crx3"
    "&x=id%3D{id}%26uc"
)

# crx3 files always start with these four bytes
CRX3_MAGIC = b"Cr24"

SUPPORTED_IDES = ["code", "code-insiders", "cursor", "idea", "pycharm", "webstorm", "subl", "zed"]

ENV_LINUX   = "linux"
ENV_WSL     = "wsl"
ENV_WINDOWS = "windows"

ENV_LABELS = {
    ENV_LINUX:   "Native Linux",
    ENV_WSL:     "WSL (Windows Subsystem for Linux)",
    ENV_WINDOWS: "Native Windows",
}


# ── environment ───────────────────────────────────────────────────────────────

def detect_environment() -> str:
    """reads /proc/version to distinguish wsl from plain linux."""
    if platform.system() == "Windows":
        return ENV_WINDOWS
    proc = Path("/proc/version")
    if proc.exists():
        text = proc.read_text(errors="ignore").lower()
        if "microsoft" in text or "wsl" in text:
            return ENV_WSL
    return ENV_LINUX


def confirm_environment(detected: str) -> str:
    """shown only on first run — lets the user correct auto-detection."""
    print(f"\n  Detected: {ENV_LABELS[detected]}")
    print("\n  1) Linux")
    print("  2) WSL  (copies output to Windows filesystem)")
    print("  3) Windows")

    choices = {"1": ENV_LINUX, "2": ENV_WSL, "3": ENV_WINDOWS}
    default = {v: k for k, v in choices.items()}.get(detected, "1")

    while True:
        answer = input(f"\n  Confirm environment [default {default}]: ").strip()
        if answer == "":
            return detected
        if answer in choices:
            return choices[answer]


# ── config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH)
    return dict(cfg["defaults"]) if "defaults" in cfg else {}


def save_config(output: str, ide: str, env: str) -> None:
    cfg = configparser.ConfigParser()
    cfg["defaults"] = {"output": output, "ide": ide, "env": env}
    with open(CONFIG_PATH, "w") as f:
        cfg.write(f)


# ── wsl helpers ───────────────────────────────────────────────────────────────

def wsl_to_windows_path(linux_path: Path) -> str:
    """converts /mnt/c/... to C:\\... using wslpath."""
    try:
        result = subprocess.run(
            ["wslpath", "-w", str(linux_path)],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def windows_desktop_path() -> Path | None:
    """asks powershell for the real desktop path — handles onedrive redirection."""
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "[Environment]::GetFolderPath('Desktop')"],
            capture_output=True, text=True, timeout=10,
        )
        win_path = result.stdout.strip()
        if win_path and ":" in win_path:
            # convert the windows path back to a wsl linux path
            result2 = subprocess.run(
                ["wslpath", "-u", win_path],
                capture_output=True, text=True, timeout=5,
            )
            linux_path = result2.stdout.strip()
            if linux_path:
                return Path(linux_path)
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return None


def find_vscode_exe() -> str | None:
    """searches common install locations for code.exe when it's not on PATH."""
    if shutil.which("code.exe"):
        return "code.exe"

    # ask cmd.exe for the windows username to build user-specific paths
    try:
        r = subprocess.run(
            ["cmd.exe", "/c", "echo %USERNAME%"],
            capture_output=True, text=True, timeout=5,
        )
        username = r.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        username = ""

    # common install locations in priority order
    candidates = []
    if username and "%" not in username:
        candidates += [
            f"/mnt/c/Users/{username}/AppData/Local/Programs/Microsoft VS Code/bin/code",
            f"/mnt/c/Users/{username}/AppData/Local/Programs/Microsoft VS Code/Code.exe",
        ]
    candidates += [
        "/mnt/c/Program Files/Microsoft VS Code/bin/code",
        "/mnt/c/Program Files (x86)/Microsoft VS Code/bin/code",
    ]

    for path in candidates:
        if Path(path).exists():
            return path

    return None


# ── crx download and unpacking ────────────────────────────────────────────────

def download_crx(extension_id: str, dest: Path) -> None:
    url = CRX_DOWNLOAD_URL.format(id=extension_id)
    print(f"  downloading  {url}")

    try:
        response = requests.get(url, stream=True, timeout=30, allow_redirects=True)
    except requests.RequestException as err:
        sys.exit(f"\n  error: {err}")

    if response.status_code != 200:
        sys.exit(
            f"\n  download failed (HTTP {response.status_code})\n"
            f"  check the extension id and that it is publicly available"
        )

    with open(dest, "wb") as f:
        for chunk in response.iter_content(8192):
            f.write(chunk)


def crx_to_zip_bytes(crx_path: Path) -> bytes:
    """strips the crx3 protobuf header and returns raw zip bytes.

    crx3 layout:
        [0:4]   magic "Cr24"
        [4:8]   version (uint32 le) = 3
        [8:12]  protobuf header length (uint32 le)
        [12:12+header_len]  protobuf header (skip)
        [12+header_len:]    zip data
    """
    data = crx_path.read_bytes()

    if not data.startswith(CRX3_MAGIC):
        sys.exit(
            f"\n  not a valid crx3 file (got {data[:4]!r})\n"
            f"  the extension may require a login or isn't publicly available"
        )

    # protobuf header length sits at bytes 8-11
    header_len = struct.unpack_from("<I", data, 8)[0]
    return data[12 + header_len:]


def unpack_zip(zip_bytes: bytes, dest: Path) -> int:
    """writes zip bytes to a temp file, extracts, cleans up. returns file count."""
    tmp = dest.parent / "_crxreader_tmp.zip"
    try:
        tmp.write_bytes(zip_bytes)
        with zipfile.ZipFile(tmp, "r") as z:
            z.extractall(dest)
        return sum(1 for p in dest.rglob("*") if p.is_file())
    except zipfile.BadZipFile:
        sys.exit("\n  the zip inside this crx is corrupt — download may be incomplete")
    finally:
        if tmp.exists():
            tmp.unlink()


# ── folder naming ─────────────────────────────────────────────────────────────

def folder_name_from_manifest(manifest_path: Path, fallback: str) -> str:
    """reads the extension name from manifest and makes it filesystem-safe."""
    try:
        name = json.loads(manifest_path.read_text(encoding="utf-8")).get("name", "")
        if name and not name.startswith("__MSG_"):
            # lowercase, replace spaces and special chars with underscores
            safe = re.sub(r"[^\w]+", "_", name.strip().lower()).strip("_")
            if safe:
                return safe
    except (json.JSONDecodeError, OSError):
        pass
    return fallback


# ── ide launcher ─────────────────────────────────────────────────────────────

def open_in_ide(folder: Path, ide: str, env: str) -> None:
    if env == ENV_WSL:
        _open_wsl(folder, ide)
    elif env == ENV_WINDOWS:
        _open_windows(folder, ide)
    else:
        _open_linux(folder, ide)


def _open_wsl(folder: Path, ide: str) -> None:
    win_path = wsl_to_windows_path(folder)

    # vs code gets special treatment — search common install locations
    if ide in ("code", "code-insiders"):
        exe = find_vscode_exe()
        if exe:
            target = win_path or str(folder)
            print(f"\n  opening  {exe}")
            subprocess.Popen([exe, target])
            return

    # any other ide: try <name>.exe on PATH first, then linux binary
    exe_name = ide if ide.endswith(".exe") else ide + ".exe"
    if shutil.which(exe_name):
        subprocess.Popen([exe_name, win_path or str(folder)])
        return
    if shutil.which(ide):
        subprocess.Popen([ide, str(folder)])
        return

    # fall back to explorer
    if win_path and shutil.which("explorer.exe"):
        print(f"\n  ide not found — opening explorer at {win_path}")
        subprocess.Popen(["explorer.exe", win_path])
    else:
        print(f"\n  open manually: {folder}")


def _open_linux(folder: Path, ide: str) -> None:
    if shutil.which(ide):
        print(f"\n  opening  {ide}")
        subprocess.Popen([ide, str(folder)])
        return
    for fm in ["xdg-open", "nautilus", "thunar", "dolphin"]:
        if shutil.which(fm):
            subprocess.Popen([fm, str(folder)])
            return
    print(f"\n  open manually: {folder}")


def _open_windows(folder: Path, ide: str) -> None:
    for candidate in [ide, ide + ".exe"]:
        if shutil.which(candidate):
            subprocess.Popen([candidate, str(folder)])
            return
    subprocess.Popen(["explorer", str(folder)])


# ── manifest summary ──────────────────────────────────────────────────────────

def print_summary(folder: Path) -> None:
    manifest_path = folder / "manifest.json"
    if not manifest_path.exists():
        return

    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    bg            = m.get("background", {})
    service_worker = bg.get("service_worker", "")
    bg_scripts    = bg.get("scripts", [])
    content_scripts = [s for cs in m.get("content_scripts", []) for s in cs.get("js", [])]
    permissions   = m.get("permissions", [])
    host_perms    = m.get("host_permissions", [])

    print(f"\n  {'─' * 52}")
    print(f"  {m.get('name', '?')}  v{m.get('version', '?')}  (mv{m.get('manifest_version', '?')})")
    print(f"  {'─' * 52}")

    if service_worker:
        print(f"  service worker   {service_worker}")
    for s in bg_scripts:
        print(f"  background       {s}")
    for s in content_scripts:
        print(f"  content script   {s}")
    if permissions:
        print(f"  permissions      {', '.join(permissions)}")
    if host_perms:
        print(f"  host access      {', '.join(host_perms)}")

    print(f"\n  location  {folder}")
    print(f"\n  tip: paste js files into https://deobfuscate.relative.im/ to make them readable")
    print(f"  {'─' * 52}\n")


# ── resolve options ───────────────────────────────────────────────────────────

def resolve_env(args: argparse.Namespace, config: dict) -> str:
    """env flag > saved config > auto-detect (with confirm prompt on first run)."""
    if args.env:
        return args.env
    if config.get("env") in (ENV_LINUX, ENV_WSL, ENV_WINDOWS):
        return config["env"]
    # first run — detect and ask
    return confirm_environment(detect_environment())


def resolve_output(args: argparse.Namespace, config: dict, env: str) -> str:
    if args.output:
        return args.output
    if config.get("output"):
        return config["output"]

    # smart default for wsl: use the actual windows desktop (handles onedrive)
    if env == ENV_WSL:
        desktop = windows_desktop_path()
        if desktop:
            path = str(desktop / "crx_extensions")
            print(f"  output defaulting to windows desktop: {path}")
            return path
        return "/mnt/c/crx_extensions"

    return str(Path.home() / "crx_extensions")


# ── input parsing ─────────────────────────────────────────────────────────────

def parse_extension_id(raw: str) -> str:
    """accepts either a bare id or a full chrome web store url.

    url form:  https://chromewebstore.google.com/detail/<name>/<id>
    bare form: mnakbpdnkedaegeiaoakkjafhoidklnf
    """
    raw = raw.strip()
    if raw.startswith("http"):
        # id is always the last path segment
        segment = raw.rstrip("/").split("/")[-1]
        # strip any query string or fragment
        return re.split(r"[?#]", segment)[0]
    return raw


# ── main ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crxreader",
        description="Download and inspect a Chrome extension's source code.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python crxreader.py ofaokhiedipichpaobibbnahnkdoiiah\n"
            "  python crxreader.py https://chromewebstore.google.com/detail/vortimo-osint-tool/mnakbpdnkedaegeiaoakkjafhoidklnf\n"
            "  python crxreader.py ofaokhiedipichpaobibbnahnkdoiiah --ide cursor\n"
            "  python crxreader.py --save-defaults --env wsl --output ~/extensions --ide code\n\n"
            f"supported editors: {', '.join(SUPPORTED_IDES)}"
        ),
    )
    parser.add_argument("extension_id", nargs="?", help="extension id or full chrome web store url")
    parser.add_argument("--output", "-o", metavar="DIR", help="where to save extracted files")
    parser.add_argument("--ide", "-i", metavar="EDITOR", help="editor to open the folder in")
    parser.add_argument("--env", choices=[ENV_LINUX, ENV_WSL, ENV_WINDOWS], help="override environment detection")
    parser.add_argument("--save-defaults", action="store_true", help="save current options as defaults")
    parser.add_argument("--no-open", action="store_true", help="extract files without opening an editor")
    return parser


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()
    config = load_config()

    env    = resolve_env(args, config)
    output = resolve_output(args, config, env)
    ide    = args.ide or config.get("ide") or "code"

    if args.save_defaults:
        save_config(output, ide, env)
        print(f"\n  defaults saved")
        print(f"  env     {ENV_LABELS[env]}")
        print(f"  output  {output}")
        print(f"  ide     {ide}\n")
        return

    if not args.extension_id:
        parser.print_help()
        sys.exit(0)

    extension_id = parse_extension_id(args.extension_id)

    if len(extension_id) != 32 or not extension_id.isalnum():
        print(f"\n  warning: '{extension_id}' doesn't look like a standard extension id (32 chars)")

    print(f"\n  crxreader")
    print(f"  {'─' * 52}")
    print(f"  id       {extension_id}")
    print(f"  env      {ENV_LABELS[env]}")
    print(f"  output   {output}")
    print(f"  ide      {ide}")
    print(f"  {'─' * 52}\n")

    base_output = Path(output).expanduser().resolve()

    # for wsl: stage in linux tmp, then copy to windows
    if env == ENV_WSL:
        stage_dir = TEMP_DIR / extension_id
        crx_path  = TEMP_DIR / f"{extension_id}.crx"
    else:
        stage_dir = base_output / extension_id   # placeholder, renamed after manifest read
        crx_path  = base_output / f"{extension_id}.crx"

    TEMP_DIR.mkdir(parents=True, exist_ok=True) if env == ENV_WSL else base_output.mkdir(parents=True, exist_ok=True)

    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)

    # download
    download_crx(extension_id, crx_path)
    size_kb = crx_path.stat().st_size // 1024
    print(f"  downloaded   {size_kb} KB")

    # unpack
    zip_bytes = crx_to_zip_bytes(crx_path)
    crx_path.unlink()
    file_count = unpack_zip(zip_bytes, stage_dir)
    print(f"  extracted    {file_count} files")

    # rename folder using the extension's actual name from manifest.json
    folder_name = folder_name_from_manifest(stage_dir / "manifest.json", extension_id)

    if env == ENV_WSL:
        # copy staged files to windows filesystem with the clean name
        final_dir = base_output / folder_name
        if final_dir.exists():
            shutil.rmtree(final_dir)
        base_output.mkdir(parents=True, exist_ok=True)
        shutil.copytree(stage_dir, final_dir)
        shutil.rmtree(TEMP_DIR, ignore_errors=True)

        win_path = wsl_to_windows_path(final_dir)
        print(f"  copied to    {win_path or final_dir}")
    else:
        # rename the extraction directory in place
        final_dir = base_output / folder_name
        if final_dir.exists() and final_dir != stage_dir:
            shutil.rmtree(final_dir)
        if stage_dir != final_dir:
            stage_dir.rename(final_dir)

    print_summary(final_dir)

    if not args.no_open:
        open_in_ide(final_dir, ide, env)


if __name__ == "__main__":
    main()
