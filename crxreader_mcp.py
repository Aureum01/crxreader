#!/usr/bin/env python3
"""
crxreader_mcp.py — MCP server for Chrome Extension inspection

Exposes crxreader as an MCP tool server so any MCP-compatible AI client
can download, unpack, and read Chrome extensions conversationally.

Tools:
    download_extension   download + unpack an extension by URL or ID
    list_files           list all files in an unpacked extension
    read_file            read a specific file from the unpacked extension
    search_files         search across all source files for a pattern or regex

Setup:
    pip install mcp requests

Stdio mode (Claude Desktop on macOS/Linux):
    python crxreader_mcp.py

SSE mode (Ollama via ollmcp, or any SSE-compatible MCP client):
    python crxreader_mcp.py --transport sse --port 8000

    Then connect with:
    ollmcp --host http://<ollama-host>:11434 \\
           --servers-json mcp_servers.json \\
           --model qwen3:8b

    Where mcp_servers.json contains:
    {
      "mcpServers": {
        "crxreader": {
          "command": "/path/to/venv/bin/python",
          "args": ["/path/to/crxreader_mcp.py"]
        }
      }
    }

Claude Desktop config (macOS/Linux — stdio):
    {
      "mcpServers": {
        "crxreader": {
          "command": "/path/to/venv/bin/python",
          "args": ["/path/to/crxreader_mcp.py"]
        }
      }
    }

Note on Windows:
    Claude Desktop on Windows currently has a known MSIX packaging bug
    that silently ignores local MCP server config. Use Ollama + ollmcp
    via WSL as shown above until Anthropic ships a fix.
"""

import json
import os
import re
import struct
import sys
import zipfile
from pathlib import Path

import requests
from mcp.server.fastmcp import FastMCP

# ── constants ─────────────────────────────────────────────────────────────────

# workspace where unpacked extensions are stored between tool calls
WORKSPACE      = Path.home() / ".crxreader_mcp"
MAX_FILE_BYTES = 100_000
CRX3_MAGIC     = b"Cr24"

CRX_DOWNLOAD_URL = (
    "https://clients2.google.com/service/update2/crx"
    "?response=redirect&prodversion=120.0.0.0&acceptformat=crx3"
    "&x=id%3D{id}%26uc"
)

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".woff", ".woff2", ".ttf", ".eot", ".mp3", ".mp4",
    ".wav", ".zip", ".crx",
}


# ── helpers ───────────────────────────────────────────────────────────────────

def parse_extension_id(raw: str) -> str:
    """accepts a bare id or a full chrome web store url."""
    raw = raw.strip()
    if raw.startswith("http"):
        segment = raw.rstrip("/").split("/")[-1]
        return re.split(r"[?#]", segment)[0]
    return raw


def folder_name_from_manifest(manifest_path: Path, fallback: str) -> str:
    """converts the extension display name to a clean snake_case folder name."""
    try:
        name = json.loads(manifest_path.read_text(encoding="utf-8")).get("name", "")
        if name and not name.startswith("__MSG_"):
            safe = re.sub(r"[^\w]+", "_", name.strip().lower()).strip("_")
            if safe:
                return safe
    except (json.JSONDecodeError, OSError):
        pass
    return fallback


def crx_to_zip_bytes(data: bytes) -> bytes:
    """strips the crx3 protobuf header and returns raw zip bytes."""
    if not data.startswith(CRX3_MAGIC):
        raise ValueError(f"not a valid crx3 file (got {data[:4]!r})")
    # protobuf header length sits at bytes 8-11
    header_len = struct.unpack_from("<I", data, 8)[0]
    return data[12 + header_len:]


def unpack_to(zip_bytes: bytes, dest: Path) -> int:
    """extracts zip bytes into dest and returns the number of files."""
    tmp = dest.parent / "_mcp_tmp.zip"
    try:
        tmp.write_bytes(zip_bytes)
        with zipfile.ZipFile(tmp, "r") as z:
            z.extractall(dest)
        return sum(1 for p in dest.rglob("*") if p.is_file())
    except zipfile.BadZipFile:
        raise ValueError("the zip inside this crx is corrupt")
    finally:
        if tmp.exists():
            tmp.unlink()


def is_text_file(path: Path) -> bool:
    return path.suffix.lower() not in BINARY_EXTENSIONS and path.is_file()


# ── mcp server ────────────────────────────────────────────────────────────────

mcp = FastMCP(
    name="crxreader",
    instructions=(
        "Use download_extension first to fetch and unpack a Chrome extension. "
        "Then use list_files to see what's there, read_file to inspect individual "
        "files, and search_files to find patterns across the source. "
        "Focus on background scripts and content scripts for outbound network activity. "
        "JS files are often minified — reason about patterns rather than exact syntax."
    ),
)


@mcp.tool()
def download_extension(url_or_id: str) -> str:
    """Download and unpack a Chrome extension by Chrome Web Store URL or extension ID.

    Args:
        url_or_id: Full Chrome Web Store URL or 32-character extension ID.
    """
    import shutil

    extension_id = parse_extension_id(url_or_id)

    if not re.match(r"^[a-z]{32}$", extension_id):
        return f"error: '{extension_id}' is not a valid extension id (32 lowercase letters)"

    try:
        response = requests.get(
            CRX_DOWNLOAD_URL.format(id=extension_id),
            stream=True, timeout=30, allow_redirects=True,
        )
    except requests.RequestException as err:
        return f"error: network request failed — {err}"

    if response.status_code != 200:
        return f"error: download failed (HTTP {response.status_code})"

    try:
        zip_bytes = crx_to_zip_bytes(response.content)
    except ValueError as err:
        return f"error: {err}"

    WORKSPACE.mkdir(parents=True, exist_ok=True)
    stage = WORKSPACE / f"_stage_{extension_id}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()

    try:
        file_count = unpack_to(zip_bytes, stage)
    except ValueError as err:
        return f"error: {err}"

    folder_name = folder_name_from_manifest(stage / "manifest.json", extension_id)
    final = WORKSPACE / folder_name
    if final.exists():
        shutil.rmtree(final)
    stage.rename(final)

    try:
        m = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        m = {}

    bg = m.get("background", {})
    lines = [
        f"unpacked: {folder_name}  ({file_count} files)",
        f"name:     {m.get('name', '?')}",
        f"version:  {m.get('version', '?')}",
        f"mv:       {m.get('manifest_version', '?')}",
        "",
    ]

    sw = bg.get("service_worker", "")
    if sw:
        lines.append(f"service worker:  {sw}")
    for s in bg.get("scripts", []):
        lines.append(f"background:      {s}")
    for cs in m.get("content_scripts", []):
        for s in cs.get("js", []):
            lines.append(f"content script:  {s}")
    perms = m.get("permissions", [])
    if perms:
        lines.append(f"permissions:     {', '.join(perms)}")
    host = m.get("host_permissions", [])
    if host:
        lines.append(f"host access:     {', '.join(host)}")

    source_files = sorted(
        str(p.relative_to(final)) for p in final.rglob("*") if is_text_file(p)
    )
    lines += ["", f"workspace: {final}", "", "files:"]
    lines += [f"  {f}" for f in source_files]

    return "\n".join(lines)


@mcp.tool()
def list_files(extension_folder: str) -> str:
    """List all files in an unpacked extension folder.

    Args:
        extension_folder: Folder name returned by download_extension (e.g. 'vortimo_osint_tool').
    """
    folder = WORKSPACE / extension_folder
    if not folder.exists():
        return f"error: '{extension_folder}' not found — run download_extension first"

    files = sorted(p.relative_to(folder) for p in folder.rglob("*") if p.is_file())
    if not files:
        return "no files found"

    lines = [f"{folder}/", ""]
    for f in files:
        size = (folder / f).stat().st_size
        tag  = "  [binary]" if not is_text_file(folder / f) else ""
        lines.append(f"  {f}  ({size:,} bytes){tag}")

    return "\n".join(lines)


@mcp.tool()
def read_file(extension_folder: str, file_path: str) -> str:
    """Read a file from an unpacked extension.

    Args:
        extension_folder: Folder name returned by download_extension.
        file_path: Relative path to the file (e.g. 'background.bundle.js').
    """
    folder = WORKSPACE / extension_folder
    if not folder.exists():
        return f"error: '{extension_folder}' not found — run download_extension first"

    target = folder / file_path
    if not target.exists():
        return f"error: file not found: {file_path}"
    if not is_text_file(target):
        return f"skipping binary file: {file_path}"

    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        # return head and tail so context doesn't overflow
        data = target.read_bytes()
        head = data[:MAX_FILE_BYTES // 2].decode("utf-8", errors="replace")
        tail = data[-(MAX_FILE_BYTES // 2):].decode("utf-8", errors="replace")
        return (
            f"large file ({size:,} bytes) — showing first and last 50KB:\n\n"
            f"{head}\n\n[...]\n\n{tail}"
        )

    return target.read_text(encoding="utf-8", errors="replace")


@mcp.tool()
def search_files(extension_folder: str, pattern: str) -> str:
    """Search all readable source files for a pattern or regex.

    Useful for finding fetch() calls, eval, hardcoded URLs, API keys, GA IDs, etc.

    Args:
        extension_folder: Folder name returned by download_extension.
        pattern: Search string or regex (e.g. 'fetch|XMLHttpRequest|api_secret').
    """
    folder = WORKSPACE / extension_folder
    if not folder.exists():
        return f"error: '{extension_folder}' not found — run download_extension first"

    try:
        regex = re.compile(pattern)
    except re.error as err:
        return f"error: bad regex — {err}"

    results  = []
    searched = 0

    for path in sorted(folder.rglob("*")):
        if not is_text_file(path) or path.stat().st_size > MAX_FILE_BYTES * 2:
            continue
        searched += 1
        try:
            file_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        hits = [
            f"  line {i + 1}: {line.strip()}"
            for i, line in enumerate(file_lines)
            if regex.search(line)
        ]
        if hits:
            label = f"{path.relative_to(folder)} ({len(hits)} match{'es' if len(hits) > 1 else ''}):"
            results.append(label)
            results.extend(hits[:30])
            if len(hits) > 30:
                results.append(f"  ... {len(hits) - 30} more")
            results.append("")

    if not results:
        return f"no matches for '{pattern}' across {searched} files"

    return f"pattern: '{pattern}'  ({searched} files searched)\n\n" + "\n".join(results)


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="crxreader MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="stdio for Claude Desktop on macOS/Linux, sse for Ollama/ollmcp (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="port for sse transport (default: 8000)",
    )
    args = parser.parse_args()

    if args.transport == "sse":
        # set port via settings attribute — FastMCP API varies by version
        try:
            mcp.settings.port = args.port
        except AttributeError:
            os.environ.setdefault("FASTMCP_PORT", str(args.port))
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")
