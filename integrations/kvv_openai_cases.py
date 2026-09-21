"""Independent OpenAI Chat Completions acceptance probes, not Kimi groundtruth.

Top-level tools do not verify system.tools dynamic loading. Usage checks do not
compare a provider to Kimi's tokenizer. Requests are bounded and pass through the
existing transport recorder; this module never executes returned tool code.
"""
from __future__ import annotations
import base64
import contextvars
from io import BytesIO
import json
import os
import uuid
import httpx
import pytest

PRECHECK = [
    "test_openai_chat_completion", "test_openai_stream_and_usage",
    "test_openai_max_tokens_one", "test_openai_invalid_max_tokens",
    "test_openai_tool_required", "test_openai_tool_none", "test_openai_multiple_tools",
    "test_openai_json_object", "test_openai_image_groundtruth",
    "test_openai_cache_repeat", "test_openai_usage_accounting",
]

# (title, category, method, expected, next_step); used by the shared report.
_SPECS = {
"chat_completion": ("OpenAI 非流式对话", "基础协议", "发送 messages 和 stream=false，要求只回答 OK。", "HTTP 成功、非空 message.content、有效 finish_reason；不以模型自述证明身份。", "检查 /chat/completions 路由、模型映射和 choices/message 结构。"),
"stream_and_usage": ("OpenAI SSE 与流式 usage", "流式协议", "发送 stream=true/include_usage=true，完整消费 SSE。", "有效 JSON 帧、非空内容、finish_reason、一个 [DONE] 和相加一致的 usage。", "检查 SSE 截断、流内 error、终止帧及 usage 是否完整转发。"),
"max_tokens_one": ("max_tokens=1 上限", "Token 上限", "要求长输出，同时发送 max_tokens=1。", "请求成功且 completion_tokens 为 0 或 1；缺少 usage 为无法判定，不用字符数猜 token。", "检查 max_tokens 是否透传，以及模型是否改用 max_completion_tokens。"),
"invalid_max_tokens": ("非法 max_tokens 拒绝", "参数边界", "发送 max_tokens=-1。", "HTTP 400/422 且错误明确指向 max_tokens；鉴权和限流不算正确拒绝。", "检查参数校验和被测参数名是否在错误体中保留。"),
"tool_required": ("标准顶层工具强制调用", "工具调用", "顶层声明 Calculator，设置 tool_choice=required。", "真实 tool_calls、正确工具名、可解析 expr；不验证 Kimi system.tools 动态加载。", "检查 tools、tool_choice、function.arguments 是否完整透传。"),
"tool_none": ("禁止工具调用", "工具调用", "声明工具并设置 tool_choice=none。", "无 tool_calls，且有非空可见内容。", "检查 tool_choice=none 是否被忽略。"),
"multiple_tools": ("多个标准顶层工具", "工具调用", "顶层同时声明 Calculator/WeatherQuery，明确要求 Calculator，设置 required。", "返回 Calculator 及有效 expr；只证明标准顶层工具选择。", "检查工具列表、名称映射及强制选择是否丢失。"),
"json_object": ("JSON Object 输出", "结构化输出", "发送 response_format=json_object，要求 ready=true。", "可解析对象且 ready 严格为布尔 true；允许合法 JSON 空白。", "检查 JSON 模式支持和 response_format 透传。"),
"image_groundtruth": ("内置图片识别实测", "多模态", "直接嵌入无文字红绿蓝色块 PNG，只问左右顺序，提示词不透露答案。", "精确识别 red、green、blue 顺序；HTTP 成功本身不算识别正确。", "检查 image_url 是否确实送到视觉模型，是否只保留文本。"),
"cache_repeat": ("重复前缀缓存观测", "缓存", "两次发送相同较长前缀和小输出预算，读取缓存计数。", "两次成功，第二次正读取计数才表示观察到命中；无字段或零命中为无法判定。", "核对缓存前缀门槛、生效条件及 usage 字段，再与上游账单交叉验证。"),
"usage_accounting": ("OpenAI usage 账本", "Token 计量", "短文本请求，校验计数类型、范围及总和。", "total=prompt+completion，均为非负整数，细项不超过父项；不套用 Kimi 精确 token 基准。", "检查 usage 映射、缓存重复计入及流式/非流式计量口径。"),
"tool_roundtrip": ("工具结果回传闭环", "工具调用", "先强制 Calculator，再按原 tool_call_id 回传固定结果；不执行模型返回代码。", "工具 ID 对齐，两次成功，最终正确输出 1081。", "检查多轮 assistant.tool_calls、role=tool 与 tool_call_id。"),
"named_tool_choice": ("指定函数调用", "工具调用", "两个工具中用对象式 tool_choice 指定 Calculator。", "只调用 Calculator 且参数有效；明确不支持该形式时为不适用。", "检查具名 tool_choice 是否被改成 auto/required。"),
"parallel_tools": ("并行工具调用观测", "工具调用", "parallel_tool_calls=true，要求同次查询北京和上海天气。", "同一响应出现两个城市调用；单样本未观察到并行为无法判定。", "核对并行参数支持及多工具增量是否丢失。"),
"strict_json_schema": ("闭合对象 JSON Schema", "结构化输出", "strict=true、additionalProperties=false、所有属性必填。", "输出只包含 ready=true、整数 count=3，严格满足 Schema。", "核对 Structured Outputs 支持，以及 strict/schema 是否透传。"),
"reasoning_effort": ("reasoning_effort 请求与证据", "推理参数", "发送 low 和 max_completion_tokens，不发送 thinking/chat_template_kwargs。", "请求成功且答案正确；有正 reasoning_tokens 才确认可观测推理活动，否则无法判定。", "核对模型 effort 枚举与 reasoning_tokens；不依赖推理文本长度或自述。"),
"video_url_extension": ("video_url 扩展接入", "多模态扩展", "在 messages 中传公开视频 URL；明确作为兼容渠道扩展。", "成功且有描述仅确认扩展可执行，不证明画面识别准确；明确不支持时不适用。", "核对 video_url 扩展约定；标准接口可另测抽帧图片，扩展拒绝不代表模型造假。"),
"temperature_zero": ("temperature=0 接受性", "参数边界", "发送 temperature=0 的短请求。", "成功且有可见内容；明确不支持采样参数时为不适用。", "核对该模型允许的采样参数，不能强套 Kimi 的固定取值。"),
"invalid_temperature": ("temperature 负值拒绝", "参数边界", "发送 temperature=-1。", "HTTP 400/422 且错误指向 temperature；不使用 Kimi 不可变参数基线。", "核对采样范围校验及参数名错误映射。"),
"invalid_message_role": ("非法 message role 拒绝", "参数边界", "发送 role=workbench_invalid_role。", "HTTP 400/422 且错误指向 role/messages。", "检查协议校验，防止网关静默删除非法消息。"),
"multi_image_groundtruth": ("两图独立识别", "多模态", "嵌入红绿蓝、蓝红绿两张不同色块图，分别询问顺序；不透露答案。", "两组颜色顺序均符合各自真值。", "检查后续图片是否截断、是否错误复用第一张。"),
"max_completion_tokens_one": ("max_completion_tokens=1 上限", "Token 上限", "只发送 max_completion_tokens=1，不混用 max_tokens。", "completion_tokens≤1；不支持该字段为不适用，缺少计量为无法判定。", "按模型契约选择上限字段，检查是否包含推理 token。"),
}
OPENAI_CASE_SPECS = {
    "test_openai_" + name: dict(zip(("title", "category", "method", "expected", "next_step"), spec))
    for name, spec in _SPECS.items()
}
for _name, _spec in OPENAI_CASE_SPECS.items():
    _spec["dimensions"] = (["multimodal"] if "image" in _name or "video" in _name else
                           ["tools"] if "tool" in _name else
                           ["max_tokens"] if "max_tokens" in _name or "max_completion_tokens" in _name else
                           ["cache"] if "cache" in _name or "usage_accounting" in _name else
                           ["protocol", "reliability"] if _name in ('test_openai_chat_completion','test_openai_stream_and_usage') else
                           ["protocol"])

_OBSERVATIONS = contextvars.ContextVar("openai_probe_observations", default=None)

def _observe(kind, **values):
    observations = _OBSERVATIONS.get()
    if observations is not None and len(observations) < 40:
        observations.append({"kind": kind, **values})

@pytest.fixture(autouse=True)
def _record_observations(record_property):
    observations = []
    token = _OBSERVATIONS.set(observations)
    # Register the mutable list before the call phase so passed call reports
    # carry observations without requiring a second teardown result row.
    record_property("workbench_observations", observations)
    try:
        yield
    finally:
        _OBSERVATIONS.reset(token)

CALCULATOR = {"type":"function","function":{"name":"Calculator","description":"Calculate one arithmetic expression.","parameters":{"type":"object","properties":{"expr":{"type":"string"}},"required":["expr"]}}}
WEATHER = {"type":"function","function":{"name":"WeatherQuery","description":"Look up weather for a city.","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}
CALC_PROMPT = "Use Calculator to calculate 23 * 47. Do not calculate it yourself."
VIDEO_URL = "https://sf1-cdn-tos.huoshanstatic.com/obj/media-fe/xgplayer_doc_video/mp4/xgplayer-demo-360p.mp4"

@pytest.fixture
def model():
    value = os.environ.get("MODEL_NAME", "").strip()
    if not value:
        pytest.fail("缺少 MODEL_NAME；未发起渠道请求。")
    return value

@pytest.fixture
def hclient():
    base = os.environ.get("KIMI_BASE_URL", "").strip()
    if not base or not os.environ.get("KIMI_API_KEY"):
        pytest.fail("缺少渠道地址或密钥；未发起渠道请求。")
    with httpx.Client(base_url=base.rstrip("/") + "/", follow_redirects=False, trust_env=False,
                      headers={"Authorization":"Bearer " + os.environ["KIMI_API_KEY"]},
                      timeout=float(os.environ.get("WORKBENCH_REQUEST_TIMEOUT", "120"))) as client:
        yield client

def _safe(value):
    text = str(value)
    secret = os.environ.get("KIMI_API_KEY")
    if secret:
        text = text.replace(secret, "[REDACTED]")
    return text[:1200]

def _payload(model, messages=None, **kwargs):
    payload = {"model":model, "messages":messages or [{"role":"user","content":"Reply with OK only."}], "stream":False, "max_tokens":96}
    if "max_completion_tokens" in kwargs:
        payload.pop("max_tokens")
    payload.update(kwargs)
    return payload

def _post(client, model, messages=None, **kwargs):
    return client.post("chat/completions", json=_payload(model, messages, **kwargs))

def _body(response):
    _observe("http_response", http_status=response.status_code)
    assert 200 <= response.status_code < 300, f"HTTP {response.status_code}：{_safe(response.text)}"
    try:
        value = response.json()
    except ValueError:
        pytest.fail("响应不是有效 JSON：" + _safe(response.text))
    assert isinstance(value, dict), "响应顶层必须是 JSON 对象"
    assert not value.get("error"), "HTTP 成功但携带 error：" + _safe(value["error"])
    return value

def _choice(body):
    choices = body.get("choices")
    assert isinstance(choices, list) and choices and isinstance(choices[0], dict), "响应缺少 choices[0]"
    assert isinstance(choices[0].get("message"), dict), "响应缺少 message 对象"
    return choices[0]

def _text(body):
    value = _choice(body)["message"].get("content")
    assert isinstance(value, str) and value.strip(), "响应缺少非空可见文本"
    value = value.strip()
    _observe("visible_response", text=_safe(value))
    return value

def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0

def _usage(body):
    usage = body.get("usage")
    assert isinstance(usage, dict), "响应未提供 usage 对象"
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        assert _integer(usage.get(name)), f"usage.{name} 必须是非负整数，实际={_safe(usage.get(name))}"
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"], "total_tokens 不等于 prompt_tokens + completion_tokens"
    for group, field, upper in (("prompt_tokens_details","cached_tokens","prompt_tokens"), ("completion_tokens_details","reasoning_tokens","completion_tokens")):
        details = usage.get(group)
        if details is not None:
            assert isinstance(details, dict), f"usage.{group} 必须为对象或 null"
            if details.get(field) is not None:
                assert _integer(details[field]), f"usage.{group}.{field} 必须是非负整数"
                assert details[field] <= usage[upper], f"usage.{group}.{field} 超过 {upper}"
    _observe("usage", prompt_tokens=usage["prompt_tokens"], completion_tokens=usage["completion_tokens"],
             total_tokens=usage["total_tokens"],
             cached_tokens=(usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
             reasoning_tokens=(usage.get("completion_tokens_details") or {}).get("reasoning_tokens"))
    return usage

def _attributed_error(response, names):
    return response.status_code in (400,422) and any(name.lower() in response.text.lower() for name in names)

def _optional(response, names):
    # Auth/rate/network errors remain failures for the transport classifier.
    unsupported = ("unsupported","not supported","does not support","unknown parameter","unrecognized","not allowed","不支持","不允许")
    if _attributed_error(response, names) and any(word in response.text.lower() for word in unsupported):
        pytest.skip("能力未验证：渠道明确不支持 " + "/".join(names) + "；" + _safe(response.text))

def _rejected(response, names):
    _observe("parameter_rejection", http_status=response.status_code, parameters=list(names),
             attributed=_attributed_error(response, names))
    assert _attributed_error(response, names), "应返回 HTTP 400/422 并明确指向 " + "/".join(names) + f"，实际 HTTP {response.status_code}：{_safe(response.text)}"

def _calls(body, expected=None, allowed=None):
    choice = _choice(body)
    calls = choice["message"].get("tool_calls")
    assert choice.get("finish_reason") == "tool_calls", "工具调用的 finish_reason 应为 tool_calls"
    assert isinstance(calls, list) and calls, "未返回真实 tool_calls"
    ids = set()
    for call in calls:
        assert isinstance(call, dict) and call.get("type") == "function", "tool_calls 条目必须为 function"
        assert isinstance(call.get("id"),str) and call["id"] and call["id"] not in ids, "工具调用 id 缺失或重复"
        ids.add(call["id"])
        function = call.get("function")
        assert isinstance(function,dict) and isinstance(function.get("name"),str), "缺少 function.name"
        try:
            arguments = json.loads(function.get("arguments",""))
        except (ValueError,TypeError):
            pytest.fail("function.arguments 不是有效 JSON 字符串")
        assert isinstance(arguments,dict), "工具参数必须为 JSON 对象"
        name = function["name"]
        assert name in (allowed or {"Calculator","WeatherQuery"}), "返回未声明的工具：" + _safe(name)
        field = "expr" if name == "Calculator" else "city"
        assert isinstance(arguments.get(field),str) and arguments[field].strip(), "缺少有效工具参数 " + field
    if expected:
        assert any(call["function"]["name"] == expected for call in calls), "未调用要求的工具 " + expected
    _observe("tool_calls", names=[call["function"]["name"] for call in calls], count=len(calls))
    return calls

def _fixture_image(order=("red","green","blue")):
    """A deterministic real PNG, with no text that leaks the answer."""
    from PIL import Image, ImageDraw
    canvas = Image.new("RGB", (600,240), "white")
    draw = ImageDraw.Draw(canvas)
    colors = {"red":(235,32,32), "green":(20,190,65), "blue":(30,80,235)}
    for index, color in enumerate(order):
        draw.rectangle((25+index*195,30,185+index*195,210), fill=colors[color])
    stream = BytesIO()
    canvas.save(stream,"PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")

def _colors(text):
    try:
        value = json.loads(text)
    except ValueError:
        pytest.fail("图片识别结果不是要求的 JSON 数组：" + _safe(text))
    assert isinstance(value,list), "图片识别结果应为 JSON 数组"
    return value

def _sse(client, payload):
    content, usage, finishes, done, events = [], None, [], 0, 0
    with client.stream("POST","chat/completions",json=payload) as response:
        if not 200 <= response.status_code < 300:
            response.read()
            pytest.fail(f"HTTP {response.status_code}：{_safe(response.text)}")
        assert "text/event-stream" in response.headers.get("content-type","").lower(), "流式响应 Content-Type 不是 text/event-stream"
        data, event_type = [], ""
        def consume():
            nonlocal usage, done, events
            if not data:
                assert event_type != "error", "收到空 event:error 帧"
                return
            raw = "\n".join(data)
            if raw.strip() == "[DONE]":
                done += 1
                return
            assert not done, "[DONE] 之后仍返回 data 帧"
            try:
                item = json.loads(raw)
            except ValueError:
                pytest.fail("SSE data 不是有效 JSON：" + _safe(raw))
            assert isinstance(item,dict), "SSE data 顶层必须是对象"
            assert event_type != "error" and not item.get("error"), "SSE 流内错误：" + _safe(item)
            events += 1
            assert events <= 5000, "SSE 帧数超过短探针预算"
            if item.get("usage") is not None:
                usage = item["usage"]
            choices = item.get("choices",[])
            assert isinstance(choices,list), "SSE choices 必须是数组"
            for choice in choices:
                assert isinstance(choice,dict), "SSE choice 必须是对象"
                delta = choice.get("delta") or {}
                assert isinstance(delta,dict), "SSE delta 必须是对象"
                if delta.get("content"):
                    assert isinstance(delta["content"],str), "SSE content 必须为文本"
                    content.append(delta["content"])
                if choice.get("finish_reason"):
                    finishes.append(choice["finish_reason"])
        for line in response.iter_lines():
            line = line.lstrip("\ufeff")
            if not line:
                consume()
                data, event_type = [], ""
            elif line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
                assert sum(len(part) for part in data) <= 1024*1024, "SSE 单帧过大"
            elif line.startswith("event:"):
                event_type = line[6:].strip()
        assert not data, "SSE 在未完成的 data 帧中结束"
    assert done == 1, f"期望一个 [DONE]，实际 {done}"
    assert events and "".join(content).strip(), "流式响应没有可见文本"
    assert finishes and all(reason in ("stop","length","content_filter") for reason in finishes), "流式响应缺少或包含无效 finish_reason"
    _observe("sse", json_events=events, done_markers=done, finish_reasons=finishes, visible_text=_safe("".join(content)))
    _usage({"usage":usage})
    return {"content":"".join(content),"usage":usage}

def test_openai_chat_completion(hclient, model):
    body = _body(_post(hclient,model))
    _text(body)
    assert _choice(body).get("finish_reason") in ("stop","length","content_filter"), "缺少有效 finish_reason"

def test_openai_stream_and_usage(hclient, model):
    _sse(hclient,_payload(model,stream=True,stream_options={"include_usage":True}))

def _token_limit(body):
    _choice(body)
    usage = body.get("usage")
    if not isinstance(usage,dict) or usage.get("completion_tokens") is None:
        pytest.skip("能力未验证：没有 completion_tokens，不能用字符数替代 token 上限验证。")
    value = usage["completion_tokens"]
    _observe("token_limit", requested_limit=1, completion_tokens=value)
    assert _integer(value) and value <= 1, f"输出上限为 1，但 completion_tokens={_safe(value)}"

def test_openai_max_tokens_one(hclient, model):
    _token_limit(_body(_post(hclient,model,[{"role":"user","content":"Write the numbers from one to twenty in full words."}],max_tokens=1)))

def test_openai_invalid_max_tokens(hclient, model):
    _rejected(_post(hclient,model,max_tokens=-1),("max_tokens",))

def test_openai_tool_required(hclient, model):
    _calls(_body(_post(hclient,model,[{"role":"user","content":CALC_PROMPT}],tools=[CALCULATOR],tool_choice="required")),"Calculator",allowed={"Calculator"})

def test_openai_tool_none(hclient, model):
    body = _body(_post(hclient,model,tools=[CALCULATOR],tool_choice="none"))
    assert not _choice(body)["message"].get("tool_calls"), "tool_choice=none 仍返回工具调用"
    _text(body)

def test_openai_multiple_tools(hclient, model):
    _calls(_body(_post(hclient,model,[{"role":"user","content":CALC_PROMPT}],tools=[WEATHER,CALCULATOR],tool_choice="required")),"Calculator")

def test_openai_json_object(hclient, model):
    body = _body(_post(hclient,model,[{"role":"user","content":'Return a JSON object with one field "ready" set to true.'}],response_format={"type":"json_object"}))
    value = json.loads(_text(body))
    assert isinstance(value,dict) and value.get("ready") is True, "JSON 对象不满足 ready=true"

def test_openai_image_groundtruth(hclient, model):
    content = [{"type":"text","text":"Name the colors of the three large solid blocks from left to right. Return only a JSON array of lowercase English color names, without markdown."},
               {"type":"image_url","image_url":{"url":_fixture_image(),"detail":"low"}}]
    value = _colors(_text(_body(_post(hclient,model,[{"role":"user","content":content}]))))
    assert value == ["red","green","blue"], "图片颜色顺序与内置真值不一致"

def _cache_reads(usage):
    values = []
    details = usage.get("prompt_tokens_details")
    if isinstance(details,dict) and details.get("cached_tokens") is not None:
        values.append(details["cached_tokens"])
    for name in ("cached_tokens","cache_read_input_tokens","prompt_cache_hit_tokens","prompt_cache_read_tokens"):
        if usage.get(name) is not None:
            values.append(usage[name])
    for value in values:
        assert _integer(value), "缓存读取计数必须是非负整数"
        assert value <= usage["prompt_tokens"], "缓存读取计数超过 prompt_tokens"
    return values

def test_openai_cache_repeat(hclient, model):
    prompt = "CACHE OBSERVATION " + uuid.uuid4().hex + "\n" + ("This is a fixed public acceptance fixture. Keep this prefix unchanged. The requested answer is READY. " * 100)
    messages = [{"role":"user","content":prompt}]
    first = _usage(_body(_post(hclient,model,messages,max_tokens=8)))
    second = _usage(_body(_post(hclient,model,messages,max_tokens=8)))
    _cache_reads(first)
    counters = _cache_reads(second)
    _observe("cache_repeat", first_read_counters=_cache_reads(first), second_read_counters=counters,
             cache_hit_observed=any(value > 0 for value in counters))
    if not counters:
        pytest.skip("能力未验证：重复请求成功，但未返回可核验的缓存读取 usage 字段。")
    if not any(value > 0 for value in counters):
        pytest.skip("能力未验证：缓存读取计数为 0，本轮未观察到命中，不能认定渠道不支持缓存。")

def test_openai_usage_accounting(hclient, model):
    _usage(_body(_post(hclient,model,max_tokens=16)))

def test_openai_tool_roundtrip(hclient, model):
    messages = [{"role":"user","content":CALC_PROMPT}]
    first = _body(_post(hclient,model,messages,tools=[CALCULATOR],tool_choice="required"))
    calls = _calls(first,"Calculator",allowed={"Calculator"})
    assert len(calls) == 1, "闭环探针期望单次 Calculator 调用"
    messages += [{"role":"assistant","content":None,"tool_calls":calls},
                 {"role":"tool","tool_call_id":calls[0]["id"],"content":'{"result":1081}'},
                 {"role":"user","content":"Return only the numeric result supplied by the tool."}]
    body = _body(_post(hclient,model,messages,tools=[CALCULATOR],tool_choice="none"))
    assert _text(body) == "1081", "工具结果回传后未正确输出 1081"

def test_openai_named_tool_choice(hclient, model):
    response = _post(hclient,model,[{"role":"user","content":CALC_PROMPT}],tools=[WEATHER,CALCULATOR],tool_choice={"type":"function","function":{"name":"Calculator"}})
    _optional(response,("tool_choice",))
    calls = _calls(_body(response),"Calculator")
    assert all(call["function"]["name"] == "Calculator" for call in calls), "具名调用返回其他工具"

def test_openai_parallel_tools(hclient, model):
    response = _post(hclient,model,[{"role":"user","content":"Call WeatherQuery once for Beijing and once for Shanghai. Return both tool calls together, without waiting for either result."}],tools=[WEATHER],tool_choice="required",parallel_tool_calls=True,max_tokens=256)
    _optional(response,("parallel_tool_calls",))
    calls = _calls(_body(response),"WeatherQuery",allowed={"WeatherQuery"})
    cities = [json.loads(call["function"]["arguments"])["city"].lower() for call in calls]
    if not (len(calls)>=2 and any("beijing" in city or "北京" in city for city in cities) and any("shanghai" in city or "上海" in city for city in cities)):
        pytest.skip("能力未验证：本轮未观察到两个指定城市的并行调用，不能根据单样本认定不支持。")

def test_openai_strict_json_schema(hclient, model):
    schema = {"type":"object","properties":{"ready":{"type":"boolean"},"count":{"type":"integer"}},"required":["ready","count"],"additionalProperties":False}
    response = _post(hclient,model,[{"role":"user","content":"Return ready=true and count=3."}],response_format={"type":"json_schema","json_schema":{"name":"workbench_status","strict":True,"schema":schema}})
    value = json.loads(_text(_body(response)))
    assert isinstance(value,dict) and set(value)=={"ready","count"} and value["ready"] is True and type(value["count"]) is int and value["count"]==3, "JSON Schema 输出与请求约束不一致"

def test_openai_reasoning_effort(hclient, model):
    response = _post(hclient,model,[{"role":"user","content":"There are 35 chickens and rabbits in total with 94 legs. Return only the number of rabbits."}],reasoning_effort="low",max_completion_tokens=1024)
    _optional(response,("reasoning_effort","max_completion_tokens"))
    body = _body(response)
    assert _text(body).strip().rstrip(".。") == "12", "reasoning_effort 请求未给出正确问题答案"
    usage = _usage(body)
    details = usage.get("completion_tokens_details")
    if not isinstance(details,dict) or not details.get("reasoning_tokens"):
        pytest.skip("能力未验证：请求成功，但无正 reasoning_tokens，无法确认参数生效；不要求泄露推理文本。")

def test_openai_video_url_extension(hclient, model):
    response = _post(hclient,model,[{"role":"user","content":[
        {"type":"text","text":"请简短描述视频中的实际画面；若无法读取视频请明确说明。"},
        {"type":"video_url","video_url":{"url":VIDEO_URL}},
    ]}],max_tokens=300)
    _optional(response,("video_url","video","content.type","content type"))
    text = _text(_body(response))
    if any(term in text.lower() for term in ("无法读取","无法访问","无法观看","can't view","cannot view","cannot access","unable to access")):
        pytest.skip("能力未验证：请求被接受，但模型明确表示无法读取视频。" + _safe(text))

def test_openai_temperature_zero(hclient, model):
    response = _post(hclient,model,temperature=0)
    _optional(response,("temperature",))
    _text(_body(response))

def test_openai_invalid_temperature(hclient, model):
    _rejected(_post(hclient,model,temperature=-1),("temperature",))

def test_openai_invalid_message_role(hclient, model):
    _rejected(_post(hclient,model,[{"role":"workbench_invalid_role","content":"hello"}]),("role","messages"))

def test_openai_multi_image_groundtruth(hclient, model):
    content = [{"type":"text","text":"For each image, list the three large blocks' colors from left to right. Return only a JSON array containing two arrays of lowercase English color names, in image order. Do not use markdown."},
               {"type":"image_url","image_url":{"url":_fixture_image(),"detail":"low"}},
               {"type":"image_url","image_url":{"url":_fixture_image(("blue","red","green")),"detail":"low"}}]
    value = _colors(_text(_body(_post(hclient,model,[{"role":"user","content":content}]))))
    assert value == [["red","green","blue"],["blue","red","green"]], "两张图片的顺序/颜色识别不符合内置真值"

def test_openai_max_completion_tokens_one(hclient, model):
    response = _post(hclient,model,max_completion_tokens=1)
    _optional(response,("max_completion_tokens",))
    _token_limit(_body(response))
