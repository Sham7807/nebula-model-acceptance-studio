"""Build the Windows portable distribution with PyInstaller."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
WINDOWS = ROOT / "desktop" / "windows"
BUILD = WINDOWS / "build"
DIST = WINDOWS / "dist"


def run(*args: str) -> None:
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def main() -> None:
    # KVV is pinned and verified before it enters the portable package.
    run(sys.executable, ROOT / "scripts" / "prepare_kvv.py", "--check")
    for path in (BUILD, DIST):
        if path.exists():
            shutil.rmtree(path)
    DIST.mkdir(parents=True)
    separator = ";" if os.name == "nt" else ":"
    common = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
              "--distpath", str(DIST), "--workpath", str(BUILD), "--specpath", str(BUILD),
              "--add-data", f"{ROOT / 'multimodal-workbench'}{separator}multimodal-workbench",
              "--add-data", f"{ROOT / 'integrations'}{separator}integrations"]
    run(*(common + ["--windowed", "--name", "Channel-Test-System-Windows",
                    "--hidden-import", "webview.platforms.edgechromium", str(WINDOWS / "launcher.py")]))
    run(*(common + ["--console", "--name", "Channel-Test-System-KVV",
                    str(WINDOWS / "kvv_worker.py")]))
    package = DIST / "Channel-Test-System-Windows"
    worker = DIST / "Channel-Test-System-KVV"
    # Put the KVV worker beside the main executable so the launcher can invoke
    # it as an isolated process without exposing a second user-facing binary.
    for source in worker.iterdir():
        target = package / source.name
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
    shutil.rmtree(worker)
    (package / "版本说明.txt").write_text(
        "渠道测试系统 · Windows 版\n\n"
        "双击 Channel-Test-System-Windows.exe 启动。\n"
        "需要 Windows 10/11 与 Microsoft WebView2 Runtime。\n"
        "如果系统没有 WebView2，程序会自动使用默认浏览器打开本地工作台。\n"
        "历史记录位于 %LOCALAPPDATA%\\NebulaWorkbench。\n",
        encoding="utf-8",
    )
    print(f"Windows package: {package}")


if __name__ == "__main__":
    main()
