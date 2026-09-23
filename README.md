# Nebula Model Acceptance Studio · 小小宇宙无敌

面向中转站和模型渠道接入的专业验收工作台。它把文本、图像、视频、音频请求放到同一个界面里，帮助你在接入新渠道时快速确认：请求是否发出、响应是否符合协议、媒体是否可以展示，以及失败时应该从哪里排查。

工作台支持基础多模态测试、通用深度检测，以及由 Python 服务执行的 CCMax 渠道验收、Claude 上游专项和 Kimi Vendor Verifier（KVV）。完整工作台中的模型列表与基础/通用测试请求通过服务访问渠道；单文件模式使用浏览器直连。它是渠道兼容性与传输质量工具，不是模型身份认证、生成质量评分或“覆盖所有私有接口”的承诺；每项结论都以实际请求和报告中的证据为准。

## 你可以用它做什么

| 模态 | 主要能力 | 已适配协议 / 工作流 |
| --- | --- | --- |
| 文本 | 单次对话、流式响应、通用深度检测、模型列表获取 | OpenAI Chat Completions / Responses、Anthropic Messages、Gemini GenerateContent |
| 图像 | 文生图、图生图 / 编辑、多图参考、页面预览、打开与下载 | OpenAI 兼容图片接口、Gemini 图像生成 / 编辑、JSON 参考图协议 |
| 视频 | 创建任务、实时进度、轮询、继续查询、播放与下载 | 中转站 Videos JSON、OpenAI Videos、豆包 / Seedance 原生任务、自定义 JSON |
| 音频 | 语音生成、音频对话、转写、翻译、页面播放与下载 | OpenAI Speech、Chat Completions 音频、Gemini TTS、Transcriptions / Translations |

每种模态都提供代表性测试场景。选择场景后提示词会直接填入，可继续编辑；图像任务支持上传本地参考图、逐行填写图片 URL，或使用附加 JSON 自定义 `image`、`model`、`aspect_ratio`、`size` 等字段。

模型 ID 可以手动填写，也可以获取后搜索、多选。基础、通用、CCMax、Claude、KVV、GPT 专项共用登录会话保护的 `/api/models`，由服务器访问渠道，避免浏览器 CORS 导致入口之间结果不一致。地址会保留 `/api/v1`、`/openai/v1`、`/compatible-mode/v1` 等前缀和显式版本，也接受完整模型列表或对话接口地址；未填写版本时会有限尝试常见路径。

服务支持 Bearer、Anthropic、Gemini 和无鉴权，以及同源鉴权兼容、分页和短暂故障重试。每次获取会显示可展开的连接诊断：实际接口、鉴权、HTTP 状态、耗时和处理建议，密钥始终脱敏。401/403 渠道拒绝与工作台登录失效分开提示。获取列表仅发送 GET；生成和验收请求不会因此自动重复。直接打开单文件 HTML 仍需渠道允许 CORS。

国外渠道还取决于**工作台服务器**的出站网络与上游地区政策。本机浏览器能访问，不代表服务器能访问；可按[部署说明](docs/server-deployment.md#模型列表与国外渠道连通性)配置模型发现、通用请求及 CCMax / Claude 专项的出站代理。列表权限也可能与推理权限不同；渠道未开放列表时，可手动填写模型 ID。模型名称不会自动改变测试协议。

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

选择 **请求格式** 后，工具声明、图片输入、流式事件、输出长度和 usage 字段按对应协议转换：

| 格式 | 默认端点 | 默认鉴权 |
| --- | --- | --- |
| OpenAI Chat Completions | `/v1/chat/completions` | Bearer |
| OpenAI Responses | `/v1/responses` | Bearer |
| Anthropic Messages | `/v1/messages` | x-api-key |
| Gemini GenerateContent | `/v1beta/models/{model}:generateContent` | x-goog-api-key |

可展开「接口路径与鉴权」覆盖路径和鉴权，并先预览请求。显式 Base URL 版本与渠道前缀会保留，路径覆盖限于同一渠道。快速模式检查基础连通、工具和流式；标准/完整模式增加多模态输入、缓存与长度控制。当前原生协议没有等价字段的项目标为“不适用”，不会偷偷丢弃参数后算通过。视频输入、JSON Schema 和缓存的实际支持仍取决于渠道与模型，未观测到缓存命中不等于模型没有缓存能力。

服务模式的通用请求使用 `/api/proxy`；SSE 实时透传并验证内容增量及收尾事件。每次请求保存实际格式、鉴权类型、端点和脱敏原始响应。图像/视频理解与图像/视频生成分别测试。

各模态基础测试会记录请求状态、耗时、响应结构、输出匹配情况、视频任务进度和媒体加载结果；文本模型下的通用深度检测还会按多个代表性用例检查响应行为。网络失败会区分地址、TLS、CORS、混合内容、HTTP 状态和媒体跨域等常见原因，并把排查建议写入页面和报告。

### CCMax 渠道验收

CCMax 用于 Claude 渠道验收，使用独立检测器，不运行 KVV。可选择 **Anthropic Messages** 或 **OpenAI Chat Completions** 请求格式；两者分别解析对应响应和 SSE，选择 Bearer 鉴权本身不会改变原生请求体。

默认原生格式启用 12 类检查：

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

OpenAI 格式使用 `/v1/chat/completions` 与 Bearer，检查 `choices`、`delta.tool_calls`、`finish_reason`、`[DONE]`、响应体结束及 `prompt_tokens_details.cached_tokens`，同时保留高级行为探针。原生签名契约不适用，不发请求、不计通过、不计入评分分母；默认快速 / 批量模式分别发送 10 / 57 次请求。

高级探针只记录固定输入下的本轮行为，不会索取隐藏系统提示词、用户数据或渠道密钥。提示词泄露、指令覆盖、重复响应差异不能单独证明模型身份、官方来源、蒸馏事实或稳定可利用漏洞；401、429、超时和网络错误会标为“无法判定”。

### Claude 上游专项

在 **CCMax 与 KVV 之间**选择 **Claude 专项**。这一独立执行器面向中转渠道的 Claude 验收，支持 Anthropic Messages 与 OpenAI Chat Completions；模型获取、多选批量、任务恢复、历史和报告沿用工作台的统一流程。

「来源声明」可选择待确认、Anthropic 或 AWS Bedrock。它记录供应商声称的来源，不改变请求端点，也不意味着已经验证来源。接入参数仍是渠道的 Base URL、API Key 和模型 ID，不需要填写 AWS 密钥；直接调用 AWS Runtime 的 SigV4 鉴权不在此适配器范围内。

| 模块 | 重点证据 | 权重 |
| --- | --- | ---: |
| 协议与透传 | 成功基线、SSE 结构与收尾、停止词、返回模型、request ID 和 usage | 18% |
| 鉴权与 thinking 签名 | 无效凭据对照、签名原样回传、合成及真实签名篡改对照 | 14% |
| 工具与多模态 | 强制 Calculator、参数 Schema、工具结果闭环、内置图片识别 | 14% |
| 输出上限 | `max_tokens=1` 的计数与截断、非法参数拒绝 | 10% |
| 注入与指令层级 | 随机合成金丝雀、系统指令约束、直接与文档间接注入 | 14% |
| 来源线索 | 请求/返回模型、响应头与非法模型对照；身份结论独立保留 | 10% |
| 大 Token 缓存 | 唯一长前缀、完全重复、后缀变化、首部变化四轮对照 | 10% |
| 受控压测 | 有界并发、成功率、HTTP 分布、延迟 P50/P95 与首字节时间 | 10% |

开始前点击 **查看测试请求**，按模块查看实际构建的请求体、条件依赖和预计请求数；预览不发送 API Key、不调用模型。执行时只启用勾选模块，必要的成功基线用于解释负对照。工具结果和签名回传依赖上一轮有效结果，实际请求数会单独记录。

缓存默认目标为 **约 12,000 Token** 的变化长文本，可选范围 1,024–100,000；这是文本规模估算，报告以上游实际 usage 为准。Messages 使用原生 `cache_control`，OpenAI 兼容格式观察 `cached_tokens`；缺少字段、输入低于阈值或未命中均不会被包装成「缓存通过」。

压测默认 **20 次、并发 4**，最多 200 次、并发 20；请求有独立超时，不自动重试。实际执行会产生渠道用量。结果仅反映本轮负载，不代表长期容量承诺。

OpenAI Chat 没有 Anthropic 原生 thinking 签名契约，相关项明确为不适用。签名回传、模型名、响应头或固定提示词行为都不能单独证明官方来源、AWS 代签、模型权重或是否蒸馏；需要供应商控制台、请求 ID、上游日志与账单交叉核验。报告保留每项方法、预期、实测、得分依据、影响、建议和脱敏请求证据。

### Kimi Vendor Verifier

Kimi KVV 由同一个本地服务调用 [MoonshotAI/Kimi-Vendor-Verifier](https://github.com/MoonshotAI/Kimi-Vendor-Verifier) 固定版本 `66092cf444c97356c0e11c5078c67116390615d9`，无需另开 KVV 项目。

- **11 项预检**：两种 thinking 基础请求、非法温度、流式 / 非流式 Tool Schema、Dynamic Tools、JSON Object、`tool_choice=required` 和两项 Prompt Tokens 用例。
- **全套验证**：运行官方 `tests/params`、`tests/tool_call_json_schema`、`tests/k3_features`、`tests/prompt_tokens`。当前固定版本收集 611 个 pytest 项，包含官方跳过项和本地边界检查；用例数量不等于 API 请求数量。

KVV 会分别记录 pytest 结果和传输证据。网络、鉴权或上游传输错误不会被误记为模型能力不合格，也不会把“无法判定”算作通过。全套验证不包含 OCRBench、MMMU、AIME、BEAM 或 DeepSWE 等独立 benchmark。

对于使用 OpenAI 格式转发的 Kimi 或其他模型，在 **验收请求格式** 选择 **OpenAI 兼容格式 · 全范围适配**，再选择预检或全套。该选项覆盖四个检测层面，不仅调整 Thinking 字段：

| 范围 | OpenAI 兼容测试内容 |
| --- | --- |
| 11 项预检 | 非流式、SSE / usage、`max_tokens=1`、非法上限、强制 / 禁止 / 多工具、JSON 输出、内置图片识别、缓存重复观测、token 计数一致性 |
| 全套兼容验证 | 22 项专项探针（增加工具闭环、具名 / 并行调用、严格 JSON Schema、推理参数、多图、视频 URL 扩展和参数边界）＋固定版 KVV 的 408 项工具 Schema 矩阵，共 430 项 |

兼容模式使用独立断言：顶层工具工作流不冒充 Kimi 原生 `system.tools` 动态加载，usage 计数也不套用 Kimi 固定 tokenizer 数值。视频 URL 属于渠道扩展；未上报推理证据、未观察到缓存命中等结果会标为未覆盖。完整 Schema 矩阵可能超出部分模型支持的子集。报告记录具体方法、实测值、请求证据、限制和建议；如需官方原生契约验证，可切回 Kimi 原生格式。

## 报告、历史与隐私

专项任务完成或取消后，可以下载 HTML 报告、JSON 结果和 ZIP 证据包。文件名统一为：

```text
测试报告-测试的模型-YYYYMMDD-HHmmss
```

模型名中的路径、控制字符和系统保留字符会自动替换；时间包含年月日、时分秒，便于区分不同测试批次。基础、通用、CCMax、Claude、KVV、GPT 和历史下载使用同一报告标准：蓝色封面、结论 KPI、全项结果矩阵、维度评分、逐项方法/预期/实测、问题影响与建议、请求证据。服务端与独立 HTML 共用 `report-theme.css`，可打印为 PDF；服务端报告支持搜索和筛选。

实际请求数与检查项数分别统计，只有真实关联的证据才链接到检查项。统一评分只对已观察的通过/失败/待判定项目计算，跳过、不适用和未覆盖项另列；通用检测的原始启发式分数独立保留，不能与统一分数混用。报告不会重新调用模型，也不会把普通响应、自述身份或字段加总当作真实性或计费准确性证明。

启用完整工作台后，基础测试、通用检测、CCMax、Claude 和 KVV 结果会写入 SQLite 历史记录，可按模型、渠道、提示词、类型和状态搜索。历史详情保留脱敏配置、输出、错误、检测证据和媒体引用，不会保存 API Key；本地媒体受大小限制，远程媒体只保存脱敏后的 URL。

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
npm run test:models
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
