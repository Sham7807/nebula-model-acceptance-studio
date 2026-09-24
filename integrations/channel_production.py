"""Paced channel production-readiness experiments; no network activity on import.

Every HTTP attempt, including follow-up tool rounds and transient retries, uses
one shared request/token-estimate budget.  This suite measures observed channel
behaviour, not provider identity, future SLA, billable cancellation, or tokenizer
accuracy.  The injectable transport and clock are only for isolated tests.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import secrets
import threading
import time
from collections import Counter
from datetime import timezone
from email.utils import parsedate_to_datetime

try:
    from . import ccmax_acceptance as core, claude_acceptance as claude, acceptance_matrix as matrix
except ImportError:
    import ccmax_acceptance as core
    import claude_acceptance as claude
    import acceptance_matrix as matrix

WORKLOADS = {
    "short": ("短业务请求", 1, "精确返回本任务唯一标记；检查串响应、空答与格式。"),
    "long_output": ("长输出交付", 1, "生成带顺序编号与末尾标记的长文本，验证交付完整性。"),
    "long_context": ("长上下文检索", 1, "在长文档首、中、尾放置不同标记，核对三个检索结果。"),
    "stream": ("长流式完整性", 1, "长文本流式交付；检查结束事件、有效内容首达及最大内容间隔。"),
    "thinking": ("推理业务响应", 1, "显式请求推理并核对确定性计算；推理能力是否显式返回另作证据。"),
    "vision": ("图像业务响应", 1, "上传内置已知图片，核对三个黑色方块，不依赖第三方图片 URL。"),
    "tools": ("多轮工具完整任务", 3, "两次固定 Calculator 调用和最终答案；原样回传 assistant 块及签名。"),
    "cancellation": ("客户端取消边界", 1, "有效内容到达后关闭本地响应；不推断上游停止计算或停止扣费。"),
    "custom": ("自定义业务样本", 1, "执行审核过的同模型 JSON 请求体；只做声明的确定性断言。"),
}
PRESETS = {
    "screening": {"duration_seconds": 120, "max_requests": 60, "concurrency": 2, "context_tokens": 4096, "output_tokens": 2048},
    "standard": {"duration_seconds": 1800, "max_requests": 600, "concurrency": 4, "context_tokens": 12000, "output_tokens": 4096},
    "soak": {"duration_seconds": 21600, "max_requests": 2000, "concurrency": 4, "context_tokens": 16000, "output_tokens": 4096},
}
TRANSIENT = {408, 429, 500, 502, 503, 504, 529}
MAX_RETAINED_EVIDENCE_BYTES = 64 * 1024 * 1024
MAX_RESPONSE_EVIDENCE_BYTES = 2 * 1024 * 1024
SAFE_CUSTOM_FIELDS = {"messages", "system", "max_tokens", "max_completion_tokens", "stream", "temperature", "top_p", "top_k", "stop", "stop_sequences", "thinking", "reasoning_effort", "response_format", "stream_options", "metadata", "tools", "tool_choice", "parallel_tool_calls"}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def configuration(config):
    raw = config.get("production")
    if raw is None: raw = {"profile": "off"}
    if not isinstance(raw, dict): raise ValueError("生产验收配置必须为对象")
    profile = raw.get("profile", "off")
    if profile not in ("off", "screening", "standard", "soak", "custom"):
        raise ValueError("生产验收方案无效")
    if profile == "off": return {"profile": "off", "enabled": False}
    defaults = PRESETS.get(profile, PRESETS["screening"])
    fmt = config.get("request_format", "anthropic")
    if fmt == "native" and str(config.get("suite", "")).startswith("kvv"): fmt = "openai"
    safe, _ = core._configuration({**config, "key": config.get("key") or "preview-only", "request_format": fmt, "concurrency": 1, "advanced": False})
    integer = core._integer
    result = {name: safe[name] for name in ("base", "model", "request_format", "auth", "timeout", "close_grace")}
    result.update(profile=profile, enabled=True, endpoint=claude.endpoint(safe["base"], fmt))
    for name, label, lower, upper in (
        ("duration_seconds", "持续观察秒数", 1, 86400), ("max_requests", "总请求预算", 1, 10000),
        ("concurrency", "并发上限", 1, 20), ("context_tokens", "上下文估计 Token", 1024, 100000),
        ("output_tokens", "输出 Token 预算", 256, 16384),
    ):
        result[name] = integer(raw.get(name), label, defaults[name], lower, upper)
    result["requests_per_minute"] = integer(raw.get("requests_per_minute"), "每分钟请求数", 0, 0, 600)
    result["recovery_retries"] = integer(raw.get("recovery_retries"), "暂态故障重试次数", 1, 0, 2)
    result["recovery_delay_ms"] = integer(raw.get("recovery_delay_ms"), "重试基础等待毫秒", 1000, 0, 30000)
    bound = raw.get("max_estimated_tokens")
    result["max_estimated_tokens"] = None if bound is None or bound == "" else integer(bound, "Token 估算预算", 0, 0, 10000000000)
    workloads = raw.get("workloads", [x for x in WORKLOADS if x != "custom"])
    if not isinstance(workloads, list) or not workloads or any(x not in WORKLOADS for x in workloads):
        raise ValueError("请选择有效的生产业务负载")
    result["workloads"] = list(dict.fromkeys(workloads))
    cases = raw.get("custom_cases", [])
    if not isinstance(cases, list) or len(cases) > 20: raise ValueError("自定义业务样本应为不超过 20 项的数组")
    reviewed = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or not isinstance(case.get("body"), dict): raise ValueError("自定义业务样本需要 body 对象")
        body = copy.deepcopy(case["body"])
        if "model" in body and body["model"] != result["model"]: raise ValueError("自定义样本只能使用当前模型")
        body.pop("model", None)
        if set(body) - SAFE_CUSTOM_FIELDS: raise ValueError("自定义请求体包含未支持字段（不允许覆盖 URL、鉴权或扩展请求）")
        if not isinstance(body.get("messages"), list) or not body["messages"]: raise ValueError("自定义样本需要非空 messages")
        if not isinstance(body.get("stream", False), bool): raise ValueError("自定义 stream 必须为布尔值")
        for field in ("max_tokens", "max_completion_tokens"):
            if field in body: body[field] = integer(body[field], field, result["output_tokens"], 1, 16384)
        if not any(x in body for x in ("max_tokens", "max_completion_tokens")): body["max_tokens"] = result["output_tokens"]
        encoded = json.dumps(body, ensure_ascii=False)
        if len(encoded.encode()) > 2 * 1024 * 1024: raise ValueError("单个自定义请求体不得超过 2 MiB")
        # URLs inside prompt/media content are ordinary reviewed input data.
        # The allowlist above protects the actual request endpoint and headers;
        # this runner never follows content URLs or executes requested tools.
        expectation = case.get("expect", {"exact_text": case["expected_text"]} if "expected_text" in case else {})
        if not isinstance(expectation, dict) or set(expectation) - {"exact_text", "contains"}: raise ValueError("自定义断言只支持 exact_text 或 contains")
        if "exact_text" in expectation and not isinstance(expectation["exact_text"], str): raise ValueError("exact_text 应为文本")
        contains = expectation.get("contains", [])
        if not isinstance(contains, list) or any(not isinstance(x, str) or not x for x in contains): raise ValueError("contains 应为非空文本数组")
        if not expectation.get("exact_text") and not contains: raise ValueError("自定义样本需要可验证的业务断言")
        # Store model-neutral reviewed fixtures so the same batch configuration
        # can be applied to the next selected model without stale model routing.
        reviewed.append({"id": "custom-%s" % (index + 1), "title": str(case.get("title") or case.get("name") or "业务样本 %s" % (index + 1))[:100], "body": body, "expect": copy.deepcopy(expectation)})
    if "custom" in workloads and not reviewed: raise ValueError("自定义负载需要至少一个带断言的样本")
    result["custom_cases"] = reviewed
    return result


def _estimate(body):
    """Deliberate estimate, not a model tokenizer or billing guarantee."""
    return math.ceil(len(json.dumps(body, ensure_ascii=False)) / 4) + int(body.get("max_completion_tokens", body.get("max_tokens", 0)))


def _body(settings, prompt, **extra):
    return claude._convert({"model": settings["model"], "max_tokens": settings["output_tokens"], "stream": False, "messages": [{"role": "user", "content": prompt}], **extra}, settings)


def _spec(settings, workload, task_id, variant=0):
    marker = "PRODUCTION-" + task_id.upper()
    row = {"workload": workload, "task_id": task_id, "traffic_class": "control" if workload == "cancellation" else "normal", "expect": {}, "rounds": WORKLOADS[workload][1]}
    if workload == "short":
        row.update(body=_body(settings, "Reply with exactly this text and nothing else:\n" + marker, max_tokens=256), expect={"exact_text": marker})
    elif workload in ("long_output", "stream", "cancellation"):
        count = max(16, min(512, settings["output_tokens"] // 12))
        prompt = 'Output exactly %s lines numbered 0001 through %04d. Every line must be "NNNN channel delivery verified" with NNNN replaced by its number. No commentary or markdown. After all lines add a final line %s.' % (count, count, marker)
        row.update(body=_body(settings, prompt, stream=workload != "long_output"), expect={"numbered_lines": count, "contains": [marker]})
        if workload == "cancellation": row["cancel_after_content"] = True
    elif workload == "long_context":
        prefix = matrix._prefix(settings["context_tokens"], task_id)
        parts = [prefix[:len(prefix)//2], prefix[len(prefix)//2:]]
        markers = [marker + "-HEAD", marker + "-MIDDLE", marker + "-TAIL"]
        prompt = "Read this synthetic document and return the three verification markers in head, middle, tail order.\nHEAD_MARKER=" + markers[0] + "\n" + parts[0] + "\nMIDDLE_MARKER=" + markers[1] + "\n" + parts[1] + "\nTAIL_MARKER=" + markers[2]
        row.update(body=_body(settings, prompt, max_tokens=256), expect={"contains": markers})
    elif workload == "vision":
        row.update(body=_body(settings, [{"type": "text", "text": "How many separate black squares are visible in the image? Answer only the integer."}, matrix._image("red", shapes=True)], max_tokens=256), expect={"exact_text": "3"})
    elif workload == "thinking":
        body = _body(settings, "Compute (3456 * 7891) + 12345. Return only the final integer.", max_tokens=max(2048, settings["output_tokens"]))
        if settings["request_format"] == "anthropic": body["thinking"] = {"type": "adaptive"}
        else: body["reasoning_effort"] = "medium"
        row.update(body=body, expect={"numeric_answer": "27283641", "thinking_requested": True})
    elif workload == "tools":
        tool = {"name": "Calculator", "description": "Return the result for one reviewed fixed arithmetic expression. Do not approximate.", "input_schema": {"type": "object", "properties": {"expr": {"type": "string"}}, "required": ["expr"], "additionalProperties": False}}
        row.update(body=_body(settings, "Use Calculator to compute 3456 * 7891. Do not calculate it yourself.", max_tokens=max(512, settings["output_tokens"]), tools=[tool], tool_choice={"type": "tool", "name": "Calculator"}), expect={"tool_expr": "3456*7891"})
    else:
        selected = settings["custom_cases"][variant % len(settings["custom_cases"])]
        row.update(body={**copy.deepcopy(selected["body"]), "model": settings["model"]}, expect=copy.deepcopy(selected["expect"]), custom_case_id=selected["id"])
    return row


def build_plan(config):
    settings = configuration(config)
    if not settings["enabled"]: return {"suite": "channel_production", "enabled": False, "configuration": settings, "request_count": 0, "max_requests": 0, "workloads": []}
    workloads = []
    for name in settings["workloads"]:
        spec = _spec(settings, name, "preview-" + name)
        workloads.append({"id": name, "label": WORKLOADS[name][0], "requests_per_task": WORKLOADS[name][1], "method": WORKLOADS[name][2], "expected": spec["expect"], "example": {"method": "POST", "url": settings["endpoint"], "body": spec["body"]}, "estimated_tokens_per_initial_request": _estimate(spec["body"])})
    maximum = max([_estimate(_spec(settings, name, "preview")["body"]) for name in settings["workloads"]] + [512])
    # Tool histories grow on subsequent rounds.  The actual estimate is checked
    # per attempt, including full history; this preview is intentionally labeled.
    estimate = maximum * settings["max_requests"]
    fingerprint_data = {name: copy.deepcopy(settings[name]) for name in ("workloads", "context_tokens", "output_tokens", "custom_cases")}
    for item in fingerprint_data["custom_cases"]: item["body"] = {k: v for k, v in item["body"].items() if k != "model"}
    return {"suite": "channel_production", "enabled": True, "configuration": settings, "request_format": settings["request_format"], "request_count": settings["max_requests"], "max_requests": settings["max_requests"], "duration_seconds": settings["duration_seconds"], "launch_grace_seconds": 1, "max_wall_seconds": settings["duration_seconds"] + 1 + settings["timeout"], "drain_seconds": settings["timeout"], "evidence_limit_bytes": MAX_RETAINED_EVIDENCE_BYTES, "response_evidence_limit_bytes": MAX_RESPONSE_EVIDENCE_BYTES, "estimated_tokens": estimate, "estimate_method": "请求 JSON 字符数 / 4 向上取整 + 输出预算；非 tokenizer 计数、非账单上限，工具历史与重试按实际请求另计", "workload_fingerprint": hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True, ensure_ascii=False).encode()).hexdigest(), "workloads": workloads, "prerequisite": "先发送短业务有效凭据基线，再用合成无效凭据检查拒绝；两个控制请求及重试均占预算，任一控制异常则停止批量负载。", "limits": ["只统计实际有请求活动的观察时长；预算提前耗尽不能当作完整持续验收。", "单响应证据上限 2 MiB，累计保留证据 64 MiB 后停止发起新请求（并发在途响应保留）；提前停止不算完整验收。", "重试只用于尚未交付有效内容的暂态错误，不重放外部工具。", "取消仅验证本地响应关闭；上游停止计算和停止扣费需独立日志/账单。", "不自动注入上游故障；若未观察到暂态失败，恢复能力保持未验证。", "持续时间约束新请求发起窗口；边界调度宽限最多 1 秒，进行中请求最多再等待单次 timeout，不靠空等补足观察时间。", "所有频率、时长与预算均为本次测试范围，不是长期 SLA。"]}


def _case(ident, title, status, observed, request_ids, *, workload=None, category="capability", applicable=True, expected=None):
    return {"id": ident, "title": title, "label": title, "status": status, "dimensions": ["reliability"], "module": "stress", "method": WORKLOADS.get(workload, (None, None, "汇总本轮逐请求业务证据，区分首试、重试和正常任务。"))[2], "expected": expected or "在所选负载和预算内完成正常业务请求，协议及业务断言均成立。", "observed": observed, "observed_summary": observed, "next_step": "按关联请求 ID 核对状态、结束事件、业务断言及上游日志；异常负载需修复后复测。", "request_ids": request_ids, "evidence_category": category, "score_applicable": applicable, "applicable": applicable}


def _text_check(text, expect):
    if "exact_text" in expect and text.strip() != expect["exact_text"]: return False, "可见输出不符合精确业务断言"
    if "numeric_answer" in expect and text.strip().strip("` ") != expect["numeric_answer"]: return False, "确定性计算结果不匹配"
    if any(value not in text for value in expect.get("contains", [])): return False, "业务末尾/检索标记缺失"
    if "numbered_lines" in expect:
        count = expect["numbered_lines"]
        lines = text.splitlines()
        expected = ["%04d channel delivery verified" % i for i in range(1, count + 1)]
        if lines[:count] != expected: return False, "长输出存在缺行、重复行、乱序或格式不匹配"
    return bool(text.strip()), "业务断言符合" if text.strip() else "未返回可见业务内容"


def _interpret(sample, spec, settings):
    facts = matrix._facts(sample, settings["request_format"])
    code = sample.get("response", {}).get("status")
    end = sample.get("termination")
    http_ok = isinstance(code, int) and 200 <= code < 300
    protocol = bool(http_ok and end == "eof" and facts["schema_valid"] and not facts["stream_errors"] and not facts.get("tool_errors"))
    reason = None
    if code in (401, 403): reason = "authentication"
    elif code == 429: reason = "rate_limited"
    elif code and code >= 500: reason = "upstream_5xx"
    elif code and 400 <= code < 500: reason = "parameter_or_capability"
    elif end == "timeout": reason = "timeout"
    elif end not in ("eof", "client_cancel_probe"): reason = "transport_" + str(end)
    elif not protocol: reason = "response_protocol"
    success, detail = False, "正常业务请求未取得完整有效响应"
    if protocol:
        if "tool_expr" in spec["expect"]:
            calls = facts["tools"]
            valid = len(calls) == 1 and calls[0].get("name") == "Calculator" and isinstance(calls[0].get("id"), str) and calls[0]["id"] and isinstance(calls[0].get("input"), dict)
            if valid:
                args = calls[0]["input"]
                expr = args.get("expr")
                valid = set(args) == {"expr"} and isinstance(expr, str) and "".join(expr.split()) == spec["expect"]["tool_expr"]
            success, detail = bool(valid), "固定 Calculator 调用及参数符合" if valid else "工具名称、数量、ID 或固定表达式不符合；未执行任何非预设表达式"
        else: success, detail = _text_check(facts["text"], spec["expect"])
        if not success: reason = "business_assertion"
    if spec["workload"] == "cancellation":
        success = end == "client_cancel_probe" and sample.get("evidence", {}).get("client_response_closed") is True
        detail = "有效内容到达后本地响应已关闭；未验证上游停止计算或停止扣费" if success else "未观察到可提前关闭的有效内容窗口；不推断上游取消能力"
    evidence = sample.setdefault("evidence", {})
    evidence.update(reported_usage=facts["usage"], response_model=facts["model"], business_assertion=detail, protocol_success=protocol, business_success=bool(success), failure_category=None if success else reason, output_text=facts["text"][:4000])
    if not spec["body"].get("stream") and (facts["text"] or facts["tools"]) and protocol:
        evidence["first_content_ms"] = sample.get("duration_ms")
        evidence["content_timing_source"] = "完整非流式响应可用时刻"
    if spec["expect"].get("thinking_requested"):
        payload = matrix._payload(sample)
        message = (payload.get("choices") or [{}])[0].get("message", {}) if settings["request_format"] == "openai" else {}
        content = payload.get("content") if isinstance(payload.get("content"), list) else []
        evidence["thinking_evidence_present"] = any(isinstance(block, dict) and block.get("type") in ("thinking", "redacted_thinking") for block in content) or bool(message.get("reasoning_content") or message.get("reasoning"))
        evidence["thinking_evidence_note"] = "显式推理块只记录是否观察到；业务答案正确不证明 thinking 参数被执行"
    sample.update(status="passed" if success else "inconclusive" if spec["workload"] == "cancellation" else "failed", assessments=[{"check": "production_business", "status": "passed" if success else "failed", "detail": detail}], issues=[] if success else [detail])
    return facts, bool(success), protocol


def _followup(spec, sample, step, settings):
    """Only synthetic fixed Calculator results; never evaluate model arguments."""
    body = copy.deepcopy(spec["body"])
    payload = matrix._payload(sample)
    facts = matrix._facts(sample, settings["request_format"])
    call = facts["tools"][0]
    value = "27271296" if step == 1 else "27283641"
    if settings["request_format"] == "anthropic":
        # Preserve every content block, including native thinking signatures.
        body["messages"].append({"role": "assistant", "content": copy.deepcopy(payload["content"])})
        body["messages"].append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": call["id"], "content": value}, {"type": "text", "text": "Now use Calculator to compute 27271296 + 12345." if step == 1 else "Return only the final integer from the last tool result. Do not call tools again."}]})
        if step == 2: body["tool_choice"] = {"type": "auto"}
    else:
        body["messages"].append(copy.deepcopy(payload["choices"][0]["message"]))
        body["messages"].append({"role": "tool", "tool_call_id": call["id"], "content": value})
        body["messages"].append({"role": "user", "content": "Now use Calculator to compute 27271296 + 12345." if step == 1 else "Return only the final integer from the last tool result. Do not call tools again."})
        if step == 2: body["tool_choice"] = "none"
    return {**spec, "body": body, "expect": {"tool_expr": "27271296+12345"} if step == 1 else {"numeric_answer": "27283641"}}


def _retry_after(sample, now):
    for name, value in sample.get("response", {}).get("headers", []):
        if name.lower() != "retry-after": continue
        try:
            seconds = float(value)
            return seconds if math.isfinite(seconds) and seconds >= 0 else 0
        except (TypeError, ValueError):
            try:
                stamp = parsedate_to_datetime(value)
                if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
                return max(0, stamp.timestamp() - now)
            except (TypeError, ValueError, OverflowError): return 0
    return 0


def _retryable(sample):
    if sample.get("evidence", {}).get("first_content_ms") is not None: return False
    code = sample.get("response", {}).get("status")
    return code in TRANSIENT or (code is None and sample.get("termination") in ("timeout", "network_error"))


def _percentile(values, ratio):
    valid = sorted(x for x in values if _number(x))
    return valid[max(0, math.ceil(len(valid) * ratio) - 1)] if valid else None


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _summarize(settings, plan, samples, tasks, reason, started, finished, maximum_active, estimated_tokens, control_cases):
    normal = [task for task in tasks if task["traffic_class"] == "normal"]
    normal_samples = [sample for sample in samples if sample["traffic_class"] == "normal"]
    complete = sum(task["completed"] for task in normal)
    passed = sum(task["business_success"] for task in normal)
    first = sum(task["business_success"] and task["retry_count"] == 0 for task in normal)
    protocol = sum(bool(sample.get("evidence", {}).get("protocol_success")) for sample in normal_samples)
    failures = Counter(sample.get("evidence", {}).get("failure_category") for sample in normal_samples if sample.get("status") != "passed")
    failures.pop(None, None)
    sample_start = min((x["started_at"] for x in samples), default=None)
    sample_end = max((x["finished_at"] for x in samples), default=None)
    observation = max(0, sample_end - sample_start) if sample_start is not None else 0
    observation_complete = observation >= settings["duration_seconds"]
    metrics = {"logical_tasks": len(tasks), "attempted_requests": len(samples), "normal_tasks": len(normal), "normal_attempted_requests": len(normal_samples), "business_successful": passed, "first_attempt_successful": first, "protocol_successful": protocol, "completed_tasks": complete, "completion_rate": _ratio(complete, len(normal)), "first_attempt_success_rate": _ratio(first, len(normal)), "eventual_success_rate": _ratio(passed, len(normal)), "business_success_rate": _ratio(passed, len(normal)), "protocol_success_rate": _ratio(protocol, len(normal_samples)), "success_rate_unit": "ratio", "failure_counts": dict(failures), "http_status_counts": dict(Counter(str(s.get("response", {}).get("status")) for s in normal_samples)), "duration_seconds": max(0, finished - started), "observation_seconds": observation, "observation_complete": observation_complete, "max_concurrency": maximum_active, "estimated_tokens_reserved": estimated_tokens, "retry_attempts": sum(s["attempt"] > 1 for s in samples), "tasks_retried": sum(t["retry_count"] > 0 for t in normal), "recovered_tasks": sum(t["business_success"] and t["retry_count"] > 0 for t in normal), "retry_sample_ids": [s["id"] for s in samples if s["attempt"] > 1], "billing_verification": "未对账；失败、重试和取消请求可能收费，原始 usage 均保留", "workload_fingerprint": plan["workload_fingerprint"], "time_buckets": [], "workloads": {}}
    for metric, field in (("first_content", "first_content_ms"), ("latency", "duration_ms")):
        values = [s.get("evidence", {}).get(field) if field != "duration_ms" else s.get(field) for s in normal_samples]
        for pct in (50, 95, 99): metrics[metric + "_p%s_ms" % pct] = _percentile(values, pct / 100)
    gaps = [s.get("evidence", {}).get("max_content_gap_ms") for s in normal_samples]
    metrics["max_content_gap_ms"] = max((x for x in gaps if _number(x)), default=None)
    metrics["latency_population"] = "所有正常业务 HTTP 尝试（包含失败与重试）；首有效内容仅统计实际观察到文本或工具参数的请求"
    metrics["throughput"] = {"attempted_requests_per_second": len(samples) / observation if observation else None, "successful_tasks_per_second": passed / observation if observation else None}
    cancellation_samples = [s for s in samples if s["workload"] == "cancellation"]
    metrics["cancellation"] = {"attempted": len(cancellation_samples), "client_closed": sum(s.get("termination") == "client_cancel_probe" and s.get("evidence", {}).get("client_response_closed") is True for s in cancellation_samples), "status": "observed" if any(s.get("termination") == "client_cancel_probe" for s in cancellation_samples) else "not_observed", "upstream_stopped_verified": False, "billing_stopped_verified": False, "sample_ids": [s["id"] for s in cancellation_samples]}
    metrics["recovery"] = {"retry_attempts": metrics["retry_attempts"], "tasks_retried": metrics["tasks_retried"], "recovered_tasks": metrics["recovered_tasks"], "status": "observed" if metrics["tasks_retried"] else "not_observed", "load_reduction_tested": False, "duplicate_billing_verified": False, "sample_ids": metrics["retry_sample_ids"]}
    metrics["validated_limits"] = {"max_concurrency": maximum_active, "duration_seconds": observation, "max_input_tokens": max((matrix._facts(s, settings["request_format"])["total_input_tokens"] for s in normal_samples if _number(matrix._facts(s, settings["request_format"])["total_input_tokens"])), default=None), "max_output_tokens": max((matrix._facts(s, settings["request_format"])["output_tokens"] for s in normal_samples if _number(matrix._facts(s, settings["request_format"])["output_tokens"])), default=None)}
    for pct in (50, 95, 99):
        metrics["task_latency_p%s_ms" % pct] = _percentile([(t["finished_at"] - t["started_at"]) * 1000 for t in normal], pct / 100)
    metrics["task_latency_population"] = "正常逻辑业务任务首个尝试至最后尝试结束，包含工具多轮与重试/退避时间"
    cases = list(control_cases)
    for name in list(dict.fromkeys(["short"] + settings["workloads"])):
        selected = [t for t in tasks if t["workload"] == name and not t.get("prerequisite")]
        selected_samples = [s for s in samples if s["workload"] == name and not s.get("prerequisite")]
        successful = sum(t["business_success"] for t in selected)
        metrics["workloads"][name] = {"tasks": len(selected), "normal_tasks": len(selected) if name != "cancellation" else 0, "business_successful": successful, "business_success_rate": _ratio(successful, len(selected)), "attempted_requests": len(selected_samples), "completed": sum(t["completed"] for t in selected), "protocol_successful": sum(s.get("evidence", {}).get("protocol_success", False) for s in selected_samples), "latency_p95_ms": _percentile([s.get("duration_ms") for s in selected_samples], .95)}
        status = "not_covered" if not selected else "passed" if successful == len(selected) else "inconclusive" if name == "cancellation" or any(not t["completed"] for t in selected) else "failed"
        text = "%s：完整业务成功 %s/%s 个任务，HTTP 尝试 %s 次。" % (WORKLOADS[name][0], successful, len(selected), len(selected_samples))
        if name == "cancellation": text += "只验证客户端响应关闭，不验证上游停止计费。"
        if name == "thinking":
            observed_thinking = sum(s.get("evidence", {}).get("thinking_evidence_present") is True for s in selected_samples)
            metrics["workloads"][name]["thinking_evidence_observed"] = observed_thinking
            text += "显式推理证据 %s/%s 次；答案正确本身不证明推理参数被执行。" % (observed_thinking, len(selected_samples))
        cases.append(_case("production-workload-" + name, WORKLOADS[name][0], status, text, [s["id"] for s in selected_samples], workload=name, category="control" if name == "cancellation" else "capability", applicable=name != "cancellation" and bool(selected)))
    # Buckets are populated by real task starts, never by empty waiting time.
    bucket_seconds = max(1, min(300, settings["duration_seconds"] / 6))
    buckets = {}
    for task in normal:
        index = int(max(0, task["started_at"] - started) // bucket_seconds)
        buckets.setdefault(index, []).append(task)
    for index, group in sorted(buckets.items()):
        ids = {t["id"] for t in group}; rows = [s for s in normal_samples if s["task_id"] in ids]
        lo = min(t["started_at"] for t in group); hi = max(t["finished_at"] for t in group)
        bucket_passed = sum(t["business_success"] for t in group)
        bucket_protocol = sum(s.get("evidence", {}).get("protocol_success", False) for s in rows)
        metrics["time_buckets"].append({"index": index, "started_at": lo, "finished_at": hi, "offset_seconds": max(0, lo - started), "observation_seconds": max(0, hi - lo), "tasks": len(group), "normal_tasks": len(group), "business_successful": bucket_passed, "first_attempt_successful": sum(t["business_success"] and not t["retry_count"] for t in group), "business_success_rate": _ratio(bucket_passed, len(group)), "attempted_requests": len(rows), "protocol_successful": bucket_protocol, "protocol_success_rate": _ratio(bucket_protocol, len(rows)), "failure_counts": dict(Counter(s.get("evidence", {}).get("failure_category") or "unknown" for s in rows if s["status"] != "passed"))})
    coverage_status = "passed" if observation_complete and reason not in ("cancelled", "baseline_failed", "authentication", "estimated_token_budget") else "inconclusive"
    cases.append(_case("production-observation-window", "持续观察覆盖", coverage_status, "实际请求活动跨度 %.2f 秒 / 计划 %s 秒；%s 个有真实业务任务的时段；结束原因 %s。空等时间不补足覆盖。" % (observation, settings["duration_seconds"], len(metrics["time_buckets"]), reason), [s["id"] for s in samples], category="aggregate", applicable=False))
    retries = [s for s in samples if s["attempt"] > 1]
    cases.append(_case("production-recovery", "暂态失败恢复", "not_covered" if not retries else "passed" if metrics["recovered_tasks"] == metrics["tasks_retried"] else "failed", "重试 %s 次，涉及 %s 个正常任务，恢复 %s 个。未交付有效内容才允许暂态重试；未观察到故障时不声称已验证恢复；不能证明上游不会重复扣费。" % (len(retries), metrics["tasks_retried"], metrics["recovered_tasks"]), [s["id"] for s in samples if any(s["task_id"] == r["task_id"] for r in retries)], category="aggregate", applicable=False))
    return {"suite": "channel_production", "configuration": settings, "status": "cancelled" if reason == "cancelled" else "completed" if observation_complete and reason in ("duration_reached", "request_budget") else "incomplete", "stop_reason": reason, "started_at": started, "finished_at": finished, "cases": cases, "samples": samples, "tasks": tasks, "metrics": metrics, "notes": plan["limits"]}


def run(config, emit=None, cancelled=None):
    settings = configuration(config)
    if not settings["enabled"]:
        return {"suite": "channel_production", "configuration": settings, "status": "not_covered", "cases": [], "samples": [], "tasks": [], "metrics": {"attempted_requests": 0, "normal_tasks": 0, "success_rate_unit": "ratio"}}
    key = str(config.get("key") or "").strip()
    if not key: raise ValueError("请填写 API Key")
    plan = build_plan(config)
    clock = config.get("_clock", time)
    notify = emit if callable(emit) else lambda event: None
    now = clock.monotonic
    started_mono, started_wall = now(), clock.time()
    deadline = started_mono + settings["duration_seconds"]
    launch_deadline = deadline + 1  # bounded scheduling jitter; actual span remains measured
    interval = 60 / settings["requests_per_minute"] if settings["requests_per_minute"] else settings["duration_seconds"] / max(1, settings["max_requests"] - 1)
    lock, output_lock = threading.Lock(), threading.Lock()
    state = {"attempts": 0, "estimated_tokens": 0, "evidence_bytes": 0, "next_at": started_mono, "active": 0, "maximum_active": 0, "task_index": 0, "stop_reason": None}
    samples, tasks, control_cases = [], [], []
    nonce = secrets.token_hex(4)

    def stopped():
        return core._cancelled(cancelled) or state["stop_reason"] in ("authentication", "baseline_failed")

    def wait_until(target):
        while now() < target:
            if stopped() or now() >= launch_deadline: return False
            clock.sleep(min(.05, target - now(), max(0, launch_deadline - now())))
        return not stopped() and now() < launch_deadline

    def reserve(body):
        while not stopped():
            with lock:
                if now() >= launch_deadline: state["stop_reason"] = state["stop_reason"] or "duration_reached"; return False
                if state["next_at"] > deadline + .001: state["stop_reason"] = state["stop_reason"] or "duration_reached"; return False
                if state["attempts"] >= settings["max_requests"]: state["stop_reason"] = state["stop_reason"] or "request_budget"; return False
                if state["evidence_bytes"] >= MAX_RETAINED_EVIDENCE_BYTES: state["stop_reason"] = "evidence_budget"; return False
                estimate = _estimate(body)
                if settings["max_estimated_tokens"] is not None and state["estimated_tokens"] + estimate > settings["max_estimated_tokens"]:
                    state["stop_reason"] = state["stop_reason"] or "estimated_token_budget"; return False
                delay = state["next_at"] - now()
                if delay <= 0:
                    state["next_at"] = now() + interval
                    state["attempts"] += 1
                    state["estimated_tokens"] += estimate
                    state["active"] += 1
                    state["maximum_active"] = max(state["maximum_active"], state["active"])
                    return True
            if not wait_until(now() + delay): return False
        return False

    def task(workload, ident, variant=0, prerequisite=None):
        spec = _spec(settings, workload, ident, variant)
        if prerequisite: spec["traffic_class"] = "control"
        task_samples, success, completed = [], False, False
        retry_count, rounds_completed = 0, 0
        for step in range(spec["rounds"]):
            success = False
            for attempt in range(1, settings["recovery_retries"] + 2):
                if not reserve(spec["body"]): break
                sample_id = ident + "-round-%s-attempt-%s" % (step + 1, attempt)
                notify({"type": "request_start", "suite": "channel_production", "sample_id": sample_id, "task_id": ident, "workload": workload, "round": step + 1, "attempt": attempt, "completed": len(samples), "total": settings["max_requests"], "active": state["active"], "elapsed_seconds": round(now() - started_mono, 2), "message": "生产验收 · " + WORKLOADS[workload][0]})
                call_started = clock.time()
                transport_spec = {"id": sample_id, "probe": "sse" if spec["body"].get("stream") else "production", "body": spec["body"], "cancel_after_content": spec.get("cancel_after_content", False), "max_evidence_bytes": MAX_RESPONSE_EVIDENCE_BYTES}
                try:
                    sample = core._collect_sample(transport_spec, settings, "production-invalid-" + secrets.token_hex(16) if prerequisite == "authentication" else key, config.get("transport"), stopped)
                except Exception as exc:
                    sample = {"id": sample_id, "probe": "production", "request_format": settings["request_format"], "request": {"method": "POST", "url": settings["endpoint"], "body": spec["body"]}, "response": {"status": None, "headers": [], "body": ""}, "termination": "internal_error", "evidence": {"transport_error": {"type": type(exc).__name__, "message": str(exc)}}, "duration_ms": 0}
                finally:
                    with lock: state["active"] -= 1
                sample.update(task_id=ident, workload=workload, attempt=attempt, round=step + 1, traffic_class=spec["traffic_class"], prerequisite=prerequisite, started_at=call_started, finished_at=clock.time(), module="stress", scenario_id="production-" + workload)
                sample = matrix._redact(sample, key)
                try: facts, success, protocol = _interpret(sample, spec, settings)
                except (ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
                    sample.update(status="failed", issues=["响应结构无法解析：" + type(exc).__name__], assessments=[])
                    sample.setdefault("evidence", {}).update(protocol_success=False, business_success=False, failure_category="response_protocol")
                    success, protocol = False, False
                if prerequisite == "authentication":
                    success = sample.get("response", {}).get("status") in (401, 403) and sample.get("termination") == "eof"
                    sample.update(status="passed" if success else "failed", assessments=[{"check": "production-authentication", "status": "passed" if success else "failed", "detail": "合成无效凭据被拒绝" if success else "合成无效凭据未被明确拒绝"}])
                    sample["evidence"].update(business_success=success, failure_category=None if success else "authentication_control")
                task_samples.append(sample)
                with output_lock: samples.append(sample)
                with lock: state["evidence_bytes"] += len(json.dumps(sample, ensure_ascii=False).encode())
                if attempt > 1: retry_count += 1
                notify({"type": "progress", "suite": "channel_production", "phase": "sample_complete", "sample_id": sample_id, "task_id": ident, "workload": workload, "status": sample["status"], "completed": len(samples), "total": settings["max_requests"], "active": state["active"], "logical_tasks": len(tasks), "elapsed_seconds": round(now() - started_mono, 2), "message": "%s · 第 %s 轮第 %s 次请求：%s" % (WORKLOADS[workload][0], step + 1, attempt, sample["status"])})
                if prerequisite != "authentication" and sample.get("response", {}).get("status") in (401, 403):
                    state["stop_reason"] = "authentication"
                    break
                if prerequisite == "authentication" or success or not _retryable(sample) or attempt > settings["recovery_retries"]: break
                delay = max(settings["recovery_delay_ms"] / 1000, _retry_after(sample, clock.time()))
                sample["evidence"]["retry_planned_delay_seconds"] = delay
                if now() + delay >= launch_deadline:
                    sample["evidence"]["retry_skipped"] = "Retry-After/退避超过剩余观察预算"
                    break
                if not wait_until(now() + delay): break
            if not success: break
            rounds_completed += 1
            if workload == "tools" and step < 2:
                spec = _followup(spec, sample, step + 1, settings)
        if task_samples:
            # A terminal error response is not a completed model task. A final
            # complete response with a wrong answer is completed, but not a
            # business success; partial multi-round conversations are neither.
            completed = bool(sample.get("round") == spec["rounds"] and sample.get("evidence", {}).get("protocol_success"))
            if prerequisite == "authentication": completed = bool(success)
            row = {"id": ident, "workload": workload, "prerequisite": prerequisite, "traffic_class": spec["traffic_class"], "business_success": bool(success and rounds_completed == spec["rounds"]), "completed": completed, "rounds_completed": rounds_completed, "rounds_expected": spec["rounds"], "request_ids": [s["id"] for s in task_samples], "retry_count": retry_count, "started_at": task_samples[0]["started_at"], "finished_at": task_samples[-1]["finished_at"]}
            with output_lock: tasks.append(row)
            return row
        return None

    baseline = task("short", "production-" + nonce + "-baseline", prerequisite="baseline")
    control_cases.append(_case("production-baseline", "正常业务有效凭据基线", "passed" if baseline and baseline["business_success"] else "failed" if baseline else "not_covered", "正常请求必须完整返回本次唯一标记；基线异常时停止批量负载。", baseline["request_ids"] if baseline else [], category="control", applicable=False))
    auth = task("short", "production-" + nonce + "-auth", prerequisite="authentication") if baseline and baseline["business_success"] else None
    control_cases.append(_case("production-authentication", "合成无效凭据拒绝", "passed" if auth and auth["business_success"] else "failed" if auth else "not_covered", "有效凭据基线先成立，再确认无效凭据得到 HTTP 401/403；有效与无效请求均占总预算。", (baseline["request_ids"] if baseline else []) + (auth["request_ids"] if auth else []), category="control", applicable=False))
    if not baseline or not baseline["business_success"] or not auth or not auth["business_success"]:
        state["stop_reason"] = state["stop_reason"] or "baseline_failed"
    else:
        def worker():
            while not stopped() and now() < launch_deadline:
                with lock:
                    if state["attempts"] >= settings["max_requests"] or state["stop_reason"] == "estimated_token_budget": return
                    index = state["task_index"]; state["task_index"] += 1
                workload = settings["workloads"][index % len(settings["workloads"])]
                if task(workload, "production-" + nonce + "-task-%05d" % (index + 1), index) is None: return
        workers = [threading.Thread(target=worker, name="channel-production-%s" % i, daemon=True) for i in range(settings["concurrency"])]
        for thread in workers: thread.start()
        for thread in workers:
            while thread.is_alive(): thread.join(timeout=.05)
    reason = "cancelled" if core._cancelled(cancelled) else state["stop_reason"] or ("duration_reached" if now() >= launch_deadline else "request_budget")
    result = _summarize(settings, plan, sorted(samples, key=lambda s: (s["started_at"], s["id"])), sorted(tasks, key=lambda t: t["started_at"]), reason, started_wall, clock.time(), state["maximum_active"], state["estimated_tokens"], control_cases)
    notify({"type": "progress", "suite": "channel_production", "phase": "completed", "status": result["status"], "completed": len(samples), "total": settings["max_requests"], "active": 0, "logical_tasks": len(tasks), "elapsed_seconds": result["metrics"]["duration_seconds"], "message": "生产验收结束：%s 次请求，%s 个正常业务任务，实际观察 %.2f 秒" % (len(samples), result["metrics"]["normal_tasks"], result["metrics"]["observation_seconds"])})
    return result
