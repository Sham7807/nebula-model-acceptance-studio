# 小小宇宙无敌 · macOS 桌面版

原生 macOS 窗口与本机检测引擎，让模型渠道验收像使用一款 Mac 应用一样直接。网页版的源码、单文件 HTML 和服务器部署方式完整保留；桌面版是 `desktop/` 下的独立入口。

**当前构建支持 Apple Silicon、macOS 14 及以上。** 安装包为本地签名，尚无 Apple Developer ID 签名或公证。没有在 Intel Mac 或 macOS 14 实机上验证；本次验证环境是 Apple Silicon / macOS 26。

## 安装与首次使用

1. 打开 `Nebula-Studio-1.0.0-Apple-Silicon.dmg`，将「小小宇宙无敌.app」拖入 Applications。
2. 双击打开应用，等待左下角显示「本地引擎就绪」。Python 和验收依赖已内置，无需打开终端。
3. 点击右上角「连接渠道」或按 `⌘K`，填写 Base URL、API Key 和可选的默认模型。
4. 从侧边栏选择工作区，获取模型列表、检查请求配置，再开始测试。
5. 完成后在「测试档案」回看记录；下载报告时会出现 macOS 保存窗口。

如果 Gatekeeper 提示开发者无法验证，请确认安装包来源，再到「系统设置 → 隐私与安全性」查看「仍要打开」。不需要关闭系统的整体安全保护。正式公开分发前应使用自己的 Developer ID 签名并完成 Apple 公证。

## 可以使用的功能

| 入口 | 主要用途 |
| --- | --- |
| 原生概览 | 启动测试、查看实际历史数量与最近记录 |
| 文本、图像、视频、音频 | 沿用网页版协议、提示词、文件和 URL 输入、进度、播放与导出 |
| 通用检测 | 多协议、多参数、工具、多模态、缓存与受控并发 |
| CCMax、Claude | 专项验收、请求矩阵、签名和透传证据 |
| Kimi · KVV | 内置固定版本官方 API verifier 与 K3 扩展 |
| GPT 生成专项 | 生成结果、HTML/SVG 结构和 Token 账本 |
| 测试档案 | 本机数据库、搜索、详情、媒体与统一报告 |

本机引擎不依赖已部署的网站在线。**实际模型请求仍需要网络和有效渠道权限，并可能产生渠道费用。** 模型支持程度、地区限制、媒体 URL 有效期和上游限流仍由渠道决定。桌面版不能保证所有第三方私有接口都兼容。

## 数据与退出

- 数据目录：`~/Library/Application Support/NebulaWorkbench/`，可从应用设置在 Finder 中打开。
- 历史数据库：`history.sqlite3`；验收证据：`Reports/`。与服务器网页版分别保存，不自动同步或导入。
- 默认渠道的名称、地址和模型保存在应用偏好设置中。API Key 默认仅驻留当前进程；勾选记住时才保存到 macOS 钥匙串。
- 工作区使用临时 WebKit 数据存储。启动时创建随机的本机会话，服务只绑定 `127.0.0.1` 的系统分配端口，不占用网页版固定端口。
- 关闭最后一个窗口会退出应用。测试进行中会提示是否停止；后台任务收到取消信号，已保存记录保留。
- 引擎或工作区异常后可以重新打开；不会自动重新提交生成请求。上游已开始的任务可能仍计费，取消无法撤销已处理请求。

## 从源码构建

需要 Apple Silicon Mac、Xcode Command Line Tools（Swift 5.9+）、Git 和 [uv](https://docs.astral.sh/uv/)。在仓库根目录执行：

```bash
python3 scripts/prepare_kvv.py
uv python install 3.12
uv venv --python 3.12 desktop/.build-venv
uv pip sync --python desktop/.build-venv/bin/python desktop/requirements.lock
python3 desktop/scripts/build_app.py
```

输出到 `desktop/dist/`。构建器复制现有网页与 Python 引擎、固定版本 KVV、可迁移的 Python 运行时、依赖和许可证；校验 KVV 素材，签名嵌套二进制，验证签名并生成 DMG。它不会修改网页源码或服务器配置。

`--no-dmg` 只生成 `.app`。当前构建器面向本机架构，不是 Universal 2 构建。开发时修改 `Resources/desktop.css` 和 `desktop-bridge.js` 后需要重新打包才能进入应用。

## 验证

以下检查只用本地模拟渠道，不消耗付费模型额度：

```bash
# 本机会话、访问控制、数据库持久化、独立端口和退出清理
desktop/.build-venv/bin/python -m unittest discover -s desktop/Tests -p 'test_*.py' -v

# 原生模型与 WebKit：先在另一个终端启动本地模拟渠道
python3 desktop/Tests/mock_channel.py
# 然后执行；需要图形登录会话，会短暂打开一个测试窗口
NEBULA_TEST_PROVIDER=1 desktop/scripts/test_native.sh

# 原有网页版回归
npm test
desktop/.build-venv/bin/python -m pytest integrations --ignore=integrations/Kimi-Vendor-Verifier -q
```

原生检查使用独立测试数据目录，覆盖全部工作区导航、默认渠道同步、模型发现、实际模拟请求和历史保存。也可使用 `open "desktop/dist/小小宇宙无敌.app" --args --isolated-testing` 启动隔离的人工 QA 会话；这不会读写正式渠道配置或钥匙串。

## 结构

| 路径 | 职责 |
| --- | --- |
| `Sources/NebulaDesktop/` | SwiftUI 窗口、侧边栏、概览、设置、钥匙串、WebKit 和下载 |
| `Resources/` | 仅桌面版加载的样式和工作区桥接 |
| `engine/desktop_service.py` | 带临时鉴权的本机服务入口、历史目录和进程生命周期 |
| `engine/kvv_extensions/` | K3 工具、长度、视频输入与缓存扩展，打包时加入官方测试目录 |
| `scripts/` | 自包含 `.app` / `.dmg` 构建、图标生成与原生检查 |
| `Tests/` | 离线引擎测试、原生 WebKit 检查与模拟渠道 |

第三方来源见仓库的 [THIRD_PARTY.md](../THIRD_PARTY.md)。Python 运行时的许可证、各依赖的许可证和 KVV MIT 许可证随应用资源保留。
