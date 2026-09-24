# 第三方来源

## Kimi Vendor Verifier

- 项目：[MoonshotAI/Kimi-Vendor-Verifier](https://github.com/MoonshotAI/Kimi-Vendor-Verifier)
- 固定版本：`66092cf444c97356c0e11c5078c67116390615d9`
- 许可证：[MIT License](https://github.com/MoonshotAI/Kimi-Vendor-Verifier/blob/66092cf444c97356c0e11c5078c67116390615d9/LICENSE)
- 获取方式：`scripts/prepare_kvv.py` 下载固定版本及 API 验证必需的 Git LFS 测试素材，保留官方许可证。

官方源码与数据不重复提交到本仓库。网页通过 `integrations/kvv_runner.py` 调用官方用例；进度、取消、传输证据和报告由本项目的适配层提供。

## CCMax 检测方法

独立检测器参考项目使用者提供的 `api-channel-acceptance-test` 方法，覆盖 Anthropic Messages 渠道的签名校验、流式完整性、错误格式和工具调用。原始附件不随本仓库分发，也不是运行依赖。

## Playwright

[Playwright](https://github.com/microsoft/playwright) 仅用于开发时的浏览器回归测试，依赖与版本记录于 `package.json` 和 `package-lock.json`，遵循其上游许可证。

## macOS 桌面运行时

桌面版打包 [python-build-standalone](https://github.com/astral-sh/python-build-standalone) 发行的可迁移 CPython 3.12，以及 `desktop/requirements.lock` 中锁定的依赖。Python 运行时许可证、依赖 `.dist-info` 许可证和 KVV MIT 许可证随 `.app` 资源保留。SwiftUI、AppKit 和 WebKit 使用系统框架，不嵌入第三方浏览器运行时。
