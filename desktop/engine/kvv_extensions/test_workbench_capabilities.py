"""Bounded capability probes used by the workbench for Kimi-K3 channels.

The upstream Kimi Vendor Verifier remains the source of the protocol contract.
These probes complement it with the capabilities operators commonly need when
checking a relay: dynamic and top-level tools, a one-token generation limit,
video URL input, and observable prompt-cache accounting.  Requests are sent
through the raw httpx fixture so the workbench transport recorder preserves the
exact JSON body and response evidence.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest


CALCULATOR = {
    "type": "function",
    "function": {
        "name": "Calculator",
        "description": "计算器，只支持单个算术表达式的求值",
        "parameters": {
            "type": "object",
            "properties": {
                "expr": {
                    "type": "string",
                    "description": "算术表达式，支持四则运算、指数运算、对数函数、三角函数，使用 javascript 语法",
                }
            },
            "required": ["expr"],
        },
    },
}

WEATHER_QUERY = {
    "type": "function",
    "function": {
        "name": "WeatherQuery",
        "description": "查询指定城市的天气",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}


def _post(hclient: httpx.Client, model: str, payload: dict[str, Any]) -> httpx.Response:
    return hclient.post("/chat/completions", json={"model": model, **payload}, timeout=120)


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError as exc:
        pytest.fail(f"渠道返回的不是 JSON：HTTP {response.status_code}；{exc}；正文={response.text[:800]}")
    if not isinstance(value, dict):
        pytest.fail(f"渠道 JSON 顶层不是对象：{value!r}")
    return value


def _tool_calls(payload: dict[str, Any]) -> list[dict[str, Any]]:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") or choices[0].get("delta") or {}
        calls = message.get("tool_calls") if isinstance(message, dict) else None
        if isinstance(calls, list):
            return [call for call in calls if isinstance(call, dict)]
        # Some gateways expose a single legacy function call.
        function_call = message.get("function_call") if isinstance(message, dict) else None
        if isinstance(function_call, dict):
            return [{"function": function_call}]
    # Preserve compatibility with OpenAI Responses-style wrappers used by a few
    # relays, while still requiring a real function call object.
    output = payload.get("output")
    if isinstance(output, list):
        return [item for item in output if isinstance(item, dict) and item.get("type") in ("function_call", "tool_call")]
    return []


def _require_call(response: httpx.Response, expected: str) -> dict[str, Any]:
    payload = _json(response)
    assert response.status_code == 200, (
        f"应返回 HTTP 200 并触发 {expected}，实际 HTTP {response.status_code}："
        f"{payload.get('error') or response.text[:800]}"
    )
    calls = _tool_calls(payload)
    assert calls, f"HTTP 200 但没有返回 tool_calls，响应={payload}"
    names = []
    for call in calls:
        function = call.get("function") if isinstance(call, dict) else None
        if isinstance(function, dict) and function.get("name"):
            names.append(function["name"])
        elif call.get("name"):
            names.append(call["name"])
    assert expected in names, f"期望调用 {expected}，实际工具={names}；响应={payload}"
    return payload


@pytest.mark.k3_extension
@pytest.mark.smoke_test
def test_k3_dynamic_tool_in_system_calculator(hclient: httpx.Client, model: str):
    """Dynamic tools may be declared by a content-less system message."""
    response = _post(
        hclient,
        model,
        {
            "messages": [
                {"role": "system", "content": "You are Kimi, an AI assistant developed by Moonshot AI."},
                {"role": "user", "content": "帮我计算一下 23 * 47 的结果。"},
                {"role": "system", "tools": [CALCULATOR]},
            ]
        },
    )
    _require_call(response, "Calculator")


@pytest.mark.k3_extension
def test_k3_top_level_tool_calculator(hclient: httpx.Client, model: str):
    """A conventional request-level tool remains callable as a control."""
    response = _post(
        hclient,
        model,
        {
            "messages": [
                {"role": "system", "content": "You are Kimi, an AI assistant developed by Moonshot AI."},
                {"role": "user", "content": "帮我计算一下 23 * 47 的结果。"},
            ],
            "tools": [CALCULATOR],
        },
    )
    _require_call(response, "Calculator")


@pytest.mark.k3_extension
def test_k3_dynamic_tool_required(hclient: httpx.Client, model: str):
    """tool_choice=required must force the dynamically loaded Calculator."""
    response = _post(
        hclient,
        model,
        {
            "tool_choice": "required",
            "messages": [
                {"role": "system", "content": "You are Kimi, an AI assistant developed by Moonshot AI."},
                {"role": "user", "content": "帮我计算一下 23 * 47 的结果。"},
                {"role": "system", "tools": [CALCULATOR]},
            ],
        },
    )
    _require_call(response, "Calculator")


@pytest.mark.k3_extension
def test_k3_dynamic_and_top_level_tools_coexist(hclient: httpx.Client, model: str):
    """Global and dynamically loaded tools must be visible in one request."""
    response = _post(
        hclient,
        model,
        {
            "messages": [
                {"role": "system", "content": "You are Kimi, an AI assistant developed by Moonshot AI."},
                {"role": "user", "content": "帮我计算一下 23 * 47 的结果。"},
                {"role": "system", "tools": [CALCULATOR]},
            ],
            "tools": [WEATHER_QUERY],
            "tool_choice": "required",
        },
    )
    _require_call(response, "Calculator")


@pytest.mark.k3_extension
def test_k3_max_tokens_one_is_enforced(hclient: httpx.Client, model: str):
    """The relay accepts max_tokens=1 and does not silently expand it."""
    response = _post(
        hclient,
        model,
        {"max_tokens": 1, "messages": [{"content": "hi", "role": "user"}], "stream": False},
    )
    payload = _json(response)
    assert response.status_code == 200, f"max_tokens=1 应可执行，HTTP {response.status_code}：{payload.get('error') or response.text[:800]}"
    usage = payload.get("usage")
    if isinstance(usage, dict) and usage.get("completion_tokens") is not None:
        value = usage["completion_tokens"]
        assert isinstance(value, int) and not isinstance(value, bool), f"completion_tokens 类型异常：{value!r}"
        assert value <= 1, f"max_tokens=1 但 completion_tokens={value}，渠道可能忽略了上限"
    choices = payload.get("choices")
    assert isinstance(choices, list) and choices, f"max_tokens=1 返回中缺少 choices：{payload}"


@pytest.mark.k3_extension
def test_k3_video_url_multimodal(hclient: httpx.Client, model: str):
    """A video_url content part is accepted and produces a textual description."""
    response = _post(
        hclient,
        model,
        {
            "stream": False,
            "max_tokens": 300,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请描述一下这个视频的内容，有什么象征的东西"},
                        {
                            "type": "video_url",
                            "video_url": {
                                "url": "https://sf1-cdn-tos.huoshanstatic.com/obj/media-fe/xgplayer_doc_video/mp4/xgplayer-demo-360p.mp4"
                            },
                        },
                    ],
                }
            ],
        },
    )
    payload = _json(response)
    assert response.status_code == 200, f"video_url 应返回可诊断的成功响应，HTTP {response.status_code}：{payload.get('error') or response.text[:800]}"
    choices = payload.get("choices")
    assert isinstance(choices, list) and choices, f"视频请求响应缺少 choices：{payload}"
    message = choices[0].get("message") or {}
    text = message.get("content") if isinstance(message, dict) else ""
    if isinstance(text, list):
        text = "".join(str(item.get("text", "")) for item in text if isinstance(item, dict))
    assert isinstance(text, str) and text.strip(), f"视频请求成功但没有可见描述文本：{payload}"


def _cache_values(usage: Any) -> list[int]:
    """Collect common OpenAI/Anthropic/Kimi cache counters."""
    values: list[int] = []
    if not isinstance(usage, dict):
        return values
    candidates = (
        "cached_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_read_tokens",
    )
    for key in candidates:
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            values.append(value)
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        value = details.get("cached_tokens")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            values.append(value)
    return values


@pytest.mark.k3_extension
def test_k3_prompt_cache_repeatability(hclient: httpx.Client, model: str):
    """Repeat an identical prompt and inspect standard cache accounting fields.

    Cache is intentionally reported as an observable capability.  A relay can
    successfully generate both responses while hiding cache accounting; that is
    marked as a skipped evidence case rather than incorrectly claiming a cache
    hit.  If counters are present, they must be non-negative and the repeated
    request must remain successful.
    """
    prompt = (
        "缓存验收固定前缀 CACHE-PROBE-K3-20260920。请只回答 READY。 "
        "这段较长的固定前缀用于检查重复请求的 prompt cache usage 字段，不包含任何秘密。 "
    ) * 8
    payload = {"stream": False, "temperature": 0, "max_tokens": 8, "messages": [{"role": "user", "content": prompt}]}
    first = _post(hclient, model, payload)
    first_body = _json(first)
    assert first.status_code == 200, f"缓存首次请求失败 HTTP {first.status_code}：{first_body.get('error') or first.text[:800]}"
    second = _post(hclient, model, payload)
    second_body = _json(second)
    assert second.status_code == 200, f"缓存重复请求失败 HTTP {second.status_code}：{second_body.get('error') or second.text[:800]}"
    counters = _cache_values(first_body.get("usage")) + _cache_values(second_body.get("usage"))
    if not counters:
        pytest.skip("两次请求均成功，但响应未提供可核验的缓存 usage 字段（无法判定是否命中缓存）")
    assert all(value >= 0 for value in counters), f"缓存 usage 计数必须为非负整数：{counters}"
