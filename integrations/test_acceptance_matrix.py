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
        for profile, expected, caps in (("quick", 27, 3), ("standard", 59, 12), ("comprehensive", 160, 72)):
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
                self.assertEqual(len(calls), 59)
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
            self.assertEqual(len(calls), 160)
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


if __name__ == "__main__": unittest.main()
