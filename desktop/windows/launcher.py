"""Windows desktop entry point for 渠道测试系统.

The web workbench and reviewed Python engine are bundled unchanged.  pywebview
uses the system WebView2 runtime for a native window; if WebView2 is missing,
the same local authenticated service is opened in the default browser with an
actionable message instead of failing silently.
"""
from __future__ import annotations

import atexit
import os
from pathlib import Path
import sys
import threading
import time
import webbrowser


APP_NAME = "渠道测试系统"


def bundled_root() -> Path:
    return Path(getattr(__import__("sys"), "_MEIPASS", Path(__file__).resolve().parents[2]))


def data_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "NebulaWorkbench"


def start_service():
    root = bundled_root()
    workbench = root / "integrations"
    os.environ.update(
        WORKBENCH_DB=str(data_root() / "history.sqlite3"),
        WORKBENCH_REPORTS=str(data_root() / "Reports"),
        WORKBENCH_COOKIE_SECURE="0",
        PYTHONDONTWRITEBYTECODE="1",
    )
    data_root().mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(workbench))
    import server

    # Keep the desktop behaviour aligned with the macOS app while leaving the
    # public website's serial execution policy untouched.
    server.MAX_CONCURRENT_RUNS = 4
    if getattr(sys, "frozen", False):
        runner = Path(sys.executable).with_name("Channel-Test-System-KVV.exe")
        if runner.exists():
            os.environ["WORKBENCH_PYTEST_RUNNER"] = str(runner)
    server.restore_reports()
    httpd = server.WorkbenchServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.15}, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{httpd.server_port}/"

    def stop() -> None:
        for job in list(server.JOBS.values()):
            job.get("cancel", threading.Event()).set()
        httpd.shutdown()
        httpd.server_close()

    atexit.register(stop)
    return url, stop


def main() -> int:
    url, stop = start_service()
    try:
        import webview

        window = webview.create_window(
            APP_NAME,
            url,
            width=1440,
            height=920,
            min_size=(1080, 680),
            resizable=True,
            text_select=True,
        )
        # Edge Chromium is the Windows WebView2 backend.  pywebview emits a
        # useful exception when the runtime is absent; fall back to a browser.
        try:
            webview.start(gui="edgechromium", debug=False)
        except Exception as exc:  # pragma: no cover - Windows runtime branch
            webbrowser.open(url)
            print(f"{APP_NAME} 已在浏览器打开：{url}\nWebView2 未就绪：{exc}")
            input("按 Enter 退出本地服务…")
    except ImportError:  # pragma: no cover - only used in a damaged package
        webbrowser.open(url)
        print(f"{APP_NAME} 已在浏览器打开：{url}")
        input("按 Enter 退出本地服务…")
    finally:
        stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
