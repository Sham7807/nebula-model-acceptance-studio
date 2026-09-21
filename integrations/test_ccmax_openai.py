"""CCMax OpenAI branch regressions; only mocks and a loopback fixture are used."""
import json
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

try:
    from integrations import ccmax_acceptance as acceptance
except ImportError:
    import ccmax_acceptance as acceptance


KEY = "fixture-openai-key-never-save"


def frame(payload):
    return "data: " + (payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)) + "\n\n"


def chunk(identity, delta=None, finish=None, usage=None, index=0):
    return {"id": identity, "object": "chat.completion.chunk", "model": "claude-fixture", "choices": [] if usage is not None else [{"index": index, "delta": delta or {}, "finish_reason": finish}], "usage": usage}


def good_sse(tool=False, identity=None, cache=True):
    identity = identity or "chatcmpl-" + uuid.uuid4().hex
    data = frame(chunk(identity, {"role": "assistant", "content": ""}))
    if tool:
        data += frame(chunk(identity, {"tool_calls": [{"index": 0, "id": "call-fixture", "type": "function", "function": {"name": "acceptance_", "arguments": '{"token":'}}]}))
        data += frame(chunk(identity, {"tool_calls": [{"index": 0, "function": {"name": "echo", "arguments": '"channel-check"}'}}]}))
    else:
        data += frame(chunk(identity, {"content": "你好，测试通过。"}))
    data += frame(chunk(identity, finish="tool_calls" if tool else "stop"))
    usage = {"prompt_tokens": 25, "completion_tokens": 7, "total_tokens": 32}
    if cache:
        usage["prompt_tokens_details"] = {"cached_tokens": 16}
    return data + frame(chunk(identity, usage=usage)) + frame("[DONE]")


class ChunkStream(httpx.SyncByteStream):
    def __init__(self, value):
        self.value = value.encode()

    def __iter__(self):
        for i in range(0, len(self.value), 7):
            yield self.value[i:i + 7]


class InterruptedStream(httpx.SyncByteStream):
    def __init__(self, value, error=httpx.ReadError):
        self.value, self.error = value.encode(), error

    def __iter__(self):
        yield self.value
        raise self.error("local fixture interrupted")


class HangingStream(httpx.SyncByteStream):
    def __init__(self, value=""):
        self.value, self.closed = value.encode(), threading.Event()

    def __iter__(self):
        if self.value:
            yield self.value
        self.closed.wait(5)

    def close(self):
        self.closed.set()


def response_for(body):
    if body["model"].startswith("__channel_acceptance_invalid"):
        return 404, {"error": {"type": "invalid_request_error", "code": "model_not_found", "param": "model", "message": "model not found"}}
    if body.get("max_tokens") == 0:
        return 400, {"error": {"type": "invalid_request_error", "param": "max_tokens", "message": "max_tokens must be positive"}}
    if body.get("stream"):
        return 200, good_sse(bool(body.get("tool_choice")))
    system = next((m["content"] for m in body["messages"] if m["role"] == "system"), "")
    text = "I cannot reveal private verification details." if "private verification canary" in system else "CCMAX-SAFE-ACK" if "CCMAX-SAFE-ACK" in system else "CHANNEL-STABILITY-OK"
    return 200, {"id": "chatcmpl-" + uuid.uuid4().hex, "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}}


class OpenAIAcceptanceTests(unittest.TestCase):
    def config(self, handler=None, **kwargs):
        value = {"request_format": "openai", "base": "https://fixture.test/v1", "key": KEY, "model": "claude-fixture", "sse_samples": 1, "signature_samples": 3, "timeout": 5, "concurrency": 2, "transport": httpx.MockTransport(handler or self.good_handler)}
        value.update(kwargs)
        return value

    def good_handler(self, request):
        self.assertEqual(str(request.url), "https://fixture.test/v1/chat/completions")
        self.assertEqual(request.headers["authorization"], "Bearer " + KEY)
        self.assertNotIn("x-api-key", request.headers)
        self.assertNotIn("anthropic-version", request.headers)
        body = json.loads(request.content)
        self.assertNotIn("system", body)
        self.assertNotIn("metadata", body)
        self.assertNotIn("thinking", json.dumps(body))
        self.assertNotIn("input_schema", json.dumps(body))
        self.assertNotIn("cache_control", json.dumps(body))
        if body.get("stream"):
            self.assertEqual(body["stream_options"], {"include_usage": True})
            self.assertEqual(body["tools"][0]["type"], "function")
            self.assertIn("parameters", body["tools"][0]["function"])
        if body.get("tool_choice"):
            self.assertEqual(body["tool_choice"], {"type": "function", "function": {"name": "acceptance_echo"}})
        status, payload = response_for(body)
        headers = [("x-request-id", "upstream-fixture"), ("x-request-id", "gateway-fixture")]
        return httpx.Response(status, headers=headers, stream=ChunkStream(payload)) if isinstance(payload, str) else httpx.Response(status, headers=headers, json=payload)

    def one(self, response, probe="sse", **kwargs):
        settings, key = acceptance._configuration(self.config(**kwargs))
        spec = next(item for item in acceptance._probe_specs(settings) if item["probe"] == probe)
        return acceptance._collect_sample(spec, settings, key, httpx.MockTransport(lambda _: response), lambda: False)

    def check(self, sample, name):
        return next(row for row in sample["assessments"] if row["check"] == name)

    def test_run_uses_real_chat_format_and_signature_is_not_applicable(self):
        calls = []
        def handler(request):
            calls.append(request)
            return self.good_handler(request)
        result = acceptance.run(self.config(handler, auth="anthropic"))
        self.assertEqual(result["suite"], "ccmax_acceptance")
        self.assertEqual(result["configuration"]["request_format"], "openai")
        self.assertEqual(result["configuration"]["auth"], "bearer")
        self.assertEqual(result["summary"], {"total": 3, "completed": 3, "passed": 3, "failed": 0, "inconclusive": 0, "cancelled": 0})
        self.assertEqual(len(calls), 3)
        signature = result["checks"][0]
        self.assertEqual(signature["status"], "skipped")
        self.assertFalse(signature["applicable"])
        self.assertEqual(signature["samples"], 0)
        self.assertTrue(signature["skip_reason"])
        self.assertTrue(all(c["status"] == "passed" for c in result["checks"][1:]))
        self.assertTrue(all(all(field in c for field in ("title", "method", "expected", "meaning", "next_step", "applicable")) for c in result["checks"]))
        self.assertNotIn(KEY, json.dumps(result))
        tool = next(s for s in result["samples"] if s["probe"] == "tool")
        self.assertEqual(tool["evidence"]["sse"]["tools"][0]["input"], {"token": "channel-check"})
        self.assertEqual(tool["evidence"]["sse"]["done_count"], 1)
        self.assertNotIn("message_start_count", tool["evidence"]["sse"])
        self.assertEqual(len(tool["evidence"]["request_ids"]), 2)

    def test_advanced_probes_really_parse_choices_content(self):
        result = acceptance.run(self.config(advanced=True))
        self.assertEqual(result["summary"]["total"], 8)
        self.assertEqual(result["summary"]["passed"], 8)
        self.assertEqual(len(result["checks"]), 12)
        self.assertTrue(all(c["status"] == "passed" for c in result["checks"][1:]))
        samples = {sample["probe"]: sample for sample in result["samples"]}
        self.assertEqual(samples["instruction_hierarchy"]["evidence"]["output_text"], "CCMAX-SAFE-ACK")
        self.assertTrue(samples["prompt_injection"]["request"]["body"]["messages"][0]["role"] == "system")

    def test_empty_or_malformed_success_is_never_a_safety_pass(self):
        for probe in ("prompt_injection", "instruction_hierarchy", "fingerprint"):
            for payload in ({}, {"choices": []}, {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}, {"choices": [{"message": {"content": "CHANNEL-STABILITY-OK"}}]}, {"choices": [{"message": {"content": "CHANNEL-STABILITY-OK"}, "finish_reason": "length"}]}, {"error": {"message": "failed"}}):
                with self.subTest(probe=probe, payload=payload):
                    sample = self.one(httpx.Response(200, json=payload), probe, advanced=True)
                    self.assertEqual(sample["status"], "inconclusive")

    def test_leak_and_hierarchy_override_are_observed_failures(self):
        for probe, text in (("prompt_injection", "CCMAX-PRIVATE-CANARY-7f31"), ("instruction_hierarchy", "CCMAX-OVERRIDE")):
            sample = self.one(httpx.Response(200, json={"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}), probe, advanced=True)
            self.assertEqual(sample["status"], "failed")
            self.assertEqual(sample["evidence"]["output_text"], text)

    def test_different_repeatability_answers_are_not_claimed_as_distillation_proof(self):
        count = [0]
        def handler(request):
            body = json.loads(request.content)
            if "Return the required stability token." in json.dumps(body):
                count[0] += 1
                return httpx.Response(200, json={"choices": [{"message": {"content": "CHANNEL-STABILITY-OK" if count[0] == 1 else "changed"}, "finish_reason": "stop"}]})
            return self.good_handler(request)
        result = acceptance.run(self.config(handler, advanced=True))
        check = next(c for c in result["checks"] if c["id"] == "behavioral_consistency")
        self.assertEqual(check["status"], "failed")
        self.assertTrue(any("不能单独证明" in row["detail"] for row in check["details"]))

    def test_finish_reason_and_done_are_both_required_at_clean_eof(self):
        for source in (good_sse().replace(frame("[DONE]"), ""), good_sse().replace('"finish_reason": "stop"', '"finish_reason": null'), good_sse().replace('"finish_reason": "stop"', '"finish_reason": "end_turn"'), good_sse() + frame("[DONE]"), good_sse().rstrip("\n")):
            with self.subTest(source=source[-90:]):
                sample = self.one(httpx.Response(200, stream=ChunkStream(source)))
                self.assertEqual(self.check(sample, "message_stop")["status"], "failed")

    def test_role_id_and_choice_structure_are_checked(self):
        for source in (good_sse().replace('"role": "assistant"', '"role": "user"'), good_sse().replace('"index": 0', '"index": 1'), good_sse().replace('"chat.completion.chunk"', '"chat.completion"'), good_sse(identity="base").replace('"id": "base"', '"id": "other"', 1)):
            sample = self.one(httpx.Response(200, text=source))
            self.assertEqual(self.check(sample, "message_start")["status"], "failed")

    def test_plain_json_cannot_pass_stream_checks(self):
        for payload in ({"choices": [{"message": {"content": "text"}, "finish_reason": "stop"}]}, {"error": {"message": "upstream failure"}}):
            sample = self.one(httpx.Response(200, json=payload))
            self.assertEqual(self.check(sample, "message_stop")["status"], "failed")
            if "error" in payload:
                self.assertEqual(self.check(sample, "stream_error")["status"], "failed")

    def test_partial_tool_arguments_on_timeout_are_inconclusive(self):
        partial = frame(chunk("partial", {"role": "assistant"})) + frame(chunk("partial", {"tool_calls": [{"index": 0, "id": "partial-tool", "function": {"name": "acceptance_echo", "arguments": '{"token":'}}]}))
        for error in (httpx.ReadError, httpx.ReadTimeout):
            sample = self.one(httpx.Response(200, stream=InterruptedStream(partial, error)), "tool")
            self.assertEqual(sample["status"], "inconclusive")
            self.assertTrue(all(row["status"] == "inconclusive" for row in sample["assessments"]))
            self.assertEqual(sample["evidence"]["sse"]["tool_errors"], [])

    def test_observed_error_survives_later_disconnect(self):
        source = frame(chunk("partial", {"role": "assistant"})) + frame({"error": {"code": "overloaded", "message": "upstream failed"}})
        sample = self.one(httpx.Response(200, stream=InterruptedStream(source)))
        self.assertEqual(self.check(sample, "stream_error")["status"], "failed")
        self.assertEqual(sample["status"], "failed")
        self.assertEqual(len(sample["evidence"]["sse"]["errors"]), 1)

    def test_completed_tool_requires_valid_json_and_forced_arguments(self):
        for source in (good_sse(True).replace('channel-check', 'wrong-token'), good_sse(True).replace('acceptance_', 'wrong_'), good_sse(True).replace('\\"channel-check\\"}', '\\"channel-check\\"'), good_sse(True).replace('"finish_reason": "tool_calls"', '"finish_reason": "stop"'), good_sse()):
            sample = self.one(httpx.Response(200, stream=ChunkStream(source)), "tool")
            self.assertEqual(self.check(sample, "tool_stream")["status"], "failed")

    def test_usage_cache_is_optional_but_returned_values_are_validated(self):
        sample = self.one(httpx.Response(200, text=good_sse(cache=False)))
        self.assertEqual(self.check(sample, "usage_cache")["status"], "passed")
        self.assertIn("无法判断缓存命中", self.check(sample, "usage_cache")["detail"])
        sample = self.one(httpx.Response(200, text=good_sse().replace('"cached_tokens": 16', '"cached_tokens": null')))
        self.assertEqual(self.check(sample, "usage_cache")["status"], "passed")
        self.assertIn("无法判断缓存命中", self.check(sample, "usage_cache")["detail"])
        for change in (( '"cached_tokens": 16', '"cached_tokens": 100'), ('"prompt_tokens": 25', '"prompt_tokens": true'), ('"total_tokens": 32', '"total_tokens": 999')):
            sample = self.one(httpx.Response(200, text=good_sse().replace(*change)))
            self.assertEqual(self.check(sample, "usage_cache")["status"], "failed")
        source = "".join(frame_.strip("\n") + "\n\n" for frame_ in good_sse().split("\n\n") if frame_.strip() and '"prompt_tokens":' not in frame_)
        sample = self.one(httpx.Response(200, text=source))
        self.assertEqual(self.check(sample, "usage_cache")["status"], "inconclusive")

    def test_auth_limit_and_generic_parameter_errors_do_not_pass(self):
        for probe in ("invalid_model", "invalid_parameters"):
            for status, message in ((401, "key invalid"), (429, "rate limit"), (400, "route unknown"), (503, "capacity overloaded")):
                sample = self.one(httpx.Response(status, json={"error": {"message": message}}), probe, advanced=True)
                self.assertEqual(sample["status"], "inconclusive")

    def test_negative_model_probe_needs_valid_stream_baseline(self):
        def reject(request):
            return httpx.Response(404, json={"error": {"type": "invalid_request_error", "message": "model not found"}})
        result = acceptance.run(self.config(reject))
        self.assertEqual(next(c for c in result["checks"] if c["id"] == "error_format")["status"], "inconclusive")

    def test_parameter_rejection_also_needs_a_working_positive_control(self):
        def reject(request):
            return httpx.Response(400, json={"error": {"message": "max_tokens invalid"}})
        result = acceptance.run(self.config(reject, advanced=True))
        self.assertEqual(next(c for c in result["checks"] if c["id"] == "parameter_validation")["status"], "inconclusive")

    def test_tool_ids_cannot_be_reused_for_different_indices(self):
        identity = "tool-id-fixture"
        calls = [{"index": i, "id": "same-id", "type": "function", "function": {"name": "acceptance_echo", "arguments": '{"token":"channel-check"}'}} for i in range(2)]
        source = frame(chunk(identity, {"role": "assistant", "tool_calls": calls})) + frame(chunk(identity, finish="tool_calls")) + frame("[DONE]")
        sample = self.one(httpx.Response(200, text=source), "tool")
        self.assertEqual(self.check(sample, "tool_stream")["status"], "failed")
        self.assertIn("复用", self.check(sample, "tool_stream")["detail"])

    def test_done_uses_existing_bounded_response_close_watchdog(self):
        stream = HangingStream(good_sse())
        started = time.monotonic()
        sample = self.one(httpx.Response(200, stream=stream), close_grace=0.05)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(sample["termination"], "connection_grace_exceeded")
        self.assertEqual(self.check(sample, "connection")["status"], "failed")
        self.assertEqual(self.check(sample, "message_stop")["status"], "passed")
        self.assertTrue(stream.closed.is_set())

    def test_cancellation_does_not_start_remaining_requests(self):
        stop, calls = threading.Event(), []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, stream=HangingStream())
        threading.Timer(0.08, stop.set).start()
        started = time.monotonic()
        result = acceptance.run(self.config(handler, concurrency=1, sse_samples=10), cancelled=stop)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(len(calls), 1)

    def test_evidence_limit_does_not_turn_partial_json_into_bad_tool(self):
        source = frame(chunk("partial", {"role": "assistant"}))
        before = acceptance.MAX_EVIDENCE_BYTES
        acceptance.MAX_EVIDENCE_BYTES = len(source.encode())
        try:
            sample = self.one(httpx.Response(200, text=source + good_sse(True)), "tool")
        finally:
            acceptance.MAX_EVIDENCE_BYTES = before
        self.assertEqual(sample["termination"], "evidence_limit")
        self.assertEqual(sample["status"], "inconclusive")

    def test_unknown_format_is_rejected_before_requests(self):
        with self.assertRaisesRegex(ValueError, "请求格式"):
            acceptance.run(self.config(request_format="typo"))
        self.assertEqual(acceptance._endpoint("https://fixture.test/v1", "openai"), "https://fixture.test/v1/chat/completions")
        self.assertEqual(acceptance._endpoint("https://fixture.test/prefix", "openai"), "https://fixture.test/prefix/v1/chat/completions")
        self.assertEqual(acceptance._endpoint("https://fixture.test/v1/chat/completions", "openai"), "https://fixture.test/v1/chat/completions")

    def test_loopback_http_fixture_runs_without_mock_transport(self):
        observations = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                observations.append((self.path, self.headers.get("Authorization"), body))
                status, payload = response_for(body)
                data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream" if isinstance(payload, str) else "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = acceptance.run(self.config(base="http://127.0.0.1:%s" % server.server_port, transport=None, advanced=True))
            self.assertEqual(result["summary"]["passed"], 8)
            self.assertEqual(len(observations), 8)
            self.assertTrue(all(path == "/v1/chat/completions" and auth == "Bearer " + KEY for path, auth, _ in observations))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
