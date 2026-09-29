# Claude 专项：请求约定与判定依据

测试规则版本：`claude-2026-09-29`，随桌面版 **1.5.5 / build 15** 发布。

此次修复针对测试器自身的请求和判定问题。官方 Key 不能使不受支持的参数组合合法，也不能保证账户配额、网络和上游容量始终可用。报告将协议符合性、能力实测、参数适用范围和服务可用性分别记录；不会因为一项失败就认定整个模型不可用。

## 怎样重测

1. 直连 Anthropic 时选择 **Claude 专项 → Messages → x-api-key**，Base URL 填 `https://api.anthropic.com`，选择该账户实际可用的模型。
2. 使用渠道提供的兼容地址时按其实际协议选择 Messages 或 OpenAI。官方 Key 加上中转地址仍不能证明直接连接官方；AWS 原生鉴权与内部代签也不能从兼容接口响应中得到认证。
3. 选择需要验证的模块和参数矩阵，先查看请求预览。输出截断矩阵包括 1 / 10 / 20，完整方案另含 64 / 128 / 256；原专项与追加矩阵会分别记录证据。
4. 重新运行后，在 HTML 结论区确认规则版本。旧 HTML 文件不会自动更新；从历史重新导出也不会替你重跑请求。

## 本轮修复及证据

| 证据 | 旧问题与影响 | 当前规则 | 回归位置 |
| --- | --- | --- | --- |
| E1：历史样本中 48 条 HTTP 200 压测响应只多一个句号 | 提示词在随机标记后紧接句号，断言却要求无句号，整阶段被重复判失败 | 标记独立成行，允许外围空白和标点；错误标记、串请求及重复响应 ID 仍检出 | `test_acceptance_matrix.py` |
| E2：官方 Messages API 定义 `max_tokens` 下限为 0 | 把合法零输出响应当非法参数未拒绝 | 原生 0 必须为空内容、零输出计量、`max_tokens` 停止原因；-1 仍须参数拒绝 | `test_claude_acceptance.py`、`test_acceptance_matrix.py` |
| E3：官方模型代际的参数支持不同 | 统一发送 adaptive、强制工具、prefill 或自定义采样参数制造 400 | 共用模型兼容表选择请求；未知别名保留不确定性，不按名称认证真实后端 | `claude_profile.py`、三个检测器的测试文件 |
| E4：旧签名探针放在已结束的历史轮次 | 部分官方模型可丢弃旧 thinking，接受该请求不能证明签名校验失效 | 获取真实签名与工具调用，在活动工具轮中先原样回传，再篡改或替换签名 | `test_claude_acceptance.py` |
| E5：拒绝文本中可能引用攻击标记 | 把引用等同执行攻击，或把解释性拒绝当失败 | 随机私有标记泄露、明确执行覆盖与正常拒绝分别判断；仅输出形式变化不能证明上游加词 | `test_claude_acceptance.py`、`test_acceptance_matrix.py` |
| E6：HTTP 429 / 529、预算用尽、格式化数字 | 服务受阻或答案展示格式被误判能力不足 | 原始错误和可用性失败保留；缺少行为证据不扣能力分，数字分组与 JSON 空白按语义比较 | `test_channel_production.py`、`test_claude_reports.py` |

以上证据形成的修复路径是：模型及协议配置 → 适用范围检查 → 构造合法探针 → 保存真实请求与响应 → 按独立断言分类 → 统一报告。正对照未成立时，不把后续负对照的普通拒绝算作通过。

## 关键适用条件

| 测试 | 适用条件与解释 |
| --- | --- |
| Thinking | 支持 extended thinking 的较早型号使用 enabled 和至少 1,024 的 budget；支持 adaptive 的型号使用相应配置。始终思考型号不发送不允许的 disabled。预算必须保留输出空间。 |
| 工具 | 支持强制模式才发送 `tool` / `any`。仅支持 `auto` / `none` 的型号仍验证工具参数、真实 ID 和结果回填；auto 没选工具不能证明不支持工具。 |
| 零输出 | `max_tokens=0` 为非流式预热行为，不与强制工具、enabled thinking 或结构化输出组合。该项本身不证明缓存已经命中。 |
| 签名 | 只在取得真实签名和可复用上下文后做正负对照；未得到签名不会补造签名证据。接受签名不能认证模型身份。 |
| 多模态 | 原生 Messages 使用正式 image content block。未定义的原生视频、音频块标记不适用，不用非法请求证明 Claude 不行；兼容协议扩展另行观察。 |
| 缓存 | 以原生读写 usage 确认命中。实际前缀长度、重复/后缀变化/前缀变化对照单列；字段缺失不是 0，耗时变快不是命中证据。 |
| 注入 | 上游加词观察与合成抗注入测试分开。黑盒返回文本可能自述或编造，确认上游加词仍需核对上游最终请求或日志。 |
| 压测 | 协议成功率、语义匹配、限流/过载、重试及延迟各自保留。将受阻样本标为待判定，不会提高业务交付成功率。 |

## 验证方式及边界

本轮在用户授权的项目中修复测试器，使用官方文档、已保存的脱敏技术字段和模拟 HTTP/SSE 响应验证。**未调用用户付费模型接口，因此不是一次官方 Key 端到端验收，也不承诺任意中转实现都能通过。** 历史报告原始请求与响应保持不变，旧规则运行会在新版导出中提示重测。

从项目根目录执行离线回归（桌面构建环境已准备时）：

```sh
PYTHONPATH=integrations desktop/.build-venv/bin/python -m unittest \
  test_claude_acceptance test_acceptance_matrix test_channel_production \
  test_acceptance_results test_claude_reports test_report_matrix \
  test_report_content test_report_brief -q
```

测试既包括合法响应，也包括真实错误工具参数、超出输出上限、错误随机标记、实际标记泄露、重复 ID 和无效协议结构，避免单纯放松断言得到高分。每次回归只验证模拟响应与测试器约定一致；真实模型覆盖范围以新生成的逐请求证据为准。

## 官方参考

以下文档于 2026-09-29 核对；云 API 行为更新时需要重新审查兼容表。

- [Messages API 请求与响应](https://platform.claude.com/docs/en/api/messages/create)
- [Thinking 模式与型号支持](https://platform.claude.com/docs/en/build-with-claude/thinking)
- [Extended thinking 限制](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)
- [消息与 assistant prefill](https://platform.claude.com/docs/en/build-with-claude/working-with-messages)
- [Prompt caching 与零 Token 预热](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)

模型名称用于选择已知接口参数，不是来源证据。这里的“官方接口约定”表示测试器对文档的实现依据，不表示本项目获得 Anthropic 官方认证。
