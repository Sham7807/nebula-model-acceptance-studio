"""Isolated acceptance tests: every HTTP request uses httpx.MockTransport."""

import json
import socket
import threading
import time
import unittest
import uuid

import httpx

try:
    from integrations import ccmax_acceptance as acceptance
except ImportError:
    import ccmax_acceptance as acceptance


def event(kind, data=None):
    value = dict(data or {})
    value["type"] = kind
    return "event: %s\ndata: %s\n\n" % (kind, json.dumps(value, ensure_ascii=False))


def good_sse(tool=False, message_id="msg_local_mock"):
    data = event("message_start", {"message": {"id": message_id, "type": "message", "usage": {"input_tokens": 11, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}})
    if tool:
        data += event("content_block_start", {"index": 0, "content_block": {"type": "tool_use", "id": "toolu_local", "name": "acceptance_echo", "input": {}}})
        data += event("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"token":'}})
        data += event("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": '"channel-check"}'}})
        data += event("content_block_stop", {"index": 0})
    else:
        data += event("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}})
        data += event("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "你好"}})
        data += event("content_block_stop", {"index": 0})
    data += event("message_delta", {"delta": {"stop_reason": "tool_use" if tool else "end_turn"}, "usage": {"output_tokens": 8}})
    return data + event("message_stop")


class ChunkStream(httpx.SyncByteStream):
    def __init__(self, content, step=7):
        self.data = content.encode()
        self.step = step

    def __iter__(self):
        for i in range(0, len(self.data), self.step):
            yield self.data[i:i + self.step]


class HangingStream(httpx.SyncByteStream):
    def __init__(self, before=""):
        self.before = before.encode()
        self.closed = threading.Event()

    def __iter__(self):
        if self.before:
            yield self.before
        self.closed.wait(5)

    def close(self):
        self.closed.set()


class InterruptedStream(httpx.SyncByteStream):
    """Deliver HTTP 200 body bytes, then interrupt without waiting in real time."""

    def __init__(self, before, error=httpx.ReadTimeout):
        self.before = before.encode()
        self.error = error

    def __iter__(self):
        if self.before:
            yield self.before
        raise self.error("local mock interrupted response")


class AcceptanceTests(unittest.TestCase):
    def config(self, handler=None, **values):
        result = {"base": "https://relay.test/v1", "key": "test-secret-never-persist", "model": "ccmax-test", "signature_samples": 1, "sse_samples": 1, "timeout": 5, "concurrency": 2,
                  "transport": httpx.MockTransport(handler or self.good_handler)}
        result.update(values)
        return result

    def good_handler(self, request):
        self.assertEqual(str(request.url), "https://relay.test/v1/messages")
        self.assertEqual(request.headers["x-api-key"], "test-secret-never-persist")
        body = json.loads(request.content)
        headers = [("x-request-id", "upstream-123"), ("x-request-id", "relay-456")]
        if body["model"].startswith("__channel_acceptance_invalid"):
            return httpx.Response(404, headers=headers, json={"type": "error", "error": {"type": "not_found_error", "message": "model not found"}})
        if body.get("stream"):
            return httpx.Response(200, headers=headers, stream=ChunkStream(good_sse(bool(body.get("tool_choice")), "msg_" + uuid.uuid4().hex)))
        return httpx.Response(400, headers=headers, json={"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid signature in thinking block"}})

    def test_complete_good_run_retains_evidence_and_redacts_request_key(self):
        notifications = []
        result = acceptance.run(self.config(), notifications.append, lambda: False)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["summary"], {"total": 4, "completed": 4, "passed": 4, "failed": 0, "inconclusive": 0, "cancelled": 0})
        self.assertEqual([c["status"] for c in result["checks"]], ["passed"] * 8)
        self.assertNotIn("test-secret-never-persist", json.dumps(result))
        for sample in result["samples"]:
            self.assertEqual([v["value"] for v in sample["evidence"]["request_ids"]], ["upstream-123", "relay-456"])
            self.assertTrue(sample["response"]["body"])
            self.assertIn("body", sample["request"])
        sse = result["samples"][1]
        self.assertIn("你好", sse["response"]["body"])
        tool = result["samples"][2]["evidence"]["sse"]["tools"][0]
        self.assertEqual(tool["input"], {"token": "channel-check"})
        self.assertEqual(notifications[0]["total"], 4)
        self.assertEqual(notifications[-1]["type"], "result")
        self.assertEqual(sum(n.get("phase") == "sample_complete" for n in notifications), 4)

    def test_advanced_probes_cover_injection_hierarchy_repeatability_and_parameters(self):
        calls = {"fingerprint": 0}

        def advanced_handler(request):
            body = json.loads(request.content)
            if body.get("model", "").startswith("__channel_acceptance_invalid"):
                return httpx.Response(404, json={"type": "error", "error": {"type": "not_found_error", "message": "model not found"}})
            if any(isinstance(m, dict) and m.get("role") == "assistant" for m in body.get("messages", [])):
                return httpx.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid signature in thinking block"}})
            if body.get("stream"):
                return httpx.Response(200, stream=ChunkStream(good_sse(bool(body.get("tool_choice")), "msg_" + uuid.uuid4().hex)))
            if body.get("max_tokens") == 0:
                return httpx.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "max_tokens must be greater than 0"}})
            system = json.dumps(body.get("system", ""), ensure_ascii=False)
            if "private verification canary" in system:
                text = "I cannot reveal the system instruction or private canary."
            elif "CCMAX-SAFE-ACK" in system:
                text = "CCMAX-SAFE-ACK"
            else:
                calls["fingerprint"] += 1
                text = "CHANNEL-STABILITY-OK" if calls["fingerprint"] == 1 else "CHANNEL-STABILITY-OK"
            return httpx.Response(200, json={"type": "message", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"})

        result = acceptance.run(self.config(advanced_handler, advanced=True), None, lambda: False)
        self.assertEqual(result["summary"], {"total": 9, "completed": 9, "passed": 9, "failed": 0, "inconclusive": 0, "cancelled": 0})
        self.assertEqual([c["id"] for c in result["checks"]], [name for name, _ in acceptance.BASE_CHECKS + acceptance.ADVANCED_CHECKS])
        self.assertEqual([c["status"] for c in result["checks"]], ["passed"] * 12)
        self.assertIn("不能单独证明", result["notes"][2])

    def test_advanced_repeatability_mismatch_is_a_review_signal(self):
        calls = 0
        def handler(request):
            nonlocal calls
            body = json.loads(request.content)
            if any(isinstance(m, dict) and m.get("role") == "assistant" for m in body.get("messages", [])):
                return httpx.Response(400, json={"error": {"type": "invalid_request_error", "message": "Invalid signature in thinking block"}})
            if body.get("stream"):
                return httpx.Response(200, stream=ChunkStream(good_sse(bool(body.get("tool_choice")), "msg_" + uuid.uuid4().hex)))
            if body.get("model", "").startswith("__channel_acceptance_invalid"):
                return httpx.Response(404, json={"error": {"type": "not_found_error", "message": "model not found"}})
            if body.get("max_tokens") == 0:
                return httpx.Response(400, json={"error": {"type": "invalid_request_error", "message": "max_tokens invalid"}})
            system = json.dumps(body.get("system", ""), ensure_ascii=False)
            if "private verification canary" in system: text = "refusal"
            elif "CCMAX-SAFE-ACK" in system: text = "CCMAX-SAFE-ACK"
            else:
                calls += 1; text = "CHANNEL-STABILITY-OK" if calls == 1 else "UNEXPECTED-VARIANT"
            return httpx.Response(200, json={"type": "message", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"})
        result = acceptance.run(self.config(handler, advanced=True), None, lambda: False)
        check = next(row for row in result["checks"] if row["id"] == "behavioral_consistency")
        self.assertEqual(check["status"], "failed")
        self.assertTrue(any("启发式" in row["detail"] for row in check["details"]))

    def one(self, response, probe="signature", **options):
        settings, key = acceptance._configuration(self.config(**options))
        spec = next(x for x in acceptance._probe_specs(settings) if x["probe"] == probe)
        transport = httpx.MockTransport(lambda request: response)
        return acceptance._collect_sample(spec, settings, key, transport, lambda: False)

    def check(self, sample, name):
        return next(x for x in sample["assessments"] if x["check"] == name)

    def test_auth_rate_limit_and_generic_400_are_not_signature_acceptance(self):
        for code, text in [(401, "invalid key"), (429, "rate limit"), (500, "upstream failure"), (400, "thinking unsupported")]:
            with self.subTest(code=code):
                sample = self.one(httpx.Response(code, json={"error": {"type": "error", "message": text}}))
                self.assertEqual(self.check(sample, "signature")["status"], "inconclusive")

    def test_successful_forged_signature_is_failure(self):
        sample = self.one(httpx.Response(200, json={"id": "msg_accept", "type": "message", "content": [{"type": "text", "text": "hello"}], "stop_reason": "end_turn"}))
        self.assertEqual(self.check(sample, "signature")["status"], "failed")
        self.assertEqual(sample["evidence"]["message_ids"], ["msg_accept"])

    def test_200_error_body_is_not_signature_acceptance(self):
        sample = self.one(httpx.Response(200, json={"error": {"message": "unauthorized"}}))
        self.assertEqual(self.check(sample, "signature")["status"], "inconclusive")

    def test_non_200_sse_is_inconclusive_protocol_coverage(self):
        sample = self.one(httpx.Response(429, json={"error": {"type": "rate_limit_error", "message": "slow down"}}), "sse")
        self.assertTrue(all(row["status"] == "inconclusive" for row in sample["assessments"]))

    def test_duplicate_start_and_missing_stop_are_detected(self):
        source = good_sse().replace(event("message_stop"), "")
        source += event("message_start", {"message": {"id": "msg_second", "usage": {"input_tokens": 1, "output_tokens": 0}}})
        sample = self.one(httpx.Response(200, text=source), "sse")
        self.assertEqual(self.check(sample, "message_start")["status"], "failed")
        self.assertEqual(self.check(sample, "message_stop")["status"], "failed")
        self.assertEqual(sample["evidence"]["message_ids"], ["msg_local_mock", "msg_second"])

    def test_unterminated_stop_is_not_treated_as_complete(self):
        source = good_sse().rstrip("\n")
        sample = self.one(httpx.Response(200, text=source), "sse")
        self.assertEqual(self.check(sample, "message_stop")["status"], "failed")
        self.assertTrue(sample["evidence"]["sse"]["incomplete_event"])

    def test_error_event_is_recorded_without_claiming_protocol_violation(self):
        source = good_sse().replace(event("message_stop"), event("error", {"error": {"type": "overloaded_error", "message": "try later"}}) + event("message_stop"))
        sample = self.one(httpx.Response(200, text=source), "sse")
        row = self.check(sample, "stream_error")
        self.assertEqual(row["status"], "failed")
        self.assertIn("协议允许", row["detail"])
        self.assertEqual(len(sample["evidence"]["sse"]["errors"]), 1)

    def test_connection_grace_closes_hung_completed_stream(self):
        stream = HangingStream(good_sse())
        started = time.monotonic()
        sample = self.one(httpx.Response(200, stream=stream), "sse", close_grace=0.05)
        self.assertLess(time.monotonic() - started, 1)
        self.assertTrue(stream.closed.is_set())
        self.assertEqual(sample["termination"], "connection_grace_exceeded")
        self.assertEqual(self.check(sample, "connection")["status"], "failed")
        self.assertEqual(self.check(sample, "message_stop")["status"], "passed")

    def test_grace_uses_trace_socket_when_response_omits_network_extension(self):
        reader, peer = socket.socketpair()
        read_finished = threading.Event()
        closed_by = []

        class NetworkStream:
            def get_extra_info(self, name):
                return reader if name == "socket" else None

            def close(self):
                reader.close()

        class ResponseStream(httpx.SyncByteStream):
            def __iter__(self):
                yield good_sse().encode()
                try:
                    reader.settimeout(2)
                    reader.recv(1)
                finally:
                    read_finished.set()

            def close(self):
                # Model a close that cannot proceed until the pending read ends.
                # Calling only Response.close() would wait for the socket timeout.
                read_finished.wait(2)
                closed_by.append(threading.current_thread().name)
                reader.close()

        def handler(request):
            request.extensions["trace"]("connection.connect_tcp.complete", {"return_value": NetworkStream()})
            return httpx.Response(200, stream=ResponseStream())

        settings, key = acceptance._configuration(self.config(close_grace=0.05))
        spec = next(x for x in acceptance._probe_specs(settings) if x["probe"] == "sse")
        started = time.monotonic()
        try:
            sample = acceptance._collect_sample(spec, settings, key, httpx.MockTransport(handler), lambda: False)
            self.assertLess(time.monotonic() - started, 0.8)
            self.assertTrue(read_finished.is_set())
            self.assertEqual(closed_by, [threading.current_thread().name])
            self.assertEqual(reader.fileno(), -1)
            self.assertEqual(sample["termination"], "connection_grace_exceeded")
            self.assertEqual(self.check(sample, "connection")["status"], "failed")
            self.assertEqual(self.check(sample, "message_stop")["status"], "passed")
            self.assertLess(sample["evidence"]["after_stop_ms"], 800)
        finally:
            reader.close()
            peer.close()

    def test_timeout_before_stop_is_not_connection_hang_claim(self):
        def fail(request):
            raise httpx.ReadTimeout("local mock timeout", request=request)
        result = acceptance.run(self.config(fail), None, lambda: False)
        self.assertEqual(result["summary"]["inconclusive"], 4)
        self.assertEqual(next(c for c in result["checks"] if c["id"] == "connection")["status"], "inconclusive")

    def partial_tool(self):
        return (event("message_start", {"message": {"id": "msg_partial", "usage": {"input_tokens": 11, "output_tokens": 0}}})
                + event("content_block_start", {"index": 0, "content_block": {"type": "tool_use", "id": "toolu_partial", "name": "acceptance_echo", "input": {}}})
                + event("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"token":'}}))

    def test_http_200_partial_stream_timeout_or_network_loss_is_inconclusive(self):
        for error, termination in [(httpx.ReadTimeout, "timeout"), (httpx.ReadError, "network_error")]:
            for source in ["", self.partial_tool(), self.partial_tool() + 'event: content_block_delta\ndata: {"type":']:
                with self.subTest(error=error.__name__, body=source[-60:]):
                    sample = self.one(httpx.Response(200, stream=InterruptedStream(source, error)), "tool")
                    self.assertEqual(sample["termination"], termination)
                    self.assertEqual(sample["response"]["status"], 200)
                    self.assertEqual(sample["status"], "inconclusive")
                    self.assertTrue(all(row["status"] == "inconclusive" for row in sample["assessments"]))
                    self.assertEqual(sample["evidence"]["sse"]["tool_errors"], [])
                    self.assertFalse(any("缺少" in issue for issue in sample["issues"]))

    def test_partial_sse_evidence_limit_does_not_claim_missing_channel_fields(self):
        source = self.partial_tool()
        original = acceptance.MAX_EVIDENCE_BYTES
        acceptance.MAX_EVIDENCE_BYTES = len(source.encode())
        try:
            sample = self.one(httpx.Response(200, text=source + event("message_stop")), "tool")
        finally:
            acceptance.MAX_EVIDENCE_BYTES = original
        self.assertEqual(sample["termination"], "evidence_limit")
        self.assertEqual(sample["response"]["body"], source)
        self.assertEqual(sample["status"], "inconclusive")
        self.assertTrue(all(row["status"] == "inconclusive" for row in sample["assessments"]))
        self.assertEqual(sample["evidence"]["sse"]["tool_errors"], [])

    def test_observed_stream_defects_still_fail_after_timeout(self):
        partial = self.partial_tool()
        cases = [
            (partial + event("message_start", {"message": {"id": "msg_duplicate", "usage": {"input_tokens": 1, "output_tokens": 0}}}), "message_start"),
            (partial + 'event: ping\ndata: {broken-json}\n\n', "message_stop"),
            (partial + event("error", {"error": {"type": "overloaded_error", "message": "upstream failed"}}), "stream_error"),
            (partial.replace('"input_tokens": 11', '"input_tokens": -1'), "usage_cache"),
            (partial + event("content_block_stop", {"index": 0}), "tool_stream"),
            (partial.replace('"name": "acceptance_echo"', '"name": "wrong_tool"'), "tool_stream"),
            (json.dumps({"error": {"message": "JSON instead of SSE"}}), "message_stop"),
            (partial + event("message_stop"), "message_stop"),
        ]
        for source, check in cases:
            with self.subTest(check=check, body=source[-80:]):
                sample = self.one(httpx.Response(200, stream=InterruptedStream(source)), "tool")
                self.assertEqual(sample["termination"], "timeout")
                self.assertEqual(sample["status"], "failed")
                self.assertEqual(self.check(sample, check)["status"], "failed")

    def test_normal_eof_with_missing_tail_remains_failure(self):
        sample = self.one(httpx.Response(200, text=self.partial_tool()), "tool")
        self.assertEqual(sample["termination"], "eof")
        self.assertEqual(sample["status"], "failed")
        for check in ("message_stop", "usage_cache", "tool_stream"):
            self.assertEqual(self.check(sample, check)["status"], "failed")

    def test_timeout_after_stop_does_not_claim_partial_ping_is_malformed(self):
        sample = self.one(httpx.Response(200, stream=InterruptedStream(good_sse() + 'event: ping\ndata: {"type":')), "sse")
        self.assertEqual(sample["status"], "inconclusive")
        self.assertEqual(self.check(sample, "message_stop")["status"], "passed")
        self.assertEqual(self.check(sample, "connection")["status"], "inconclusive")

    def test_usage_negative_bool_and_invalid_cache_values_fail(self):
        for name, value in [("output_tokens", -1), ("input_tokens", True), ("cache_read_input_tokens", "42")]:
            source = good_sse()
            original = {"output_tokens": 0, "input_tokens": 11, "cache_read_input_tokens": 0}[name]
            source = source.replace(json.dumps(name) + ": " + json.dumps(original), json.dumps(name) + ": " + json.dumps(value), 1)
            sample = self.one(httpx.Response(200, text=source), "sse")
            self.assertEqual(self.check(sample, "usage_cache")["status"], "failed")

    def test_cache_fields_optional_and_do_not_imply_cache_hit(self):
        source = good_sse().replace(', "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0', "")
        sample = self.one(httpx.Response(200, text=source), "sse")
        self.assertEqual(self.check(sample, "usage_cache")["status"], "passed")
        self.assertIn("无法判断缓存命中", self.check(sample, "usage_cache")["detail"])

    def test_tool_json_index_and_forced_argument_errors(self):
        for source in [
            good_sse(tool=True).replace('channel-check', 'wrong-token'),
            good_sse(tool=True).replace('"partial_json": "\\\"channel-check\\\"}"', '"partial_json": "not-json"'),
            good_sse(tool=True).replace('"index": 0, "delta"', '"index": 9, "delta"', 1),
            good_sse(tool=True).replace(event("content_block_stop", {"index": 0}), ""),
        ]:
            with self.subTest(source=source[-200:]):
                sample = self.one(httpx.Response(200, text=source), "tool")
                self.assertEqual(self.check(sample, "tool_stream")["status"], "failed")

    def test_missing_forced_tool_is_failure(self):
        sample = self.one(httpx.Response(200, text=good_sse()), "tool")
        self.assertEqual(self.check(sample, "tool_stream")["status"], "failed")

    def test_final_message_delta_and_usage_are_required(self):
        source = event("message_start", {"message": {"id": "m", "usage": {"input_tokens": 1,"output_tokens": 0}}}) + event("message_stop")
        sample = self.one(httpx.Response(200, text=source), "sse")
        self.assertEqual(self.check(sample, "message_stop")["status"], "failed")
        self.assertEqual(self.check(sample, "usage_cache")["status"], "failed")

    def test_unrelated_400_is_not_model_validation(self):
        sample = self.one(httpx.Response(400,json={"error":{"type":"invalid_request_error","message":"unknown endpoint"}}),"invalid_model")
        self.assertEqual(self.check(sample,"error_format")["status"],"inconclusive")

    def test_mixed_inconclusive_samples_do_not_make_clean_check(self):
        calls = 0
        def handler(request):
            nonlocal calls
            body=json.loads(request.content)
            if body.get("stream") and not body.get("tool_choice"):
                calls+=1
                if calls==2:return httpx.Response(429,json={"error":{"type":"rate_limit_error","message":"slow down"}})
            return self.good_handler(request)
        result=acceptance.run(self.config(handler,sse_samples=2))
        self.assertEqual(next(x for x in result["checks"] if x["id"]=="message_stop")["status"],"inconclusive")

    def test_message_id_reuse_across_requests_is_reported(self):
        def handler(request):
            body=json.loads(request.content)
            if body.get("stream"):
                return httpx.Response(200,text=good_sse(bool(body.get("tool_choice")),"msg_duplicate"))
            return self.good_handler(request)
        result=acceptance.run(self.config(handler))
        check=next(x for x in result["checks"] if x["id"]=="message_start")
        self.assertEqual(check["status"],"failed")
        self.assertIn("复用",str(check["details"]))

    def test_invalid_model_checks_status_and_error_shape(self):
        cases = [(200, {"type": "message"}, "failed"), (404, {"message": "missing"}, "failed"), (401, {"error": {"type": "authentication_error", "message": "bad key"}}, "inconclusive")]
        for code, payload, expected in cases:
            sample = self.one(httpx.Response(code, json=payload), "invalid_model")
            self.assertEqual(self.check(sample, "error_format")["status"], expected)

    def test_parser_handles_comments_multiline_data_and_crlf(self):
        parser = acceptance.SSEAnalysis()
        source = ': heartbeat\r\n\r\nevent: message_start\r\ndata: {"type":"message_start",\r\ndata: "message":{"id":"msg_multiline","usage":{"input_tokens":1,"output_tokens":0}}}\r\n\r\n'
        for char in source:
            parser.feed(char)
        parser.finish()
        self.assertEqual(parser.starts, 1)
        self.assertEqual(parser.message_ids, ["msg_multiline"])
        self.assertEqual(parser.malformed, [])

    def test_parser_flags_type_mismatch_and_trailing_events(self):
        parser = acceptance.SSEAnalysis()
        parser.feed(good_sse() + 'event: ping\ndata: {"type":"message_delta","usage":{"output_tokens":1}}\n\n')
        self.assertTrue(parser.sequence_errors)

    def test_content_before_start_and_unclosed_text_block_fail_completion(self):
        for source in [
            event("content_block_start", {"index": 9, "content_block": {"type": "text", "text": ""}}) + good_sse(),
            good_sse().replace(event("content_block_stop", {"index": 0}), ""),
        ]:
            sample = self.one(httpx.Response(200, text=source), "sse")
            self.assertEqual(self.check(sample, "message_stop")["status"], "failed")

    def test_pre_cancelled_run_sends_no_requests(self):
        calls = []
        result = acceptance.run(self.config(lambda request: calls.append(request)), None, lambda: True)
        self.assertEqual(calls, [])
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["summary"]["completed"], 0)

    def test_cancel_is_prompt_and_stops_scheduling_more_work(self):
        stop = threading.Event()
        entered = threading.Event()
        calls = []
        def handler(request):
            calls.append(request)
            entered.set()
            return httpx.Response(200, stream=HangingStream())
        threading.Timer(0.08, stop.set).start()
        started = time.monotonic()
        result = acceptance.run(self.config(handler, concurrency=1, sse_samples=50), None, stop)
        self.assertTrue(entered.is_set())
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(len(calls), 1)

    def test_cancel_keeps_partial_response_evidence(self):
        stop = threading.Event()
        source = event("message_start", {"message": {"id": "msg_cancelled_partial", "usage": {"input_tokens": 1, "output_tokens": 0}}})
        settings, key = acceptance._configuration(self.config())
        spec = next(x for x in acceptance._probe_specs(settings) if x["probe"] == "sse")
        threading.Timer(0.05, stop.set).start()
        sample = acceptance._collect_sample(spec, settings, key, httpx.MockTransport(lambda request: httpx.Response(200, stream=HangingStream(source))), stop)
        self.assertEqual(sample["status"], "cancelled")
        self.assertEqual(sample["response"]["body"], source)
        self.assertEqual(sample["evidence"]["message_ids"], ["msg_cancelled_partial"])

    def test_local_socket_deadline_works_before_headers_arrive(self):
        # This local-only endpoint accepts a connection but deliberately never
        # returns headers. The trace hook must expose its socket to the deadline.
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        server_done = threading.Event()
        def serve():
            connection = None
            try:
                connection, _ = listener.accept()
                connection.settimeout(1)
                while connection.recv(65536):
                    pass
            except OSError:
                pass
            finally:
                if connection:
                    connection.close()
                server_done.set()
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        settings, key = acceptance._configuration(self.config(base="http://127.0.0.1:%s" % listener.getsockname()[1]))
        settings["timeout"] = 0.15
        spec = acceptance._probe_specs(settings)[0]
        started = time.monotonic()
        try:
            sample = acceptance._collect_sample(spec, settings, key, None, lambda: False)
            self.assertLess(time.monotonic() - started, 0.8)
            self.assertEqual(sample["termination"], "timeout")
            self.assertTrue(server_done.wait(0.3))
        finally:
            listener.close()

    def test_evidence_limit_is_explicit_and_not_reported_as_success(self):
        original = acceptance.MAX_EVIDENCE_BYTES
        acceptance.MAX_EVIDENCE_BYTES = 12
        try:
            sample = self.one(httpx.Response(200, text="a" * 30))
        finally:
            acceptance.MAX_EVIDENCE_BYTES = original
        self.assertEqual(sample["response"]["body"], "a" * 12)
        self.assertTrue(sample["evidence"]["truncated"])
        self.assertEqual(sample["termination"], "evidence_limit")
        self.assertEqual(sample["status"], "inconclusive")

    def test_concurrency_bound_is_enforced(self):
        state = {"active": 0, "maximum": 0}
        lock = threading.Lock()
        def handler(request):
            with lock:
                state["active"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
            time.sleep(0.015)
            response = self.good_handler(request)
            with lock:
                state["active"] -= 1
            return response
        result = acceptance.run(self.config(handler, sse_samples=8, concurrency=3))
        self.assertEqual(result["summary"]["completed"], 11)
        self.assertGreater(state["maximum"], 1)
        self.assertLessEqual(state["maximum"], 3)

    def test_config_is_validated_before_requests(self):
        bad = [{"base": "file:///etc/passwd"}, {"base": "https://user:pass@relay.test"}, {"base": "https://relay.test/?key=secret"}, {"signature_samples": 0}, {"signature_samples": 21}, {"sse_samples": 201}, {"timeout": 4}, {"timeout": 601}, {"concurrency": 11}, {"concurrency": True}, {"sse_samples": 1.5}, {"close_grace": float("nan")}, {"advanced": "yes"}, {"key": ""}, {"model": ""}]
        for patch in bad:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                acceptance.run(self.config(**patch))

    def test_endpoint_normalization(self):
        self.assertEqual(acceptance._endpoint("https://relay.test"), "https://relay.test/v1/messages")
        self.assertEqual(acceptance._endpoint("https://relay.test/v1"), "https://relay.test/v1/messages")
        self.assertEqual(acceptance._endpoint("https://relay.test/v1/messages"), "https://relay.test/v1/messages")

    def test_bearer_auth_without_recording_auth_header(self):
        def handler(request):
            self.assertEqual(request.headers["authorization"], "Bearer test-secret-never-persist")
            self.assertNotIn("x-api-key", request.headers)
            self.assertEqual(request.headers["anthropic-version"], "2023-06-01")
            return httpx.Response(401, json={"error": {"message": "mock auth rejected"}})
        result = acceptance.run(self.config(handler, auth="bearer"))
        self.assertNotIn("test-secret-never-persist", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
