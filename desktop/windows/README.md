# 渠道测试系统 · Windows 版

Windows 版复用仓库中的网页工作台和 Python 检测引擎，使用 `pywebview + WebView2` 提供原生窗口。检测协议、统一报告、任务中心、历史数据库与 macOS 版保持一致。

GitHub Actions 会在 Windows runner 上生成 `Channel-Test-System-Windows.zip`。解压后双击 `Channel-Test-System-Windows.exe` 即可启动。首次运行需要 Windows 10/11 和 Microsoft WebView2 Runtime；没有 WebView2 时会自动回退到默认浏览器打开本机工作台。

本地数据保存在 `%LOCALAPPDATA%\NebulaWorkbench`，API Key 不写入报告。服务只监听 `127.0.0.1`，不会占用服务器端口，也不会改变网页版的部署配置。

## 本地构建

在 Windows PowerShell 中执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r desktop\requirements.lock pyinstaller pywebview
.\.venv\Scripts\python.exe -m pip install -e integrations\Kimi-Vendor-Verifier
.\.venv\Scripts\python.exe scripts\prepare_kvv.py
.\.venv\Scripts\python.exe desktop\windows\build_windows.py
```

构建输出位于 `desktop/windows/dist/Channel-Test-System-Windows/`。
