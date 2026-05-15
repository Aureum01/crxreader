# crxreader

Download and inspect any Chrome extension's source code from the command line.

Paste a Chrome Web Store URL (or bare extension ID), and crxreader downloads the CRX, unpacks it, names the folder after the extension, and opens it in your editor. Works on Linux, WSL, and Windows.

## install

```bash
pip install requests
```

No other dependencies.

## usage

```bash
python crxreader.py <url or extension id>
```

```bash
# paste the store url directly
python crxreader.py https://chromewebstore.google.com/detail/vortimo-osint-tool/mnakbpdnkedaegeiaoakkjafhoidklnf

# or just the id
python crxreader.py mnakbpdnkedaegeiaoakkjafhoidklnf

# open in a specific editor
python crxreader.py <url> --ide cursor

# extract without opening an editor
python crxreader.py <url> --no-open
```

## save your defaults

Run this once and you won't need to pass flags again:

```bash
python crxreader.py --save-defaults --env wsl --output ~/extensions --ide code
```

Supported `--env` values: `linux`, `wsl`, `windows`

## what you get

After running, your output folder contains the full unpacked extension source — named after the extension, not the raw ID string.

The terminal prints a summary from `manifest.json`:

```
  Vortimo OSINT-tool  v5.2.1  (mv3)
  ────────────────────────────────────────────────────
  service worker   background.bundle.js
  content script   contentScript.bundle.js
  permissions      storage, contextMenus, tabs, activeTab, webNavigation, webRequest, pageCapture
  host access      <all_urls>

  tip: paste js files into https://deobfuscate.relative.im/ to make them readable
```


## wsl note

On WSL, the output is automatically copied to your Windows filesystem and the folder is opened in the Windows-side editor. The real Desktop path is resolved via PowerShell, so OneDrive-redirected Desktops work correctly.


## supported editors

`code`, `code-insiders`, `cursor`, `idea`, `pycharm`, `webstorm`, `subl`, `zed`

You can also pass a full path to any executable with `--ide`


## MCP server (if you dont want to use an AI extension in your IDE)
 
`crxreader_mcp.py` exposes the same pipeline as an MCP server so an AI model can download, unpack, and read Chrome extensions
 
### install
 
```bash
pip install mcp requests
```
 
### tools
 
| tool | what it does |
|---|---|
| `download_extension` | downloads and unpacks an extension by URL or ID |
| `list_files` | lists all files in the unpacked folder |
| `read_file` | reads a specific file |
| `search_files` | searches across all source files for a pattern or regex |
 
 
### option 1 — Ollama via ollmcp (works on Windows WSL)
 
This is the recommended path on Windows. It connects your local Ollama models directly to the MCP tools via a terminal UI.
 
**Install ollmcp:**
 
```bash
pip install mcp-client-for-ollama
```
 
**Create the server config:**
 
```bash
mkdir -p ~/.config/ollmcp
cat > ~/.config/ollmcp/mcp_servers.json << 'EOF'
{
  "mcpServers": {
    "crxreader": {
      "command": "/path/to/venv/bin/python",
      "args": ["/path/to/crxreader_mcp.py"]
    }
  }
}
EOF
```
 
**Run:**
 
```bash
ollmcp --host http://$(ip route | grep default | awk '{print $3}'):11434 \
       --servers-json ~/.config/ollmcp/mcp_servers.json \
       --model qwen3:8b
```
 
Then just ask it:
 
```
inspect this extension: https://chromewebstore.google.com/detail/vortimo-osint-tool/mnakbpdnkedaegeiaoakkjafhoidklnf
```
 
The model will call `download_extension` automatically, return a manifest summary, then let you ask follow-up questions like `search for fetch calls` or `read background.bundle.js`.
 
**Note on Windows:** Claude Desktop on Windows has a known MSIX packaging bug that silently ignores local MCP server config. Use Ollama + ollmcp via WSL until Anthropic ships a fix.
 
 
### option 2 — Claude Desktop (macOS and Linux only)
 
Add this to your Claude Desktop config at `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `~/.config/Claude/claude_desktop_config.json` (Linux):
 
```json
{
  "mcpServers": {
    "crxreader": {
      "command": "/path/to/venv/bin/python",
      "args": ["/path/to/crxreader_mcp.py"]
    }
  }
}
```
 
Restart Claude Desktop. A hammer icon will appear in the chat input confirming the tools are connected. Then say:
 
```
inspect this extension: https://chromewebstore.google.com/detail/...
```
 
 
### option 3 — SSE mode (any SSE-compatible MCP client)
 
Run the server in SSE mode:
 
```bash
python crxreader_mcp.py --transport sse --port 8000
```
 
Then point your client at `http://localhost:8000/sse`.

