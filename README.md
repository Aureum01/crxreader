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
