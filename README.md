# Nebula Model Acceptance Studio · 小小宇宙无敌

面向中转站和模型渠道接入的专业验收工作台。它把文本、图像、视频、音频请求放到同一个界面里，帮助你在接入新渠道时快速确认：请求是否发出、响应是否符合协议、媒体是否可以展示，以及失败时应该从哪里排查。

工作台支持浏览器直连的基础测试，也支持由本地 Python 服务执行的通用深度检测、CCMax 渠道验收和 Kimi Vendor Verifier（KVV）。它是渠道兼容性与传输质量工具，不是模型身份认证、生成质量评分或“覆盖所有私有接口”的承诺；每项结论都以实际请求和报告中的证据为准。

## 你可以用它做什么

| 模态 | 主要能力 | 已适配协议 / 工作流 |
| --- | --- | --- |
| 文本 | 单次对话、流式响应、通用深度检测、模型列表获取 | OpenAI Chat Completions / Responses、Anthropic Messages、Gemini GenerateContent |
| 图像 | 文生图、图生图 / 编辑、多图参考、页面预览、打开与下载 | OpenAI 兼容图片接口、Gemini 图像生成 / 编辑、JSON 参考图协议 |
| 视频 | 创建任务、实时进度、轮询、继续查询、播放与下载 | 中转站 Videos JSON、OpenAI Videos、豆包 / Seedance 原生任务、自定义 JSON |
| 音频 | 语音生成、音频对话、转写、翻译、页面播放与下载 | OpenAI Speech、Chat Completions 音频、Gemini TTS、Transcriptions / Translations |

每种模态都提供代表性测试场景。选择场景后提示词会直接填入，可继续编辑；图像任务支持上传本地参考图、逐行填写图片 URL，或使用附加 JSON 自定义 `image`、`model`、`aspect_ratio`、`size` 等字段。

模型 ID 可以手动填写，也可以从渠道的 `/v1/models` 获取并搜索选择。模型名称本身不会改变渠道协议；渠道鉴权、路径、请求体和返回格式不兼容时，需要增加对应适配器。

## 快速开始

### 只做基础测试

无需安装依赖，直接打开根目录的 [中转站测试工具-多模态版.html](中转站测试工具-多模态版.html)。

1. 选择文本、图像、视频或音频。
2. 填写渠道地址、API Key 和渠道实际使用的模型 ID，或点击获取模型列表。
3. 按渠道文档选择接口协议、尺寸、分辨率、音色和其他参数。
4. 选择测试场景，检查请求预览后开始测试。
5. 查看响应、媒体和实时进度；需要时导出 HTML、JSON 或证据包。

浏览器直连要求渠道允许 CORS，并且在 HTTPS 页面中使用 HTTPS 接口。直接打开单文件 HTML 时，基础结果只保存在当前页面，不会写入服务器历史数据库。

### 启动完整工作台

完整工作台需要 Git、Python 3.9+ 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。首次准备只会获取固定版本的官方 KVV 源码和必要测试素材，不会调用模型渠道。

macOS 用户可以双击 [启动验收工作台.command](启动验收工作台.command)。命令行启动方式如下：

```bash
python3 scripts/prepare_kvv.py
uv venv --python 3.13 integrations/.venv
uv pip install --python integrations/.venv/bin/python -e integrations/Kimi-Vendor-Verifier
integrations/.venv/bin/python integrations/server.py --open
```

浏览器访问 `http://127.0.0.1:8877/`。服务只监听本机环回地址，按 `Ctrl+C` 停止；开发时可以使用 `--port 8878` 换端口。检查已安装源码和素材时使用：

```bash
python3 scripts/prepare_kvv.py --check
```

只运行网页需要的 CCMax / KVV API 验收依赖时，可以在准备 KVV 后使用精简依赖：

```bash
uv pip install --python integrations/.venv/bin/python -r integrations/requirements-api.txt
```

固定版本、上游地址和素材校验记录在 [integrations/SOURCE.json](integrations/SOURCE.json)。

## 深度检测与专项验收

入口统一在 **文本模型 → 深度检测**。切换专项不会把另一套结果混入当前页面。

### 通用检测

各模态基础测试会记录请求状态、耗时、响应结构、输出匹配情况、视频任务进度和媒体加载结果；文本模型下的通用深度检测还会按多个代表性用例检查响应行为。网络失败会区分地址、TLS、CORS、混合内容、HTTP 状态和媒体跨域等常见原因，并把排查建议写入页面和报告。

### CCMax 渠道验收

CCMax 指 Claude / Anthropic Messages 兼容渠道，使用独立检测器，不运行 KVV。默认启用 12 类检查：

- 伪造 thinking 签名
- `message_start` 唯一性
- SSE `message_stop` 收尾完整性
- `message_stop` 后连接关闭
- 流中上游错误事件
- 非法模型错误状态与格式
- `usage` / 缓存字段结构
- 工具调用 JSON 增量
- 系统提示词金丝雀泄露
- 指令层级与越权覆盖
- 重复行为一致性（蒸馏风险启发式）
- 非法参数拒绝与错误诊断

快速验收默认执行 11 次请求（1 次签名、3 次 SSE、1 次强制工具、1 次非法模型和 5 次高级探针）；批量验收默认执行 62 次请求（5 次签名、50 次 SSE、其余 7 次固定探针）。次数可在页面自定义，实际请求数以报告为准并可能产生渠道费用。

高级探针只记录固定输入下的本轮行为，不会索取隐藏系统提示词、用户数据或渠道密钥。提示词泄露、指令覆盖、重复响应差异不能单独证明模型身份、官方来源、蒸馏事实或稳定可利用漏洞；401、429、超时和网络错误会标为“无法判定”。

### Kimi Vendor Verifier

Kimi KVV 由同一个本地服务调用 [MoonshotAI/Kimi-Vendor-Verifier](https://github.com/MoonshotAI/Kimi-Vendor-Verifier) 固定版本 `66092cf444c97356c0e11c5078c67116390615d9`，无需另开 KVV 项目。

- **11 项预检**：两种 thinking 基础请求、非法温度、流式 / 非流式 Tool Schema、Dynamic Tools、JSON Object、`tool_choice=required` 和两项 Prompt Tokens 用例。
- **全套验证**：运行官方 `tests/params`、`tests/tool_call_json_schema`、`tests/k3_features`、`tests/prompt_tokens`。当前固定版本收集 611 个 pytest 项，包含官方跳过项和本地边界检查；用例数量不等于 API 请求数量。

KVV 会分别记录 pytest 结果和传输证据。网络、鉴权或上游传输错误不会被误记为模型能力不合格，也不会把“无法判定”算作通过。全套验证不包含 OCRBench、MMMU、AIME、BEAM 或 DeepSWE 等独立 benchmark。

## 报告、历史与隐私

专项任务完成或取消后，可以下载 HTML 报告、JSON 结果和 ZIP 证据包。文件名统一为：

```text
测试报告-测试的模型-YYYYMMDD-HHmmss
```

模型名中的路径、控制字符和系统保留字符会自动替换，避免出现重复的 `(... 1)` 文件名。HTML 报告包含测试范围、配置、预期与实际结果、问题影响、排查建议、耗时、请求计数和逐项证据，可搜索、筛选并打印为 PDF。

启用完整工作台后，基础测试、通用检测、CCMax 和 KVV 结果会写入 SQLite 历史记录，可按模型、渠道、提示词、类型和状态搜索。历史详情保留脱敏配置、输出、错误、检测证据和媒体引用，不会保存 API Key；本地媒体受大小限制，远程媒体只保存脱敏后的 URL。

报告和历史不是“成功即可信”的证明。分享报告前请检查响应正文、提示词和媒体中是否包含业务数据；不要把 API Key、登录配置、真实报告、日志或截图提交到 Git。

## 服务器部署

生产环境应让 Python 后端继续只监听 `127.0.0.1`，通过独立 HTTPS 反向代理对外提供访问，并使用独立端口隔离已有项目。当前部署说明采用：

| 项目 | 默认设置 |
| --- | --- |
| 代码目录 | `/opt/xiaoxiao-workbench` |
| 后端 | `127.0.0.1:18878` |
| 外部 HTTPS 入口 | `18877` |
| 服务用户 | `xxworkbench` |
| 历史数据库 | `/var/lib/xiaoxiao-workbench/data/workbench.sqlite3` |
| 报告目录 | `/var/lib/xiaoxiao-workbench/reports` |

完整的 Nginx 隔离、登录会话、证书、systemd、备份和健康检查要求见 [服务器部署说明](docs/server-deployment.md)。部署时不要把后端改成公开无登录接口，不要重启其他项目的 Nginx 或 systemd 服务；仅维护 `xiaoxiao-workbench` 和 `xiaoxiao-gateway` 单元。

## 开发与验证

前端源码位于 `multimodal-workbench/`，根目录单文件由构建脚本生成：

```bash
python3 multimodal-workbench/build.py
```

Node.js 20+ 的离线和 UI 回归测试：

```bash
npm ci
npx playwright install chromium
npm test
npm run test:choices
npm run test:acceptance
npm run test:workflows
npm run test:history
```

安装后端依赖后，可以运行 Python 服务测试：

```bash
integrations/.venv/bin/python -m unittest discover -s integrations -p 'test_*.py'
```

测试使用本地模拟接口，不需要真实渠道密钥。第三方来源和许可证说明见 [THIRD_PARTY.md](THIRD_PARTY.md)，操作细节见 [multimodal-workbench/使用说明.txt](multimodal-workbench/使用说明.txt)。

## 目录结构

```text
multimodal-workbench/          前端源码、协议引擎、场景提示词、UI 测试
integrations/                  本地验收服务、CCMax 检测器、KVV 适配器、报告与历史
scripts/                       KVV 固定版本准备与离线校验
docs/                          服务器隔离部署说明
中转站测试工具-多模态版.html     可直接打开的单文件版本
```
