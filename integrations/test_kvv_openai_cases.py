"""Offline regression checks: independent probes + real pytest/recorder wiring."""
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from PIL import Image
import pytest
import kvv_openai_cases as probes


def usage(prompt=20, completion=2, cached=None, reasoning=None):
    result = dict(prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt+completion)
    if cached is not None:
        result["prompt_tokens_details"] = {"cached_tokens":cached}
    if reasoning is not None:
        result["completion_tokens_details"] = {"reasoning_tokens":reasoning}
    return result


def completion(text="OK", calls=None, accounting=None, finish=None):
    message = {"role":"assistant","content":text}
    if calls is not None:
        message["tool_calls"] = calls
    return {"id":"fixture-completion","object":"chat.completion","model":"fixture-model",
            "choices":[{"index":0,"message":message,"finish_reason":finish or ("tool_calls" if calls else "stop")}],
            "usage":accounting if accounting is not None else usage()}


def tool(name="Calculator", **args):
    return {"id":"call-" + name + "-" + str(next(iter(args.values()))), "type":"function",
            "function":{"name":name,"arguments":json.dumps(args)}}


def frames(*items):
    return "".join("data: " + (item if isinstance(item,str) else json.dumps(item)) + "\n\n" for item in items).encode()


class MockChannel:
    """Strict incoming protocol check; image answers are read from actual pixels."""
    def __init__(self):
        self.requests, self.prefixes = [], {}
    def __call__(self, request):
        payload = json.loads(request.content)
        self.requests.append(payload)
        assert request.method == "POST" and request.url.path == "/prefix/v1/chat/completions"
        assert "thinking" not in payload and "chat_template_kwargs" not in payload
        assert not ("max_tokens" in payload and "max_completion_tokens" in payload)
        assert all("tools" not in message and "reasoning_content" not in message for message in payload["messages"])
        def response(body, status=200):
            return httpx.Response(status,json=body)
        for name in ("max_tokens","temperature"):
            if payload.get(name,0) < 0:
                return response({"error":{"message":name + " is out of range"}},400)
        if payload["messages"][0]["role"] == "workbench_invalid_role":
            return response({"error":{"message":"Invalid messages role"}},422)
        if payload.get("stream"):
            return httpx.Response(200,headers={"Content-Type":"text/event-stream"},content=frames(
                {"choices":[{"index":0,"delta":{"role":"assistant","content":"OK"},"finish_reason":None}]},
                {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]},
                {"choices":[],"usage":usage()}, "[DONE]"))
        if payload.get("max_tokens") == 1 or payload.get("max_completion_tokens") == 1:
            return response(completion("O",accounting=usage(completion=1),finish="length"))
        messages = payload["messages"]
        if any(message["role"]=="tool" for message in messages):
            calls = messages[1]["tool_calls"]
            assert calls[0]["id"] == messages[2]["tool_call_id"]
            return response(completion("1081"))
        if payload.get("parallel_tool_calls"):
            return response(completion(None,[tool("WeatherQuery",city="Beijing"),tool("WeatherQuery",city="Shanghai")]))
        if payload.get("tool_choice")=="required" or isinstance(payload.get("tool_choice"),dict):
            return response(completion(None,[tool(expr="23 * 47")]))
        if payload.get("response_format"):
            value = {"ready":True}
            if payload["response_format"]["type"]=="json_schema":
                value["count"] = 3
            return response(completion(json.dumps(value)))
        content = messages[0].get("content")
        if isinstance(content,list):
            images = [part["image_url"]["url"] for part in content if part["type"]=="image_url"]
            if not images:
                return response(completion("A sample video shows objects moving across the screen."))
            colors = []
            for url in images:
                img = Image.open(BytesIO(base64.b64decode(url.split(",",1)[1]))).convert("RGB")
                colors.append([("red","green","blue")[max(range(3),key=lambda i:img.getpixel((100+x*195,120))[i])] for x in range(3)])
            return response(completion(json.dumps(colors[0] if len(colors)==1 else colors)))
        if str(content).startswith("CACHE OBSERVATION"):
            seen = self.prefixes.get(content,0)
            self.prefixes[content] = seen+1
            return response(completion("READY",accounting=usage(prompt=2500,cached=2048 if seen else 0)))
        if "reasoning_effort" in payload:
            return response(completion("12",accounting=usage(completion=12,reasoning=8)))
        return response(completion())


class OpenAIProbeTests(unittest.TestCase):
    def client(self, callback):
        return httpx.Client(base_url="https://fixture.test/prefix/v1/",transport=httpx.MockTransport(callback))

    def test_exported_contract_and_every_full_probe(self):
        names = {name for name in vars(probes) if name.startswith("test_openai_")}
        self.assertEqual(names,set(probes.OPENAI_CASE_SPECS))
        self.assertEqual(len(probes.PRECHECK),11)
        self.assertEqual(len(set(probes.PRECHECK)),11)
        self.assertTrue(set(probes.PRECHECK) <= names)
        for spec in probes.OPENAI_CASE_SPECS.values():
            self.assertEqual(set(spec),{"title","method","expected","category","next_step","dimensions"})
            self.assertTrue(all(isinstance(spec[name],str) and spec[name] for name in ("title","method","expected","category","next_step")))
            self.assertTrue(set(spec["dimensions"]) <= {"multimodal","tools","max_tokens","cache","protocol","reliability"})
        channel = MockChannel()
        with self.client(channel) as client:
            for name in sorted(names):
                with self.subTest(name=name):
                    getattr(probes,name)(client,"fixture-model")
        self.assertEqual(len(channel.requests),len(names)+2, "only cache and tool roundtrip add one request each")
        self.assertEqual(len(channel.prefixes),1)
        self.assertEqual(next(iter(channel.prefixes.values())),2)

    def test_fixture_contains_real_color_groundtruth_and_no_prompt_leak(self):
        channel = MockChannel()
        with self.client(channel) as client:
            probes.test_openai_image_groundtruth(client,"fixture")
            probes.test_openai_multi_image_groundtruth(client,"fixture")
        for payload in channel.requests:
            question = payload["messages"][0]["content"][0]["text"].lower()
            for name in ("red","green","blue"):
                self.assertNotIn(name,question)
        self.assertNotEqual(channel.requests[1]["messages"][0]["content"][1]["image_url"]["url"],
                            channel.requests[1]["messages"][0]["content"][2]["image_url"]["url"])

    def test_wrong_image_answer_is_failure(self):
        with self.client(lambda _: httpx.Response(200,json=completion('["blue","green","red"]'))) as client:
            with self.assertRaises(AssertionError):
                probes.test_openai_image_groundtruth(client,"fixture")

    def test_token_limits_need_usage_and_reject_overruns(self):
        body = completion("a long visible string that is not counted by characters")
        body.pop("usage")
        with self.assertRaises(pytest.skip.Exception):
            probes._token_limit(body)
        probes._token_limit(completion("very long visible string",accounting=usage(completion=1)))
        for value in (2,True,-1,"1"):
            with self.subTest(value=value), self.assertRaises(AssertionError):
                probes._token_limit(completion("O",accounting={"completion_tokens":value}))

    def test_usage_rejects_boolean_counts_bad_sums_and_details(self):
        for value in (dict(prompt_tokens=True,completion_tokens=1,total_tokens=2),
                      dict(prompt_tokens=1,completion_tokens=1,total_tokens=3),
                      usage(cached=21),usage(reasoning=3)):
            with self.subTest(value=value), self.assertRaises(AssertionError):
                probes._usage({"usage":value})
        probes._usage({"usage":usage(cached=2,reasoning=1)})

    def test_negative_probes_do_not_accept_auth_or_unrelated_rejections(self):
        for status, message in ((401,"max_tokens unauthorized"),(429,"max_tokens rate limit"),(400,"model not found")):
            with self.subTest(status=status), self.assertRaises(AssertionError):
                probes._rejected(httpx.Response(status,json={"error":{"message":message}}),("max_tokens",))
        probes._rejected(httpx.Response(422,json={"error":{"message":"max_tokens must be >=1"}}),("max_tokens",))

    def test_optional_features_only_skip_attributed_unsupported_errors(self):
        response = httpx.Response(400,json={"error":{"message":"reasoning_effort is not supported"}})
        with self.assertRaises(pytest.skip.Exception):
            probes._optional(response,("reasoning_effort",))
        for status in (401,429,500):
            probes._optional(httpx.Response(status,json={"error":{"message":"reasoning_effort unsupported"}}),("reasoning_effort",))
        probes._optional(httpx.Response(400,json={"error":{"message":"invalid model"}}),("reasoning_effort",))

    def test_required_tool_needs_real_structured_call(self):
        for body in (completion("I would call Calculator"),completion(None,[]),
                     completion(None,[tool(expr="")] ),completion(None,[tool(expr="23*47"),tool(expr="23*47")])):
            with self.subTest(body=body), self.assertRaises(AssertionError):
                probes._calls(body,"Calculator")
        probes._calls(completion(None,[tool(expr="23 * 47")]),"Calculator")
        with self.assertRaises(AssertionError):
            probes._calls(completion(None,[tool(expr="23*47"),tool("WeatherQuery",city="Beijing")]),"Calculator",allowed={"Calculator"})

    def test_cache_zero_or_missing_and_invalid_values_not_claimed_as_hits(self):
        for accounting in (usage(prompt=2500),usage(prompt=2500,cached=0)):
            with self.client(lambda _:httpx.Response(200,json=completion("READY",accounting=accounting))) as client:
                with self.assertRaises(pytest.skip.Exception):
                    probes.test_openai_cache_repeat(client,"fixture")
        for value in (-1,True,3000):
            with self.assertRaises(AssertionError):
                probes._cache_reads(usage(prompt=2500,cached=value))

    def test_reasoning_without_usage_evidence_is_not_a_pass(self):
        with self.client(lambda _:httpx.Response(200,json=completion("12"))) as client:
            with self.assertRaises(pytest.skip.Exception):
                probes.test_openai_reasoning_effort(client,"fixture")

    def test_sse_failures_are_visible(self):
        head = {"choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}]}
        count = {"choices":[],"usage":usage()}
        cases = [frames(head,count), frames(head,count,"[DONE]","[DONE]"),
                 frames(head,count,"[DONE]",head), frames(head,{"error":{"message":"upstream broke"}},"[DONE]"),
                 frames(head,count,"[DONE]") + b"data: {}",
                 frames(head,{"choices":[],"usage":usage(completion=True)},"[DONE]"),
                 b"data: {broken}\n\n" + frames("[DONE]")]
        for data in cases:
            with self.subTest(data=data), self.client(lambda _:httpx.Response(200,headers={"content-type":"text/event-stream"},content=data)) as client:
                with self.assertRaises((AssertionError,pytest.fail.Exception)):
                    probes._sse(client,probes._payload("fixture",stream=True))

    def test_secrets_are_redacted_in_diagnostics(self):
        with patch.dict(os.environ,{"KIMI_API_KEY":"fixture-secret-only"}):
            self.assertNotIn("fixture-secret-only",probes._safe("error fixture-secret-only"))

    def test_passed_call_properties_contain_bounded_observations(self):
        properties = []
        fixture = probes._record_observations.__wrapped__(lambda name,value:properties.append((name,value)))
        next(fixture)
        try:
            self.assertEqual(properties,[("workbench_observations",[])])
            with self.client(MockChannel()) as client:
                probes.test_openai_usage_accounting(client,"fixture")
            self.assertTrue(any(row["kind"]=="usage" for row in properties[0][1]))
            for number in range(100):
                probes._observe("bounded",sample=number)
            self.assertEqual(len(properties[0][1]),40)
        finally:
            with self.assertRaises(StopIteration):
                next(fixture)
        self.assertIsNone(probes._OBSERVATIONS.get())


class PytestWiringTests(unittest.TestCase):
    def test_real_precheck_fixtures_and_transport_on_loopback(self):
        channel = MockChannel()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                payload = self.rfile.read(int(self.headers["content-length"]))
                try:
                    result = channel(httpx.Request("POST","https://fixture.test"+self.path,content=payload))
                    body = result.read()
                    self.send_response(result.status_code)
                    self.send_header("Content-Type",result.headers.get("content-type","application/json"))
                    self.send_header("Content-Length",str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError,ConnectionResetError):
                    pass
        server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
        worker = threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        root = Path(__file__).resolve().parent
        try:
            with tempfile.TemporaryDirectory() as directory:
                env = os.environ.copy()
                env.update(KIMI_BASE_URL=f"http://127.0.0.1:{server.server_port}/prefix/v1",
                           KIMI_API_KEY="local-fixture-key",MODEL_NAME="fixture-model",
                           WORKBENCH_EVENTS=str(Path(directory)/"events.jsonl"),
                           WORKBENCH_REQUEST_TIMEOUT="10",PYTHONPATH=str(root))
                nodes = [str(root/"kvv_openai_cases.py")+"::"+name for name in probes.PRECHECK]
                run = subprocess.run([sys.executable,"-m","pytest","-p","kvv_progress","-q","-o","addopts=",*nodes],
                                     cwd=directory,env=env,capture_output=True,text=True,timeout=40)
                self.assertEqual(run.returncode,0,run.stdout+run.stderr)
                events = [json.loads(line) for line in (Path(directory)/"events.jsonl").read_text().splitlines()]
                rows = [event for event in events if event["type"]=="case"]
                self.assertEqual(len(rows),11)
                self.assertTrue(all(event["status"]=="passed" for event in rows))
                summary = json.loads((Path(directory)/"transport-summary.json").read_text())
                self.assertEqual(summary["request_count"],12)
                self.assertEqual(summary["completed_requests"],12)
                evidence = (Path(directory)/"requests.jsonl").read_text()
                self.assertNotIn("local-fixture-key",evidence)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
