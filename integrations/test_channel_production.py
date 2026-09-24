"""Production suite fixtures: no real upstream model calls or paid requests."""
import json
import re
import threading
import time
import unittest
from unittest.mock import patch

import httpx

try:
    from . import channel_production as p, ccmax_acceptance as core
    from .test_acceptance_matrix import reply
except ImportError:
    import channel_production as p
    import ccmax_acceptance as core
    from test_acceptance_matrix import reply


class FakeClock:
    def __init__(self): self.value = 0.; self.lock = threading.Lock()
    def monotonic(self):
        with self.lock: return self.value
    def time(self): return 1700000000 + self.monotonic()
    def sleep(self, seconds):
        with self.lock: self.value += max(0, seconds)
        time.sleep(0)


class ChunkStream(httpx.SyncByteStream):
    def __init__(self, chunks, delay=.002): self.chunks, self.delay, self.closed = chunks, delay, False
    def __iter__(self):
        for chunk in self.chunks:
            time.sleep(self.delay)
            yield chunk
    def close(self): self.closed = True


class ProductionTests(unittest.TestCase):
    def config(self, *, fmt="anthropic", **production):
        return {"base": "https://relay.test/proxy/v2", "model": "fixture", "key": "fixture-secret", "request_format": fmt, "timeout": 5, "_clock": FakeClock(), "production": {"profile": "custom", "duration_seconds": 60, "max_requests": 8, "concurrency": 1, "workloads": ["short"], "recovery_delay_ms": 0, **production}}

    def handler(self, request):
        if "production-invalid-" in request.headers.get("x-api-key", request.headers.get("authorization", "")):
            return httpx.Response(401, json={"error": {"message": "invalid fixture credential"}})
        body = json.loads(request.content)
        fmt = "openai" if request.url.path.endswith("/chat/completions") else "anthropic"
        prompt = body["messages"][-1]["content"]
        all_text = json.dumps(body["messages"])
        if body.get("tools"):
            has_results = '"tool_result"' in all_text or any(m["role"] == "tool" for m in body["messages"])
            final = body.get("tool_choice") == "none" or body.get("tool_choice") == {"type": "auto"}
            if final: return reply(body, text="27283641", fmt=fmt)
            expression = "27271296 + 12345" if has_results else "3456 * 7891"
            response = reply(body, tools=[{"id": "calc-second" if has_results else "calc-first", "name": "Calculator", "input": {"expr": expression}}], fmt=fmt)
            if fmt == "anthropic":
                payload = json.loads(response.content)
                payload["content"].insert(0, {"type": "thinking", "thinking": "fixture reasoning", "signature": "preserve-exact-signature"})
                return httpx.Response(200, json=payload)
            return response
        if isinstance(prompt, str) and prompt.startswith("Reply with exactly this text"):
            return reply(body, text=prompt.split("\n", 1)[1], fmt=fmt)
        if isinstance(prompt, str) and prompt.startswith("Output exactly"):
            count = int(re.search(r"Output exactly (\d+)", prompt).group(1))
            marker = re.search(r"final line ([A-Z0-9-]+)", prompt).group(1)
            text = "\n".join("%04d channel delivery verified" % i for i in range(1, count + 1)) + "\n" + marker
            return reply(body, text=text, fmt=fmt)
        if isinstance(prompt, str) and "HEAD_MARKER=" in prompt:
            return reply(body, text=" ".join(re.findall(r"(?:HEAD|MIDDLE|TAIL)_MARKER=([A-Z0-9-]+)", prompt)), fmt=fmt)
        if body.get("thinking") or body.get("reasoning_effort"): return reply(body, text="27283641", fmt=fmt)
        if isinstance(prompt, list): return reply(body, text="3", fmt=fmt)
        return reply(body, text="custom-ok", fmt=fmt)

    def run_fixture(self, config, handler=None):
        config["transport"] = httpx.MockTransport(handler or self.handler)
        return p.run(config)

    def test_off_is_backwards_compatible_and_never_requires_credentials(self):
        self.assertFalse(p.configuration({})["enabled"])
        self.assertEqual(p.build_plan({})["request_count"], 0)
        self.assertEqual(p.run({})["metrics"]["attempted_requests"], 0)

    def test_configuration_bounds_presets_roundtrip_and_request_format(self):
        for profile, count, seconds in (("screening", 60, 120), ("standard", 600, 1800), ("soak", 2000, 21600)):
            config = self.config(); config["production"] = {"profile": profile}
            settings = p.configuration(config)
            self.assertEqual((settings["max_requests"], settings["duration_seconds"]), (count, seconds))
            self.assertEqual(p.configuration({**config, "production": settings}), settings)
        for field, value in (("concurrency", 21), ("duration_seconds", 86401), ("max_requests", True), ("recovery_retries", 3), ("requests_per_minute", -1)):
            with self.subTest(field=field), self.assertRaises(ValueError): p.configuration(self.config(**{field: value}))
        config = self.config(fmt="native"); config["suite"] = "kvv11"
        self.assertEqual(p.configuration(config)["request_format"], "openai")

    def test_plan_is_keyless_reviewable_and_does_not_mutate_custom_model(self):
        config = self.config(workloads=["custom"], custom_cases=[{"body": {"messages": [{"role": "user", "content": "hello"}]}, "expect": {"exact_text": "custom-ok"}}])
        plan = p.build_plan({**config, "key": ""})
        self.assertNotIn("model", plan["configuration"]["custom_cases"][0]["body"])
        self.assertEqual(plan["workloads"][0]["example"]["body"]["model"], "fixture")
        self.assertNotIn("fixture-secret", json.dumps(plan))
        self.assertIn("非账单上限", plan["estimate_method"])
        self.assertTrue(plan["workloads"][0]["example"]["url"].endswith("/v2/messages"))

    def test_custom_ui_aliases_and_batch_model_rebinding(self):
        config = self.config(workloads=["custom"], custom_cases=[{"name": "真实业务", "body": {"model": "fixture", "messages": [{"role": "user", "content": "hello"}]}, "expected_text": "custom-ok"}])
        settings = p.configuration(config)
        second = p.build_plan({**config, "model": "second-model", "production": settings})
        self.assertEqual(second["workloads"][0]["example"]["body"]["model"], "second-model")
        self.assertEqual(second["configuration"]["custom_cases"][0]["title"], "真实业务")
        self.assertEqual(second["configuration"]["custom_cases"][0]["expect"]["exact_text"], "custom-ok")

    def test_invalid_auth_accepted_stops_all_load(self):
        def handler(request):
            body = json.loads(request.content)
            return reply(body, text=body["messages"][-1]["content"].split("\n", 1)[1])
        result = self.run_fixture(self.config(), handler)
        self.assertEqual(len(result["samples"]), 2)
        self.assertEqual(result["metrics"]["normal_tasks"], 0)
        self.assertEqual(next(c for c in result["cases"] if c["id"] == "production-authentication")["status"], "failed")

    def test_concurrency_is_bounded_and_measured_not_copied_from_settings(self):
        active, maximum = 0, 0
        lock = threading.Lock()
        def handler(request):
            nonlocal active, maximum
            with lock: active += 1; maximum = max(maximum, active)
            try:
                time.sleep(.16)
                return self.handler(request)
            finally:
                with lock: active -= 1
        config = self.config(max_requests=8, duration_seconds=1, concurrency=2, requests_per_minute=600)
        config.pop("_clock")
        result = self.run_fixture(config, handler)
        self.assertEqual(maximum, 2)
        self.assertEqual(result["metrics"]["max_concurrency"], maximum)
        self.assertLess(result["metrics"]["duration_seconds"], 2)

    def test_cancel_interrupts_paced_wait_without_consuming_another_attempt(self):
        cancelled = threading.Event()
        config = self.config(duration_seconds=60, max_requests=3)
        config.pop("_clock")
        calls = []
        def handler(request): calls.append(request); return self.handler(request)
        config["transport"] = httpx.MockTransport(handler)
        timer = threading.Timer(.08, cancelled.set)
        timer.start()
        try: result = p.run(config, cancelled=cancelled)
        finally: timer.cancel()
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["status"], "cancelled")
        self.assertLess(result["metrics"]["duration_seconds"], .5)

    def test_request_rate_budget_ends_before_duration_without_fabricated_coverage(self):
        result = self.run_fixture(self.config(duration_seconds=3600, max_requests=5, requests_per_minute=600))
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["stop_reason"], "request_budget")
        self.assertLess(result["metrics"]["observation_seconds"], 1)

    def test_custom_body_cannot_change_model_host_or_auth(self):
        for body in ({"url": "https://other.test"}, {"model": "other"}, {"headers": {"authorization": "x"}}, {"endpoint": "https://other.test/v1/messages"}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                p.configuration(self.config(workloads=["custom"], custom_cases=[{"body": {"messages": [{"role": "user", "content": "hello"}], **body}, "expect": {"exact_text": "ok"}}]))

    def test_literal_and_media_urls_are_data_not_transport_overrides(self):
        prompt = [{"type": "text", "text": "Summarize https://example.test/reference"}, {"type": "image_url", "image_url": {"url": "https://images.test/fixture.png"}}]
        config = self.config(fmt="openai", workloads=["custom"], custom_cases=[{"body": {"messages": [{"role": "user", "content": prompt}]}, "expect": {"exact_text": "ok"}}])
        plan = p.build_plan(config)
        example = plan["workloads"][0]["example"]
        self.assertEqual(example["url"], "https://relay.test/proxy/v2/chat/completions")
        self.assertEqual(example["body"]["messages"][0]["content"], prompt)

    def test_scheduler_grace_is_not_an_extra_load_phase(self):
        result = self.run_fixture(self.config(duration_seconds=1, max_requests=100, requests_per_minute=600))
        self.assertEqual(result["metrics"]["attempted_requests"], 11)
        self.assertAlmostEqual(result["metrics"]["observation_seconds"], 1, places=5)
        self.assertEqual(result["stop_reason"], "duration_reached")

    def test_cumulative_evidence_bound_stops_new_requests_and_preserves_evidence(self):
        with patch.object(p, "MAX_RETAINED_EVIDENCE_BYTES", 1):
            result = self.run_fixture(self.config(max_requests=5))
        self.assertEqual(len(result["samples"]), 1)
        self.assertEqual(result["stop_reason"], "evidence_budget")
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(result["samples"][0]["response"]["body"])

    def test_baseline_and_normal_success_rates_have_distinct_denominators(self):
        result = self.run_fixture(self.config())
        self.assertEqual(len(result["samples"]), 8)
        metrics = result["metrics"]
        self.assertEqual((metrics["normal_tasks"], metrics["business_successful"], metrics["normal_attempted_requests"]), (6, 6, 6))
        self.assertEqual(metrics["business_success_rate"], 1)
        self.assertEqual(metrics["protocol_success_rate"], 1)
        self.assertEqual(metrics["max_concurrency"], 1)
        self.assertNotIn("fixture-secret", json.dumps(result))
        self.assertTrue(all(s["task_id"] and s["attempt"] == 1 for s in result["samples"]))
        self.assertEqual(result["status"], "completed")
        self.assertTrue(metrics["observation_complete"])

    def test_all_builtin_business_workloads_both_protocols(self):
        for fmt in ("anthropic", "openai"):
            with self.subTest(fmt=fmt):
                config = self.config(fmt=fmt, workloads=["long_output", "long_context", "stream", "thinking", "vision", "tools"], max_requests=10, output_tokens=256)
                result = self.run_fixture(config)
                self.assertEqual(result["metrics"]["attempted_requests"], 10)
                self.assertEqual(result["metrics"]["business_success_rate"], 1)
                self.assertEqual(result["metrics"]["normal_tasks"], 6)
                tools = [s for s in result["samples"] if s["workload"] == "tools"]
                self.assertEqual(len(tools), 3)
                self.assertEqual(len({s["task_id"] for s in tools}), 1)
                if fmt == "anthropic":
                    self.assertIn("preserve-exact-signature", json.dumps(tools[-1]["request"]["body"]))

    def test_failed_baseline_does_not_start_bulk_load(self):
        for status in (401, 400):
            calls = []
            def handler(request): calls.append(request); return httpx.Response(status, json={"error": {"message": "fixture error"}})
            result = self.run_fixture(self.config(), handler)
            self.assertEqual(len(calls), 1)
            self.assertIn(result["stop_reason"], ("authentication", "baseline_failed"))

    def test_retry_counts_attempt_budget_and_preserves_original_failure(self):
        calls = []
        def handler(request):
            calls.append(request)
            if len(calls) == 3: return httpx.Response(503, json={"error": {"message": "transient"}})
            return self.handler(request)
        result = self.run_fixture(self.config(max_requests=6), handler)
        self.assertEqual(len(calls), 6)
        self.assertEqual(result["metrics"]["attempted_requests"], 6)
        self.assertEqual(result["metrics"]["normal_tasks"], 3)
        self.assertEqual(result["metrics"]["first_attempt_successful"], 2)
        self.assertEqual(result["metrics"]["business_successful"], 3)
        self.assertEqual(result["metrics"]["protocol_success_rate"], .75)
        self.assertEqual(result["metrics"]["retry_attempts"], 1)
        self.assertEqual(result["samples"][2]["response"]["status"], 503)
        self.assertEqual(result["samples"][3]["attempt"], 2)

    def test_retry_after_cannot_exceed_remaining_budget(self):
        calls = []
        def handler(request): calls.append(request); return httpx.Response(429, headers={"retry-after": "1000"}, json={"error": "slow down"})
        result = self.run_fixture(self.config(duration_seconds=10), handler)
        self.assertEqual(len(calls), 1)
        self.assertIn("retry_skipped", result["samples"][0]["evidence"])

    def test_successful_byte_stream_then_error_is_never_retried(self):
        count = 0
        def handler(request):
            nonlocal count
            count += 1
            if count <= 2: return self.handler(request)
            body = json.loads(request.content)
            response = reply(body, text="started")
            raw = response.content
            raw = raw[:raw.index(b"event: message_delta")] + b'event: error\ndata: {"type":"error","error":{"message":"unavailable"}}\n\n'
            return httpx.Response(200, content=raw)
        result = self.run_fixture(self.config(workloads=["stream"], max_requests=4), handler)
        self.assertEqual(count, 4)
        self.assertEqual(result["metrics"]["retry_attempts"], 0)
        self.assertIsNotNone(result["samples"][2]["evidence"]["first_content_ms"])

    def test_estimated_budget_is_checked_before_every_attempt(self):
        calls = []
        def handler(request): calls.append(request); return self.handler(request)
        result = self.run_fixture(self.config(max_estimated_tokens=0), handler)
        self.assertEqual(calls, [])
        self.assertEqual(result["stop_reason"], "estimated_token_budget")
        self.assertEqual(result["metrics"]["attempted_requests"], 0)

    def test_tool_budget_partial_task_is_not_success(self):
        result = self.run_fixture(self.config(workloads=["tools"], max_requests=4))
        self.assertEqual(result["metrics"]["attempted_requests"], 4)
        self.assertEqual(result["metrics"]["business_successful"], 0)
        task = next(x for x in result["tasks"] if x["workload"] == "tools")
        self.assertEqual(task["rounds_completed"], 2)
        self.assertFalse(task["business_success"])
        self.assertFalse(task["completed"])

    def test_no_eval_or_execution_of_unexpected_model_tool(self):
        count = 0
        def handler(request):
            nonlocal count
            count += 1
            if count <= 2: return self.handler(request)
            return reply(json.loads(request.content), tools=[{"id": "call", "name": "Calculator", "input": {"expr": "__import__('os').system('bad')"}}])
        result = self.run_fixture(self.config(workloads=["tools"], max_requests=3), handler)
        self.assertEqual(count, 3)
        self.assertEqual(result["metrics"]["business_successful"], 0)
        self.assertIn("未执行", result["samples"][-1]["evidence"]["business_assertion"])

    def test_client_cancel_is_control_and_preserves_evidence_boundary(self):
        streams = []
        def handler(request):
            body = json.loads(request.content)
            if not body.get("stream"): return self.handler(request)
            raw = reply(body, text="visible output").content
            frames = [part + b"\n\n" for part in raw.split(b"\n\n") if part]
            stream = ChunkStream(frames)
            streams.append(stream)
            return httpx.Response(200, stream=stream)
        result = self.run_fixture(self.config(workloads=["cancellation"], max_requests=3), handler)
        sample = result["samples"][-1]
        self.assertEqual(sample["termination"], "client_cancel_probe")
        self.assertTrue(streams[0].closed)
        self.assertTrue(sample["evidence"]["client_response_closed"])
        self.assertFalse(sample["evidence"]["upstream_cancellation_confirmed"])
        self.assertEqual(result["metrics"]["normal_tasks"], 0)
        self.assertEqual(result["metrics"]["cancellation"]["client_closed"], 1)

    def test_effective_content_timing_excludes_ping_and_thinking(self):
        def frame(kind, value): return ('event: %s\ndata: %s\n\n' % (kind, json.dumps({"type": kind, **value}))).encode()
        chunks = [frame("ping", {}), frame("message_start", {"message": {"id": "fixture", "model": "fixture", "usage": {"input_tokens": 1, "output_tokens": 0}}}), frame("content_block_start", {"index": 0, "content_block": {"type": "thinking", "thinking": ""}}), frame("content_block_delta", {"index": 0, "delta": {"type": "thinking_delta", "thinking": "private reasoning"}}), frame("content_block_stop", {"index": 0}), frame("content_block_start", {"index": 1, "content_block": {"type": "text", "text": ""}}), frame("content_block_delta", {"index": 1, "delta": {"type": "text_delta", "text": "visible"}}), frame("content_block_stop", {"index": 1}), frame("message_delta", {"delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}}), frame("message_stop", {})]
        settings = p.configuration(self.config())
        sample = core._collect_sample({"id": "timing", "probe": "sse", "body": {"stream": True}}, settings, "key", httpx.MockTransport(lambda request: httpx.Response(200, stream=ChunkStream(chunks, delay=.006))), None)
        self.assertGreater(sample["evidence"]["first_content_ms"], sample["evidence"]["first_byte_ms"] + 20)
        self.assertEqual(sample["evidence"]["content_chars"], 7)
        self.assertEqual(sample["evidence"]["content_event_count"], 1)

    def test_cancelled_before_start_makes_no_requests(self):
        event = threading.Event(); event.set()
        config = self.config(); config["transport"] = httpx.MockTransport(lambda request: self.fail("must not call"))
        result = p.run(config, cancelled=event)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["metrics"]["attempted_requests"], 0)

    def test_time_buckets_use_actual_samples_and_do_not_fill_idle_tail(self):
        result = self.run_fixture(self.config(max_requests=4, requests_per_minute=60))
        self.assertEqual(len(result["metrics"]["time_buckets"]), 1)
        self.assertLess(result["metrics"]["observation_seconds"], 60)
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(all(b["normal_tasks"] > 0 for b in result["metrics"]["time_buckets"]))

    def test_every_attempt_emits_request_start_including_retry_and_tools(self):
        count, events = 0, []
        def handler(request):
            nonlocal count
            count += 1
            if count == 1: return httpx.Response(503, json={"error": "transient"})
            return self.handler(request)
        config = self.config(workloads=["tools"], max_requests=5); config["transport"] = httpx.MockTransport(handler)
        result = p.run(config, emit=events.append)
        starts = [e for e in events if e["type"] == "request_start"]
        self.assertEqual(len(starts), result["metrics"]["attempted_requests"])
        self.assertEqual(len({e["sample_id"] for e in starts}), len(starts))


if __name__ == "__main__": unittest.main()
