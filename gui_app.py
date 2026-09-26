"""gui_app.py — desktop entry point for the AuraAgent GUI (Epic M4).

Wraps the exact same app gui/server.py's `uvicorn gui.server:app` serves
(M1's backend + M2's frontend + M3's REST panels) in a real, native OS
window via pywebview (Edge WebView2 on Windows, WebKit on macOS/Linux)
instead of asking a human to open a browser tab themselves. Nothing about
the app changes here — this file only adds "how it's launched," the same
role main.py plays on top of core/bootstrap.py's shared composition, just
for a desktop window instead of a terminal REPL.

Usage: `python gui_app.py` — requires the frontend already built once
(`cd gui/frontend && npm install && npm run build`, see README's "Running
the GUI") and a real .env/API key (same requirement as `python main.py`).

Concretely: uvicorn.Server is run on a background thread (pywebview's
event loop must own the main thread on some platforms) bound to a free
localhost port chosen at random, so this never collides with another
instance of itself or with `uvicorn gui.server:app --port 8000` already
running; the main thread waits for that server to actually finish
startup (uvicorn.Server.started) before opening a window pointed at it,
then blocks in webview.start() until the window is closed, at which point
the server is asked to shut down (gui/server.py's lifespan `finally`
still runs, closing MCP connections etc. the same way main.py's REPL loop
does on exit).
"""
from __future__ import annotations

import socket
import sys
import threading
import time

import uvicorn

from gui.server import STATIC_DIR, app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    if not STATIC_DIR.is_dir():
        print(
            "No built frontend found at gui/static/.\n"
            "Run this once first:\n"
            "  cd gui/frontend\n"
            "  npm install\n"
            "  npm run build\n"
        )
        sys.exit(1)

    import webview  # imported lazily so `python gui_app.py --help`-style
    # failures above don't require pywebview's native deps to even import

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(200):  # ~20s ceiling
        if server.started or not thread.is_alive():
            break
        time.sleep(0.1)

    if not server.started:
        print(
            "The backend failed to start (see the traceback above) — most likely a missing/invalid "
            "API key. Copy .env.example to .env and fill it in, same as for `python main.py`."
        )
        sys.exit(1)

    webview.create_window("AuraAgent", f"http://127.0.0.1:{port}", width=1280, height=860, min_size=(800, 600))
    webview.start()

    # Window closed -> ask the server to shut down gracefully so
    # gui/server.py's lifespan `finally` (AppContext.aclose()) still runs.
    server.should_exit = True
    thread.join(timeout=10)


if __name__ == "__main__":
    main()
