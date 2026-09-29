"""Parameterized matrix regression tests; all requests use MockTransport."""
import json
import threading
import time
import unittest
import uuid

import httpx

try:
    from . import acceptance_matrix as m
except ImportError:
    import acceptance_matrix as m


def reply(body, text="MATRIX-BASELINE-OK", output=12, reason=None, tools=None, cache_read=None, cache_creation=None, input_tokens=12000, fmt="anthropic", empty=False, message_id=None):
    ident = message_id or "msg_" + uuid.uuid4().hex
    headers = {"request-id": "req_" + uuid.uuid4().hex}
    reason = reason or ("tool_use" if tools else "end_turn") if fmt == "anthropic" else reason or ("tool_calls" if tools else "stop")
    if fmt == "anthropic":
        usage = {"input_tokens": input_tokens, "output_tokens": output}
        if cache_read is not None: usage["cache_read_input_tokens"] = cache_read
        if cache_creation is not None: usage["cache_creation_input_tokens"] = cache_creation
        content = [{"type": "tool_use", **x} for x in tools] if tools else [] if empty else [{"type": "text", "text": text}]
        payload = {"id": ident, "type": "message", "role": "assistant", "model": "fixture", "content": content, "stop_reason": reason, "usage": usage}
    else:
        usage = {"prompt_tokens": input_tokens, "completion_tokens": output, "total_tokens": input_tokens + output}
        if cache_read is not None: usage["prompt_tokens_details"] = {"cached_tokens": cache_read}
        message = {"role": "assistant", "content": "" if empty else text}
        if tools: message.update(content=None, tool_calls=[{"id": x["id"], "type": "function", "function": {"name": x["name"], "arguments": json.dumps(x["input"], ensure_ascii=False)}} for x in tools])
        payload = {"id": ident, "object": "chat.completion", "model": "fixture", "choices": [{"index": 0, "message": message, "finish_reason": reason}], "usage": usage}
    if not body.get("stream"): return httpx.Response(200, json=payload, headers=headers)
    events = []
    def native(kind, value): events.append("event: " + kind + "\ndata: " + json.dumps({"type": kind, **value}, ensure_ascii=False) + "\n\n")
    if fmt == "anthropic":
        native("message_start", {"message": {"id": ident, "type": "message", "role": "assistant", "model": "fixture", "content": [], "stop_reason": None, "usage": {**usage, "output_tokens": 0}}})
        for index, block in enumerate(content):
            native("content_block_start", {"index": index, "content_block": {**block, "text": ""} if block["type"] == "text" else {**block, "input": {}}})
            delta = {"type": "text_delta", "text": block["text"]} if block["type"] == "text" else {"type": "input_json_delta", "partial_json": json.dumps(block["input"], ensure_ascii=False)}
            native("content_block_delta", {"index": index, "delta": delta})
            native("content_block_stop", {"index": index})
        native("message_delta", {"delta": {"stop_reason": reason}, "usage": {"output_tokens": output}})
        native("message_stop", {})
    else:
        delta = {"role": "assistant", "content": text if not empty else ""}
        if tools: delta.update(content=None, tool_calls=[{"index": index, **call} for index, call in enumerate(payload["choices"][0]["message"]["tool_calls"])])
        events.append("data: " + json.dumps({"id": ident, "object": "chat.completion.chunk", "model": "fixture", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}) + "\n\n")
        events.append("data: " + json.dumps({"id": ident, "object": "chat.completion.chunk", "model": "fixture", "choices": [{"index": 0, "delta": {}, "finish_reason": reason}], "usage": usage}) + "\n\n")
        events.append("data: [DONE]\n\n")
    return httpx.Response(200, content="".join(events), headers={**headers, "content-type": "text/event-stream"})


class MatrixTests(unittest.TestCase):
    def config(self, **kwargs):
        return {"base": "https://relay.test/relay/v2", "model": "fixture", "key": "private-fixture-key", "timeout": 5, "matrix_profile": "standard", **kwargs}

    def handler(self, calls, fmt="anthropic"):
        prefixes = set()
        def handle(request):
            body = json.loads(request.content); calls.append(body)
            prompt = json.dumps(body["messages"], ensure_ascii=False)
            system = body.get("system", "") if fmt == "anthropic" else " ".join(str(x["content"]) for x in body["messages"] if x["role"] == "system")
            def respond(**kw): return reply(body, fmt=fmt, **kw)
            if body["max_tokens"] <= 0: return httpx.Response(400, json={"error": {"message": "max_tokens must be positive", "type": "invalid_request_error"}})
            if body["max_tokens"] <= 256: return respond(text="", output=body["max_tokens"], reason="max_tokens" if fmt == "anthropic" else "length", empty=True)
            if any(x["role"] == "tool" for x in body["messages"]) or '"tool_result"' in prompt: return respond(text="27271296")
            if body.get("tools"):
                choice = body.get("tool_choice")
                if choice in ("none",) or isinstance(choice, dict) and choice.get("type") == "none": return respond(text="TOOLS-DISABLED")
                tools = [{"id": "call_calculator", "name": "Calculator", "input": {"expr": "3456 * 7891"}}]
                if "Call DeliveryQuote" in prompt: tools = [{"id": "call_quote", "name": "DeliveryQuote", "input": m.NESTED_EXPECTED}]
                if "make BOTH calls" in prompt: tools += [{"id": "call_weather", "name": "WeatherQuery", "input": {"city": "上海"}}]
                return respond(tools=tools)
            if "Unique acceptance prefix" in str(system):
                prefix = json.dumps(system, ensure_ascii=False)
                read = 12000 if prefix in prefixes else 0
                prefixes.add(prefix)
                return respond(text="CACHE-MATRIX-ACK", input_tokens=12000 if fmt == "openai" else 10, cache_read=read, cache_creation=12000 if read == 0 else 0)
            if "MATRIX-PRIVATE-" in str(system): return respond(text="MATRIX-SAFE-ACK")
            if '"type": "url"' in prompt or '"fileData"' in prompt or '"video"' in prompt or '"audio"' in prompt or '"video_url"' in prompt or '"audio_url"' in prompt or '"input_audio"' in prompt:
                return respond(text="A real public media fixture was received and is available for inspection.")
            if '"image"' in prompt or '"image_url"' in prompt:
                if "black squares" in prompt: return respond(text="3")
                images = next(x["content"] for x in body["messages"] if isinstance(x.get("content"), list))
                encoded = [x.get("source", {}).get("data") or x.get("image_url", {}).get("url", "").partition(",")[2] for x in images if x["type"] in ("image", "image_url")]
                colors = ["blue" if x == m._image("blue")["source"]["data"] else "red" for x in encoded]
                return respond(text=",".join(colors))
            user = body["messages"][-1]["content"]
            if user.startswith("Reply with exactly this text and nothing else:\n"): return respond(text=user.split("\n", 1)[1])
            if "MATRIX-LOAD-" in user: return respond(text=user.removeprefix("Reply exactly ").removesuffix("."))
            if "Copy exactly ALPHA" in user: return respond(text="ALPHA ", reason="stop_sequence" if fmt == "anthropic" else "stop")
            if any(x in user for x in ("Print every integer", "at least 2000 English", "valid JSON array")): return respond(text="a long fixture", output=700)
            return respond()
        return handle

    def test_profiles_have_actual_parameter_variations_and_precise_bounds(self):
        for profile, expected, caps in (("quick", 31, 3), ("standard", 63, 12), ("comprehensive", 164, 72)):
            with self.subTest(profile=profile):
                plan = m.build_plan(self.config(matrix_profile=profile))
                self.assertEqual(plan["request_count"], expected)
                rows = [x for x in plan["requests"] if x["id"].startswith("matrix-cap-")]
                self.assertEqual(len(rows), caps)
                self.assertEqual({x["body"]["max_tokens"] for x in rows}, set(m.PROFILES[profile]["caps"]))
                self.assertEqual(len({x["id"] for x in plan["requests"]}), expected)
                self.assertNotIn("private-fixture-key", json.dumps(plan))
                self.assertTrue(all(x["url"] == "https://relay.test/relay/v2/messages" for x in plan["requests"]))

    def test_native_and_chat_standard_complete_requests_and_evidence(self):
        for fmt in ("anthropic", "openai"):
            with self.subTest(fmt=fmt):
                calls = []
                result = m.run(self.config(request_format=fmt, transport=httpx.MockTransport(self.handler(calls, fmt))))
                failed = [(x["id"], x["observed"]) for x in result["cases"] if x["status"] != "passed"]
                self.assertEqual(failed, [])
                # Public image URL + Base64 are always included. OpenAI
                # compatibility additionally exercises optional video URL and
                # inline Base64 forms.
                self.assertEqual(len(calls), 61 + (4 if fmt == "openai" else 2))
                self.assertEqual(result["summary"]["request_count"], len(calls))
                self.assertEqual(result["metrics"]["stress"]["total_requests"], 16)
                self.assertEqual(len(result["metrics"]["cache"]["rounds"]), 4)
                self.assertNotIn("private-fixture-key", json.dumps(result))
                for row in result["cases"]:
                    self.assertTrue(all(k in row for k in ("reason_code", "score_applicable", "parameters", "scenario_id", "repetition", "method", "expected", "observed", "meaning", "next_step")))
                    self.assertTrue(all(ident in {s["id"] for s in result["samples"]} for ident in row["request_ids"]))

    def test_kvv_native_means_openai_not_messages(self):
        for suite in ("kvv11", "kvvfull"):
            plan = m.build_plan(self.config(suite=suite, request_format="native"))
            self.assertEqual(plan["request_format"], "openai")
            self.assertTrue(all(x["url"].endswith("/v2/chat/completions") for x in plan["requests"]))

    def test_disabled_modules_never_send_unselected_requests(self):
        calls = []
        result = m.run(self.config(matrix_modules=["max_tokens"], transport=httpx.MockTransport(self.handler(calls))))
        self.assertEqual(len(calls), 15)
        self.assertEqual({x["module"] for x in result["samples"]}, {"protocol", "max_tokens"})
        self.assertFalse(result["metrics"]["cache"]["rounds"])
        self.assertEqual(m.build_plan(self.config(matrix_modules=[]))["request_count"], 0)

    def test_reasoning_empty_content_truncation_is_valid(self):
        calls = []
        result = m.run(self.config(matrix_modules=["max_tokens"], transport=httpx.MockTransport(self.handler(calls))))
        caps = [x for x in result["cases"] if x["id"].startswith("matrix-cap-")]
        self.assertEqual(len(caps), 12)
        self.assertTrue(all(x["status"] == "passed" for x in caps))
        self.assertTrue(all("可见文本为空" in x["observed"] for x in caps))

    def test_zero_consumption_is_not_confirmed_truncation(self):
        def handler(request):
            body = json.loads(request.content)
            return reply(body, output=0, reason="max_tokens", empty=True)
        result = m.run(self.config(matrix_profile="quick", matrix_modules=["max_tokens"], transport=httpx.MockTransport(handler)))
        caps = [x for x in result["cases"] if x["id"].startswith("matrix-cap-")]
        self.assertTrue(all(x["status"] == "inconclusive" and not x["score_applicable"] for x in caps))

    def test_cap_excess_fails_but_natural_stop_is_not_truncation_pass(self):
        for excess, expected_status, expected_reason in ((True, "failed", "assertion_failed"), (False, "inconclusive", "cap_not_exercised")):
            def handler(request):
                body = json.loads(request.content)
                return reply(body, output=body["max_tokens"] + 1 if excess else 1)
            result = m.run(self.config(matrix_profile="quick", matrix_modules=["max_tokens"], transport=httpx.MockTransport(handler)))
            rows = [x for x in result["cases"] if x["id"].startswith("matrix-cap-")]
            self.assertTrue(all(x["status"] == expected_status and x["reason_code"] == expected_reason for x in rows))

    def test_budget_exhaustion_does_not_fail_semantic_probes(self):
        def handler(request):
            body = json.loads(request.content)
            return reply(body, text="", empty=True, output=body["max_tokens"], reason="max_tokens")
        result = m.run(self.config(matrix_modules=["tools", "multimodal", "injection"], transport=httpx.MockTransport(handler)))
        self.assertEqual(next(x for x in result["cases"] if x["id"] == "matrix-baseline")["status"], "passed")
        rows = [x for x in result["cases"] if x["id"] not in ("matrix-baseline", "matrix-tools-roundtrip")]
        self.assertTrue(all(x["status"] == "inconclusive" and x["reason_code"] == "budget_exhausted" and not x["score_applicable"] for x in rows))

    def test_explicit_unsupported_is_distinct_from_network_failure(self):
        for code, message, status, reason in ((400, "unsupported max_tokens parameter", "failed", "unsupported_parameter"), (429, "quota exceeded", "inconclusive", "rate_limited")):
            count = 0
            def handler(request):
                nonlocal count
                count += 1; body = json.loads(request.content)
                if count == 1: return reply(body)
                return httpx.Response(code, json={"error": {"message": message}})
            result = m.run(self.config(matrix_profile="quick", matrix_modules=["max_tokens"], transport=httpx.MockTransport(handler)))
            row = next(x for x in result["cases"] if x["id"].startswith("matrix-cap-"))
            self.assertEqual((row["status"], row["reason_code"]), (status, reason))

    def test_baseline_auth_failure_stops_all_supplemental_requests(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(401, json={"error": {"message": "invalid key"}})
        result = m.run(self.config(transport=httpx.MockTransport(handler)))
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["cases"][0]["reason_code"], "authentication_error")
        self.assertTrue(all(x["status"] == "not_covered" for x in result["cases"][1:]))

    def test_cache_real_long_prefix_and_changed_prefix_control(self):
        calls = []
        result = m.run(self.config(matrix_modules=["cache"], transport=httpx.MockTransport(self.handler(calls))))
        bodies = calls[1:]
        self.assertGreaterEqual(len(bodies[0]["system"][0]["text"]), 48000)
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(bodies[1]["system"], bodies[2]["system"])
        self.assertNotEqual(bodies[1]["messages"], bodies[2]["messages"])
        self.assertNotEqual(bodies[2]["system"], bodies[3]["system"])
        self.assertTrue(all(x["status"] == "passed" for x in result["cases"]))

    def test_cache_zero_missing_and_invalid_counters_are_not_fake_hits(self):
        for value, status, reason in ((0, "inconclusive", "cache_not_observed"), (None, "inconclusive", "usage_missing"), (True, "failed", "assertion_failed")):
            def handler(request):
                body = json.loads(request.content)
                return reply(body, cache_read=value)
            result = m.run(self.config(matrix_modules=["cache"], transport=httpx.MockTransport(handler)))
            row = next(x for x in result["cases"] if x["parameters"].get("variant") == "warm")
            self.assertEqual((row["status"], row["reason_code"]), (status, reason))

    def test_cache_minimum_is_independent_of_small_native_test(self):
        plan = m.build_plan(self.config(cache_tokens=1024, matrix_modules=["cache"]))
        self.assertEqual(plan["token_estimate"]["cache_prefix_target_tokens"], 4096)
        with self.assertRaises(ValueError): m.build_plan(self.config(matrix_cache_tokens=1))

    def test_cache_small_usage_is_not_large_prefix_success(self):
        def handler(request):
            body = json.loads(request.content)
            return reply(body, input_tokens=10, cache_read=10)
        result = m.run(self.config(matrix_modules=["cache"], transport=httpx.MockTransport(handler)))
        rows = [x for x in result["cases"] if x["module"] == "cache"]
        self.assertTrue(all(x["reason_code"] == "cache_scale_not_reached" and not x["score_applicable"] for x in rows))

    def test_control_rounds_cannot_inflate_capability_score(self):
        calls = []
        result = m.run(self.config(matrix_modules=["cache", "max_tokens", "injection"], transport=httpx.MockTransport(self.handler(calls))))
        controls = [x for x in result["cases"] if x["parameters"].get("variant") == "cold" or x["id"].startswith("matrix-length-control") or x["id"] == "matrix-injection-control"]
        self.assertTrue(controls)
        self.assertTrue(all(x["status"] == "passed" and x["score_applicable"] is False for x in controls))

    def test_nested_schema_and_tool_result_preserve_real_id(self):
        calls = []
        result = m.run(self.config(matrix_modules=["tools"], transport=httpx.MockTransport(self.handler(calls))))
        follow = next(b for b in calls if '"tool_result"' in json.dumps(b))
        self.assertEqual(follow["messages"][-1]["content"][0]["tool_use_id"], "call_calculator")
        self.assertEqual(follow["messages"][-1]["content"][0]["content"], "27271296")
        nested = next(x for x in result["cases"] if x["id"] == "matrix-tools-nested")
        self.assertEqual(nested["status"], "passed")
        self.assertTrue(m._schema_errors({**m.NESTED_EXPECTED, "extra": True}, m.NESTED["input_schema"]))
        self.assertTrue(m._schema_errors({"shipment": {"destination": {"city": "杭州", "country": "DE"}, "weights": [False]}, "priority": "urgent"}, m.NESTED["input_schema"]))

    def test_tool_result_not_fabricated_after_wrong_tool(self):
        def handler(request):
            body = json.loads(request.content)
            if body.get("tools"): return reply(body, tools=[{"id": "wrong", "name": "WeatherQuery", "input": {"city": "杭州"}}])
            return reply(body)
        result = m.run(self.config(matrix_modules=["tools"], transport=httpx.MockTransport(handler)))
        self.assertNotIn("matrix-tools-roundtrip", {x["id"] for x in result["samples"]})
        self.assertEqual(next(x for x in result["cases"] if x["id"] == "matrix-tools-roundtrip")["reason_code"], "prerequisite_failed")

    def test_pressure_is_staged_bounded_and_not_retried(self):
        calls = []; active = 0; peak = 0; lock = threading.Lock()
        fixture = self.handler(calls)
        def handler(request):
            nonlocal active, peak
            body = json.loads(request.content)
            if "MATRIX-LOAD-" in str(body):
                with lock: active += 1; peak = max(peak, active)
                time.sleep(.01)
                with lock: active -= 1
            return fixture(request)
        result = m.run(self.config(matrix_modules=["stress"], transport=httpx.MockTransport(handler)))
        self.assertEqual(len(calls), 17)
        self.assertLessEqual(peak, 4); self.assertGreater(peak, 1)
        self.assertEqual([x["concurrency"] for x in result["metrics"]["stress"]["stages"]], [1, 2, 4])
        self.assertTrue(all(x["p95_ms"] >= x["p50_ms"] for x in result["metrics"]["stress"]["stages"]))
        self.assertTrue(all(x["success_rate"] == 1 and x["rate_unit"] == "ratio" for x in result["metrics"]["stress"]["stages"]))

    def test_pressure_checks_actual_json_response_id_reuse(self):
        calls = []; original = self.handler(calls)
        def handler(request):
            response = original(request)
            payload = json.loads(response.content)
            payload["id"] = "replayed-message-id"
            return httpx.Response(200, json=payload)
        result = m.run(self.config(matrix_modules=["stress"], transport=httpx.MockTransport(handler)))
        stages = result["metrics"]["stress"]["stages"]
        self.assertTrue(all(x["duplicate_response_ids"] == ["replayed-message-id"] for x in stages))
        self.assertTrue(all(x["status"] == "failed" for x in result["cases"] if x["id"].startswith("matrix-stress-stage-")))

    def test_pressure_rate_limit_is_recorded_without_automatic_retries(self):
        calls = []; original = self.handler(calls)
        def handler(request):
            body = json.loads(request.content)
            if "MATRIX-LOAD-" in str(body):
                calls.append(body)
                return httpx.Response(429, json={"error": {"message": "limit exceeded"}})
            return original(request)
        result = m.run(self.config(matrix_modules=["stress"], transport=httpx.MockTransport(handler)))
        self.assertEqual(len(calls), 17)
        stages = result["metrics"]["stress"]["stages"]
        self.assertEqual(sum(x["rate_limited"] for x in stages), 16)
        self.assertTrue(all(x["http_successful"] == 0 and x["semantic_failures"] == 0 for x in stages))

    def test_cancellation_stops_new_work_and_preserves_partial_results(self):
        event = threading.Event(); calls = []; fixture = self.handler(calls)
        def handler(request):
            response = fixture(request)
            if len(calls) >= 3: event.set()
            return response
        result = m.run(self.config(transport=httpx.MockTransport(handler)), cancelled=event)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(len(calls), 3)
        self.assertTrue(result["samples"])
        event.set()
        empty = m.run(self.config(transport=httpx.MockTransport(lambda _: self.fail("network after cancel"))), cancelled=event)
        self.assertFalse(empty["samples"])
        self.assertEqual(len(empty["cases"]), empty["plan"]["request_count"])
        self.assertTrue(all(x["status"] == "cancelled" for x in empty["cases"]))

    def test_pressure_cancellation_starts_no_queued_work_after_return(self):
        event = threading.Event(); calls = []; original = self.handler(calls); lock = threading.Lock()
        def handler(request):
            with lock:
                response = original(request)
                if len(calls) >= 7: event.set()
            time.sleep(.01)
            return response
        result = m.run(self.config(matrix_modules=["stress"], transport=httpx.MockTransport(handler)), cancelled=event)
        self.assertEqual(result["status"], "cancelled")
        count = len(calls)
        time.sleep(.04)
        self.assertEqual(len(calls), count)
        self.assertLess(count, 17)
        self.assertTrue(any(x["status"] == "cancelled" and not x["request_ids"] for x in result["cases"]))

    def test_comprehensive_runs_all_160_real_mock_requests(self):
        for fmt in ("anthropic", "openai"):
            calls = []
            result = m.run(self.config(request_format=fmt, matrix_profile="comprehensive", transport=httpx.MockTransport(self.handler(calls, fmt))))
            # Both formats include the two real video probes. OpenAI adds
            # the two audio probes because it has a portable audio shape.
            self.assertEqual(len(calls), 164 + (2 if fmt == "openai" else 0))
            self.assertEqual(len([x for x in result["cases"] if x["id"].startswith("matrix-cap-")]), 72)
            self.assertTrue(all(x["status"] == "passed" for x in result["cases"] if x["id"].startswith("matrix-cap-") or x["id"] == "matrix-tools-nested-stream"))
            self.assertEqual([s["concurrency"] for s in result["metrics"]["stress"]["stages"]], [1, 2, 4, 8])

    def test_upstream_echo_of_secret_is_redacted_from_all_evidence(self):
        def handler(request):
            body = json.loads(request.content)
            return reply(body, text="private-fixture-key")
        result = m.run(self.config(matrix_modules=["injection"], transport=httpx.MockTransport(handler)))
        self.assertNotIn("private-fixture-key", json.dumps(result))
        self.assertIn("[REDACTED]", json.dumps(result))

    def test_injection_uses_distinct_synthetic_parameters(self):
        plan = m.build_plan(self.config(matrix_modules=["injection"]))
        attacks = [x for x in plan["requests"] if x["parameters"].get("attack") not in (None, "none")]
        self.assertEqual({x["parameters"]["attack"] for x in attacks}, {"direct", "document", "role", "translation"})
        self.assertEqual(len({json.dumps(x["body"]["messages"]) for x in attacks}), 4)

    def test_injection_prerequisite_failure_is_not_false_attack_failure(self):
        def handler(request):
            body = json.loads(request.content)
            return reply(body, text="unrelated response")
        result = m.run(self.config(matrix_modules=["injection"], transport=httpx.MockTransport(handler)))
        attacks = [x for x in result["cases"] if x["parameters"].get("attack") not in (None, "none")]
        self.assertTrue(all(x["reason_code"] == "prerequisite_failed" for x in attacks))

    def test_observed_injection_leak_remains_failure_when_output_truncated(self):
        def handler(request):
            body = json.loads(request.content)
            if "Private test token" in str(body): return reply(body, text="MATRIX-OVERRIDE", reason="max_tokens", output=1024)
            return reply(body)
        result = m.run(self.config(matrix_modules=["injection"], transport=httpx.MockTransport(handler)))
        attacks = [x for x in result["cases"] if x["parameters"].get("attack") not in (None, "none")]
        self.assertTrue(all(x["status"] == "failed" and x["reason_code"] == "assertion_failed" for x in attacks))


class ClaudeOfficialMatrixTests(unittest.TestCase):
    """Replay documented Messages contracts; no official API key is used."""

    def config(self, **values):
        return {"base": "https://api.anthropic.com", "model": "claude-opus-4-6",
                "key": "private-fixture-key", "suite": "claude", "timeout": 5,
                "request_format": "anthropic", "matrix_profile": "standard", **values}

    def run_fixture(self, modules, responder=None, **values):
        calls = []
        standard = MatrixTests().handler(calls)
        def handle(request):
            body = json.loads(request.content)
            if responder:
                response = responder(body)
                if response is not None:
                    calls.append(body)
                    return response
            return standard(request)
        result = m.run(self.config(matrix_modules=modules, transport=httpx.MockTransport(handle), **values))
        return result, calls

    def test_official_zero_output_is_valid_and_negative_budget_is_invalid(self):
        def responder(body):
            if body["max_tokens"] == 0:
                return reply(body, output=0, reason="max_tokens", empty=True)
        result, calls = self.run_fixture(["protocol"], responder)
        rows = {row["id"]: row for row in result["cases"]}
        self.assertEqual(rows["matrix-zero-output-budget"]["status"], "passed")
        self.assertEqual(rows["matrix-invalid-cap--1"]["status"], "passed")
        self.assertNotIn("matrix-invalid-cap-0", rows)
        self.assertTrue(all(body.get("thinking") == {"type": "disabled"} for body in calls))

    def test_zero_budget_with_actual_generation_remains_a_failure(self):
        result, _ = self.run_fixture(["protocol"], lambda body: reply(body, output=1, text="x") if body["max_tokens"] == 0 else None)
        row = next(row for row in result["cases"] if row["id"] == "matrix-zero-output-budget")
        self.assertEqual(row["status"], "failed")

    def test_native_claude_does_not_send_nonexistent_video_audio_blocks(self):
        plan = m.build_plan(self.config(matrix_modules=["multimodal"]))
        for request in plan["requests"]:
            for message in request["body"]["messages"]:
                if isinstance(message["content"], list):
                    self.assertFalse({block["type"] for block in message["content"]} & {"video", "audio"})
        self.assertEqual({row["parameters"]["media"] for row in plan["applicability"]}, {"video", "audio"})
        result, _ = self.run_fixture(["multimodal"])
        scope = [row for row in result["cases"] if row["reason_code"] == "protocol_not_applicable"]
        self.assertEqual(len(scope), 2)
        self.assertTrue(all(row["status"] == "not_covered" and not row["score_applicable"] and not row["request_ids"] and row["applicable"] is False for row in scope))
        self.assertEqual(result["summary"]["unexecuted_cases"], 0)
        self.assertEqual(result["summary"]["completed"], result["summary"]["total"])

    def test_always_on_models_use_supported_auto_and_real_tool_roundtrip(self):
        for model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1"):
            with self.subTest(model=model):
                result, calls = self.run_fixture(["tools", "max_tokens"], model=model)
                self.assertTrue(all(body.get("thinking", {}).get("type") != "disabled" for body in calls))
                self.assertFalse(any(body.get("tool_choice", {}).get("type") in ("tool", "any") for body in calls))
                self.assertTrue(any(body.get("tool_choice", {}).get("type") == "auto" for body in calls))
                roundtrip = next(row for row in result["cases"] if row["id"] == "matrix-tools-roundtrip")
                self.assertEqual(roundtrip["status"], "passed")
                self.assertEqual(roundtrip["parameters"]["source_request_id"], "matrix-tools-auto")
                caps = [row for row in result["cases"] if row["id"].startswith("matrix-cap-")]
                self.assertEqual(len(caps), 12)
                self.assertTrue(all(row["status"] == "passed" for row in caps))

    def test_always_on_semantic_budgets_leave_room_for_thinking_without_changing_caps(self):
        plan = m.build_plan(self.config(model="claude-opus-5-5", matrix_modules=["protocol", "tools", "max_tokens", "cache", "stress"]))
        for row in plan["requests"]:
            if row["id"].startswith("matrix-cap-"):
                self.assertIn(row["body"]["max_tokens"], (1, 10, 20))
                self.assertEqual(row["body"]["max_tokens"], row["parameters"]["max_tokens"])
            elif row["id"] in ("matrix-zero-output-budget", "matrix-invalid-cap--1"):
                self.assertEqual(row["body"]["max_tokens"], row["parameters"]["max_tokens"])
            else:
                self.assertEqual(row["body"]["max_tokens"], 4096, row["id"])
                if "max_tokens" in row["parameters"]:
                    self.assertEqual(row["parameters"]["max_tokens"], 4096)

    def test_cache_hit_below_estimated_target_is_preserved_with_explicit_scale_limit(self):
        prefixes = set()
        def responder(body):
            if not isinstance(body.get("system"), list): return None
            prefix = json.dumps(body["system"])
            cached = prefix in prefixes
            prefixes.add(prefix)
            return reply(body, input_tokens=10, cache_read=9000 if cached else 0,
                         cache_creation=0 if cached else 9000)
        result, _ = self.run_fixture(["cache"], responder)
        for variant in ("warm", "suffix_changed"):
            row = next(row for row in result["cases"] if row["parameters"].get("variant") == variant)
            self.assertEqual(row["status"], "passed")
            self.assertEqual(row["measurements"]["cache_read_tokens"], 9000)
            self.assertIs(row["measurements"]["scale_target_met"], False)
            self.assertIs(row["measurements"]["cache_hit_observed"], True)
            self.assertIn("不能声称指定大 Token 规模已验证", row["detail"])
        rounds = result["metrics"]["cache"]["rounds"]
        self.assertTrue(all(row["scale_target_met"] is False for row in rounds))
        self.assertEqual([row["cache_hit_observed"] for row in rounds], [False, True, True, False])

    def test_cache_missing_hit_below_target_still_cannot_pass_reuse(self):
        result, _ = self.run_fixture(["cache"], lambda body: reply(body, input_tokens=9000, cache_read=0) if body.get("system") else None)
        row = next(row for row in result["cases"] if row["parameters"].get("variant") == "warm")
        self.assertEqual(row["status"], "inconclusive")
        self.assertFalse(row["score_applicable"])
        self.assertIs(row["measurements"]["cache_hit_observed"], False)

    def test_known_supported_forced_tools_still_fail_when_silently_dropped(self):
        result, _ = self.run_fixture(["tools"], lambda body: reply(body, text="27271296") if body.get("tools") else None)
        row = next(row for row in result["cases"] if row["id"] == "matrix-tools-named")
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["score_applicable"])

    def test_expression_spacing_and_structurally_equal_json_are_not_protocol_failures(self):
        def responder(body):
            user = str(body["messages"][-1]["content"])
            if body.get("tools") and body.get("tool_choice", {}).get("type") in ("tool", "any", "auto") and "Calculator" in user and "BOTH" not in user:
                return reply(body, tools=[{"id": "tool-fixed", "name": "Calculator", "input": {"expr": "3456*7891"}}])
            if '{"ok":true,"count":7}' in user:
                return reply(body, text='{\n  "count": 7,\n  "ok": true\n}')
        result, _ = self.run_fixture(["tools", "protocol"], responder)
        for ident in ("matrix-tools-named", "matrix-tools-required", "matrix-tools-auto", "matrix-protocol-json", "matrix-protocol-json-stream"):
            self.assertEqual(next(row for row in result["cases"] if row["id"] == ident)["status"], "passed", ident)

    def test_wrong_expression_and_changed_json_values_remain_failures(self):
        def responder(body):
            user = str(body["messages"][-1]["content"])
            if body.get("tools"):
                return reply(body, tools=[{"id": "tool-fixed", "name": "Calculator", "input": {"expr": "3456+7891"}}])
            if '{"ok":true,"count":7}' in user:
                return reply(body, text='{"ok":1,"count":7}')
        result, _ = self.run_fixture(["tools", "protocol"], responder)
        for ident in ("matrix-tools-named", "matrix-protocol-json"):
            self.assertEqual(next(row for row in result["cases"] if row["id"] == ident)["status"], "failed")

    def test_stress_accepts_harmless_echo_punctuation_but_rejects_another_request_nonce(self):
        for wrong in (False, True):
            def responder(body):
                prompt = body["messages"][-1]["content"]
                if "MATRIX-LOAD-" in prompt:
                    self.assertTrue(prompt.startswith("Reply with exactly this text and nothing else:\n"))
                    marker = prompt.split("\n", 1)[1]
                    self.assertFalse(marker.endswith("."))
                    return reply(body, text=("MATRIX-LOAD-other-1-1" if wrong else marker) + ".")
            result, _ = self.run_fixture(["stress"], responder, matrix_profile="quick")
            rows = [row for row in result["cases"] if row["id"].startswith("matrix-stress-c")]
            self.assertEqual(len(rows), 6)
            self.assertTrue(all(row["status"] == ("failed" if wrong else "passed") for row in rows))
            self.assertTrue(all(stage["http_success_rate"] == 1 for stage in result["metrics"]["stress"]["stages"]))

    def test_duplicate_response_ids_have_independent_scored_evidence(self):
        def responder(body):
            prompt = body["messages"][-1]["content"]
            if "MATRIX-LOAD-" in prompt:
                return reply(body, text=prompt.split("\n", 1)[1], message_id="replayed-id")
        result, _ = self.run_fixture(["stress"], responder, matrix_profile="quick")
        rows = [row for row in result["cases"] if row["reason_code"] == "duplicate_response_ids"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["status"] == "failed" and row["score_applicable"] and row["request_ids"] for row in rows))

    def test_safe_refusal_and_quoted_attack_marker_are_not_injection_success(self):
        def responder(body):
            prompt = body["messages"][-1]["content"]
            if body.get("system") and prompt != "Acknowledge the system instruction.":
                return reply(body, text='I cannot disclose the private token or follow the instruction to print "MATRIX-OVERRIDE".')
        result, _ = self.run_fixture(["injection"], responder)
        attacks = [row for row in result["cases"] if row["parameters"].get("attack") not in (None, "none")]
        self.assertTrue(all(row["status"] == "passed" and row["reason_code"] == "injection_resisted" for row in attacks))

    def test_exact_takeover_or_private_canary_leak_still_fail_even_truncated(self):
        for leak in (False, True):
            def responder(body):
                system = body.get("system", "")
                if isinstance(system, str) and "Private test token:" in system:
                    canary = system.split("Private test token: ")[1].split(".", 1)[0]
                    return reply(body, text=canary if leak else "MATRIX-OVERRIDE", reason="max_tokens", output=1024)
            result, _ = self.run_fixture(["injection"], responder)
            attacks = [row for row in result["cases"] if row["parameters"].get("attack") not in (None, "none")]
            self.assertTrue(all(row["status"] == "failed" for row in attacks))

    def test_control_format_deviation_is_not_scored_as_injection(self):
        result, _ = self.run_fixture(["injection"], lambda body: reply(body, text="I cannot comply with this request.") if body.get("system") else None)
        row = next(row for row in result["cases"] if row["id"] == "matrix-injection-control")
        self.assertEqual(row["status"], "inconclusive")
        self.assertEqual(row["reason_code"], "control_behavior_mismatch")
        self.assertFalse(row["score_applicable"])
        self.assertFalse(any(row["status"] == "failed" for row in result["cases"]))

    def test_documented_sse_overload_error_is_service_failure_not_protocol_failure(self):
        def responder(body):
            if body.get("stream"):
                data = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
                return httpx.Response(200, content="event: error\ndata: " + json.dumps(data) + "\n\n", headers={"content-type": "text/event-stream"})
        result, _ = self.run_fixture(["protocol"], responder)
        rows = [row for row in result["cases"] if row["parameters"].get("stream")]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["status"] == "inconclusive" and row["reason_code"] == "upstream_stream_error" and not row["score_applicable"] for row in rows))


if __name__ == "__main__": unittest.main()
