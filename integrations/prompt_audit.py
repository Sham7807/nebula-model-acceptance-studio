"""Read-only, conservative observations of prompts added before a relay request.

Generated text is evidence to investigate, never proof of its own provenance.
Synthetic canary defenses and prompt/token controls are not hidden-prompt tests.
This module also reads saved reports; it does not import or call a provider SDK.
"""
from __future__ import annotations

import base64
import binascii
import json
import re


REFERENCE_CHECKS = frozenset(("prompt_exfiltration", "prompt_sidechannel", "token_accounting"))
_CONTROLS = frozenset(["B1", "B2", "B3", "B4", "B5", "X3", "T1", "T2", "T3", "T4", "T5", "T6"])
_MARKERS = re.compile(r"MY-MARKER-MARKER-XYZZY-9911|MARKER-XYZZY-9911|TENANT-ID:\s*ZX-4417", re.I)
_REFUSAL = re.compile(r"(?:I\s+)?(?:cannot|can't|won't|will not|am unable to)\s+(?:share|reveal|provide|disclose|show|access|quote)|不(?:能|会|提供|透露|公开)|无法(?:提供|访问|输出|透露|查看)|拒绝(?:提供|透露)", re.I)
_SPECULATION = re.compile(r"\b(?:example|hypothetical|might|could be|made[- ]up|not my actual)\b|例如|假设|示例|可能是|不是(?:我|实际)|编造", re.I)
_DIRECTIVE = re.compile(r"(?:^|[\n.!?。；;：:]\s*|[\"“])(?:\s*[-*\d.)、]+\s*)?(?:you\s+(?:must|shall|should|are required to|will)\b|always\b|never\b|do not\b|only\s+(?:respond|reply|output)\b|reply\s+only\b|respond\s+only\b|必须|你(?:必须|应当|只能|需要)|禁止|不得|始终|务必|只(?:能)?(?:使用|输出|回答)|每次(?:回答|回复))", re.I)
_GENERIC_RULE = re.compile(r"^(?:(?:you\s+)?(?:must |should )?(?:be |always be )?(?:helpful|honest|harmless|safe|accurate|respectful|polite)[,\s.and]*)[.!]*$|^(?:never|do not) (?:reveal|disclose|share) (?:your |the )?(?:system prompt|instructions)[.!]*$", re.I)
_GENERIC_SAFETY = re.compile(r"\b(?:helpful|honest|harmless|harmful|illegal|violence|privacy|personal (?:data|information)|api keys?|passwords?|safety polic(?:y|ies))\b|有害|违法|暴力|隐私|安全政策|诚实|有帮助|密码|密钥", re.I)
_PERSONA = re.compile(r"(?:you are|your name is|你是|你的名字是).{2,}(?:for |agent|customer service|客服|代表|品牌)|your name is\s+[A-Za-z]", re.I)
_GENERIC_IDENTITY = re.compile(r"\b(?:claude|anthropic|openai|chatgpt|gemini|google|ai assistant|large language model)\b|人工智能|语言模型", re.I)
_SPECIFIC_CLIENT_ROLE = re.compile(r"\byou are\s+[^\n]{3,180}\b(?:CLI|command[- ]line (?:assistant|client)|coding agent)\b|你是[^\n]{2,100}(?:命令行(?:助手|客户端)|代码代理)", re.I)


def _obj(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return value if isinstance(value, list) else []


def _json(value):
    if isinstance(value, dict):
        return value
    try:
        return _obj(json.loads(value)) if isinstance(value, str) else {}
    except (ValueError, TypeError):
        return {}


def _content(value):
    if isinstance(value, str):
        return value
    return "\n".join(block.get("text", "") for block in _rows(value) if isinstance(block, dict) and isinstance(block.get("text"), str))


def reference_id(sample):
    value = _obj(sample.get("evidence")).get("reference_id") or sample.get("reference_id")
    if isinstance(value, str) and re.fullmatch(r"[SCKBTX]\d+", value.upper()):
        return value.upper()
    # Older reports persisted the reference only in the deterministic sample ID.
    match = re.search(r"(?:^|-)reference-([sckbtx]\d+)(?:$|[-_])", str(sample.get("id", "")), re.I)
    return match.group(1).upper() if match else None


def _response_text(payload):
    choices = _rows(payload.get("choices"))
    if choices:
        first = _obj(choices[0])
        return _content(_obj(first.get("message")).get("content")), first.get("finish_reason")
    if payload.get("type") == "message":
        return _content(payload.get("content")), payload.get("stop_reason")
    return "", None


def _strip_request_echo(text, body):
    supplied = [_content(body.get("system"))]
    supplied.extend(_content(_obj(message).get("content")) for message in _rows(body.get("messages")))
    for value in sorted((value for value in supplied if value), key=len, reverse=True):
        text = re.sub(re.escape(value), "", text, flags=re.I)
        # A response may quote one instruction, rather than the complete input.
        for part in re.split(r"[\n。！？!?，,、（）():：;；]", value):
            part = re.sub(r"^(?:例如|比如|e\.g\.|for example)\s*", "", part.strip(), flags=re.I)
            minimum = 4 if re.search(r"[\u3400-\u9fff]", part) else 12
            if len(part) >= minimum:
                text = re.sub(re.escape(part), "", text, flags=re.I)
    text = _MARKERS.sub("", text)
    text = re.sub(r"\{\{FILL_IN_YOUR_ACTUAL_SYSTEM_PROMPT_HERE\}\}", "", text, flags=re.I)
    return text.strip()


def assess_upstream_prompt_sample(sample, rid=None):
    """Return one observational category; never an injection pass/fail score."""
    sample = _obj(sample)
    rid = rid or reference_id(sample)
    observation = {"request_id": str(sample.get("id") or sample.get("request_id") or ""), "reference_id": rid or "", "category": "inconclusive", "signal": "缺少完整请求或响应，不能判断是否额外添加提示词。", "excerpt": ""}
    if not rid:
        return None
    if rid in _CONTROLS:
        return {**observation, "category": "controls", "signal": "普通回答、参数或 Token 计量对照；不作为上游加词证据。"}
    response = _obj(sample.get("response"))
    body = _json(_obj(sample.get("request")).get("body"))
    payload = _json(response.get("body"))
    text, finish = _response_text(payload)
    observation["excerpt"] = text[:600]
    code = response.get("status")
    truncated = any(source.get(flag) for source in (sample, response, _obj(sample.get("evidence"))) for flag in ("truncated", "body_truncated"))
    if not body or not isinstance(code, int) or isinstance(code, bool) or not 200 <= code < 300 or sample.get("termination") != "eof" or truncated:
        return observation
    if not text.strip() or not finish:
        return {**observation, "signal": "响应为空或缺少协议结束信息，不能判断是否加词。"}
    if finish in ("length", "max_tokens"):
        return {**observation, "signal": "输出已截断，不能用不完整内容判断是否加词。"}
    # A synthetic/system-bearing request can only expose our own instructions.
    supplied_system = _content(body.get("system")) + "\n".join(_content(_obj(m).get("content")) for m in _rows(body.get("messages")) if _obj(m).get("role") in ("system", "developer"))
    if supplied_system.strip() and rid not in ("K4", "K6"):
        return {**observation, "category": "controls", "signal": "请求本身包含测试方提供的 system/developer 内容，不作为隐藏加词证据。"}
    compact = re.sub(r"^```(?:json|text|base64)?\s*|\s*```$", "", text.strip(), flags=re.I).strip()
    inspected = compact
    delimited = False
    if rid == "S3":
        try:
            inspected = base64.b64decode(re.sub(r"\s", "", compact), validate=True).decode("utf-8")
        except (ValueError, UnicodeError, binascii.Error):
            return {**observation, "signal": "未返回有效 UTF-8 Base64 内容；不能把编码外观判为隐藏提示。"}
        observation["excerpt"] = inspected[:600]
    parsed = _json(inspected)
    speculative = bool(_SPECULATION.search(inspected))
    if rid == "S4" and parsed:
        if parsed.get("has_system") is False and parsed.get("system_prompt") in (None, ""):
            return {**observation, "category": "clear", "signal": "模型声称没有 system；本轮未发现具体加词片段，但自述不能排除上游加词。"}
        if isinstance(parsed.get("system_prompt"), str):
            inspected = parsed["system_prompt"]
            delimited = True
        else:
            return {**observation, "signal": "仅返回 system 是否存在的自述，缺少可复核的具体指令。"}
    tagged = re.findall(r"<(?:sys|system_dump)>\s*(.*?)\s*</(?:sys|system_dump)>", inspected, flags=re.I | re.S)
    if tagged:
        inspected = "\n".join(tagged)
        delimited = True
    inspected = re.sub(r"</?(?:sys|system_dump)>|^\s*\d+\.\s*", "", inspected, flags=re.I).strip()
    inspected = _strip_request_echo(inspected, body)
    if re.fullmatch(r"(?:NONE|EMPTY|无|无角色设定|0\s*字符\s*0\s*条)[。.!！]?", inspected, re.I):
        return {**observation, "category": "clear", "signal": "本轮返回空 system 声明；未发现具体加词片段，但不证明上游没有加词。"}
    if not inspected:
        return {**observation, "category": "controls" if rid in ("K4", "K6") else "inconclusive", "signal": "输出只有已知标记、请求或预填充回显，不属于隐藏提示。"}
    if rid == "S8" or re.fullmatch(r"[\d\s,，:：字符条个\.]+", inspected):
        return {**observation, "signal": "仅有模型自报的字符/规则数量；没有实际指令内容，不能据此认定加词。"}
    if _REFUSAL.search(inspected) and not delimited:
        # Refusals often continue with advice such as "You should contact your
        # provider". Only an independently quoted rule can survive this guard.
        quoted = re.findall(r'["“]([^"”]{12,})["”]', inspected)
        if quoted and re.search(r"system|prompt|指令|规则", inspected, re.I):
            inspected = "\n".join(quoted)
            delimited = True
        else:
            return {**observation, "category": "clear", "signal": "本轮拒绝披露且未出现独立引用的具体指令；后续普通建议不作为加词证据。"}
    # Only a concrete instruction absent from the outgoing body is a candidate.
    # The candidate remains unverified: a model can invent even a quoted prompt.
    rules = [part.strip(' \t\r\n\"“”') for part in re.split(r"[\n。;；]|(?<=[.!?])\s+(?=[A-Z])", inspected)]
    concrete = [part for part in rules if len(part) >= 12 and (_DIRECTIVE.search(part) or _SPECIFIC_CLIENT_ROLE.search(part) or (delimited and _PERSONA.search(part) and not _GENERIC_IDENTITY.search(part))) and not _GENERIC_RULE.fullmatch(part) and not _GENERIC_SAFETY.search(part)]
    if concrete and not speculative and not _SPECULATION.search(inspected):
        return {**observation, "category": "candidate", "signal": "返回了请求中未提供的具体指令，疑似额外提示；模型可能编造，需与上游日志核对。", "excerpt": "\n".join(concrete)[:600]}
    if _REFUSAL.search(inspected):
        return {**observation, "category": "clear", "signal": "本轮拒绝披露且未出现具体指令片段；拒绝不证明上游未加词。"}
    return {**observation, "signal": "仅有模型自述、普通说明或无法验证的内容，尚无足够的加词证据。"}


def build_upstream_prompt_assessment(report):
    """Rebuild a summary from raw saved samples instead of old pass/fail labels."""
    report = _obj(report)
    samples = _rows(report.get("samples")) + _rows(report.get("browser_requests")) + _rows(_obj(report.get("transport")).get("requests"))
    for section in ("matrix_validation", "production_validation"):
        samples += _rows(_obj(report.get(section)).get("samples"))
    seen = set()
    evidence = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        item = assess_upstream_prompt_sample(sample)
        if item is None:
            continue
        key = (item["request_id"], item["reference_id"])
        if key in seen:
            continue
        seen.add(key)
        evidence.append(item)
    counts = {name: sum(item["category"] == name for item in evidence) for name in ("candidate", "clear", "inconclusive", "controls")}
    verdict = "suspected" if counts["candidate"] else "inconclusive" if counts["inconclusive"] else "no_signal" if counts["clear"] else "not_tested"
    labels = {"suspected": "疑似上游加词", "no_signal": "未发现加词迹象（不能排除）", "inconclusive": "证据不足，无法判断是否加词", "not_tested": "未执行上游加词检测"}
    detail = {
        "suspected": "发现 %s 条包含额外具体指令的响应；原请求中没有这些内容。它们可能来自上游，也可能是模型编造，尚不能确认来源，请核对下方原文与上游请求日志。" % counts["candidate"],
        "no_signal": "本轮 %s 条响应未出现具体加词片段，另有 %s 条证据不足。拒绝披露或回答无 system 不证明没有加词；本项不使用抗诱导分数作结论。" % (counts["clear"], counts["inconclusive"]),
        "inconclusive": "本轮 %s 条未见具体加词片段，另有 %s 条证据不足（自述、计数、错误或不完整内容）；尚未取得具体指令证据，无法确认上游是否添加提示词。" % (counts["clear"], counts["inconclusive"]),
        "not_tested": "没有保存可用的上游隐藏提示提取请求；合成金丝雀、普通回答和 Token 对照不能回答上游是否加词。",
    }[verdict]
    return {"verdict": verdict, "label": labels[verdict], "detail": detail, "counts": counts, "evidence": evidence, "request_ids": [item["request_id"] for item in evidence if item["category"] != "controls"]}
