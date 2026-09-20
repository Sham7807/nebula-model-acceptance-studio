"""Report semantics regressions; only synthetic and stored offline evidence."""

import ast
from copy import deepcopy
import json
from pathlib import Path
import unittest

try:
    from integrations import report_content as content
except ImportError:
    import report_content as content


ROOT = Path(__file__).resolve().parent


def case(node, status="passed", detail="", **extra):
    return {"id": node, "status": status, "detail": detail, **extra}


def kvv(cases, requests=None, **extra):
    return {"suite": "kvv11", "status": "completed", "cases": cases,
            "summary": {"total": len(cases), "completed": len(cases)},
            "transport": {"requests": requests or [], "checks": []}, **extra}


class ReportContentTests(unittest.TestCase):
    def test_score_dimensions_do_not_infer_tools_or_max_tokens_from_generic_params(self):
        nodes = [
            "tests/params/test_params.py::test_no_param_succeeds[thinking]",
            "tests/params/test_params.py::test_wrong_param_rejected[thinking-temperature=1.1]",
            "tests/k3_features/test_response_format.py::test_json_object[nostream]",
        ]
        report = content.build_report_data(kvv([case(nodes[0]), case(nodes[1], "failed"), case(nodes[2])]))
        score = {row["id"]: row for row in report["score"]["dimensions"]}
        self.assertEqual(score["tools"]["covered"], 0)
        self.assertEqual(score["max_tokens"]["covered"], 0)
        self.assertEqual(score["protocol"]["covered"], 3)

    def test_k3_calculator_probes_are_tools_and_json_output_is_protocol(self):
        prefix = "tests/k3_features/test_workbench_capabilities.py::"
        nodes = [
            prefix + "test_k3_dynamic_tool_in_system_calculator[nostream]",
            prefix + "test_k3_top_level_tool_calculator[nostream]",
            prefix + "test_k3_dynamic_tool_required[nostream]",
            prefix + "test_k3_dynamic_and_top_level_tools_coexist[nostream]",
            "tests/k3_features/test_response_format.py::test_json_object[nostream]",
        ]
        report = content.build_report_data(kvv([case(node) for node in nodes]))
        score = {row["id"]: row for row in report["score"]["dimensions"]}
        self.assertEqual(score["tools"]["covered"], 4)
        self.assertEqual(score["protocol"]["covered"], 1)

    def test_prompt_token_baseline_is_usage_evidence_not_cache_hit_proof(self):
        node = "tests/prompt_tokens/test_prompt_tokens.py::test_prompt_tokens_match_groundtruth[assistant_hello]"
        report = content.build_report_data(kvv([case(node, "passed")]))
        check = report["checks"][0]
        self.assertEqual(check["metadata"]["dimensions"], ["cache", "token_accounting"])
        self.assertIn("usage token 基线", check["metadata"]["dimension_note"])
        self.assertIn("不等于缓存", check["meaning"])

    def test_only_missing_statuses_leave_dimension_uncovered(self):
        node = "tests/k3_features/test_workbench_capabilities.py::test_k3_video_url_multimodal[nostream]"
        report = content.build_report_data(kvv([case(node, "skipped")]))
        dimension = next(row for row in report["score"]["dimensions"] if row["id"] == "multimodal")
        self.assertEqual(dimension["covered"], 0)
        self.assertEqual(dimension["status"], "not_covered")
        self.assertEqual(dimension["score"], 0)

    def test_cc_advanced_checks_have_professional_scope_and_limitations(self):
        source = {"suite": "ccmax_acceptance", "configuration": {"advanced": True},
                  "summary": {"total": 1, "completed": 1},
                  "checks": [{"id": "prompt_injection", "status": "failed"},
                             {"id": "instruction_hierarchy", "status": "passed"},
                             {"id": "behavioral_consistency", "status": "failed"},
                             {"id": "parameter_validation", "status": "passed"}],
                  "samples": []}
        report = content.build_report_data(source)
        rows = {row["id"]: row for row in report["checks"]}
        self.assertIn("金丝雀", rows["prompt_injection"]["method"])
        self.assertIn("蒸馏", rows["behavioral_consistency"]["meaning"])
        self.assertIn("HTTP 400", rows["parameter_validation"]["expected"])

    def test_cc_denominators_preserve_uncovered_and_inconclusive_records(self):
        samples = []
        for i, status in enumerate(("passed", "failed", "inconclusive", "not_covered"), 1):
            samples.append({"id": "sse-%s" % i, "response": {"status": 200}, "termination": "timeout" if status == "inconclusive" else "eof",
                            "evidence": {"request_ids": [{"header": "x-request-id", "value": "up-%s" % i}], "sse": {"tools": [{"name": "echo", "input": {"token": i}}]}},
                            "assessments": [{"check": "tool_stream", "status": status, "detail": "result-%s" % i}]})
        source = {"suite": "ccmax_acceptance", "samples": samples, "checks": [{"id": "tool_stream", "status": "failed"}]}
        check = next(item for item in content.build_report_data(source)["checks"] if item["id"] == "tool_stream")
        self.assertIn("通过 1/4 条", check["observed"])
        self.assertEqual(check["counts"]["not_covered"], 1)
        self.assertEqual(check["counts"]["inconclusive"], 1)
        self.assertEqual(check["request_ids"], ["up-1", "up-2", "up-3", "up-4"])
        self.assertIn('"token": 4', check["observed"])

    def test_cc_response_eof_is_distinct_from_tcp_disconnect(self):
        source = {"suite": "ccmax_acceptance", "configuration": {"close_grace": 3}, "checks": [{"id": "connection", "status": "failed"}],
                  "samples": [{"id": "tool-1", "response": {"status": 200}, "termination": "connection_grace_exceeded", "duration_ms": 7001,
                               "evidence": {"after_stop_ms": 5017, "transport_error": {"type": "ReadTimeout"}},
                               "assessments": [{"check": "connection", "status": "failed", "detail": "超过宽限"}]}]}
        check = next(item for item in content.build_report_data(source)["checks"] if item["id"] == "connection")
        self.assertIn("不要求强制关闭底层 TCP", check["expected"])
        self.assertIn("3 秒", check["expected"])
        self.assertIn("5017", check["observed"])
        self.assertIn("ReadTimeout", check["observed"])
        self.assertIn("响应流结束失败 1/1 条", check["observed_summary"])
        self.assertIn("after_stop_ms 范围 5017–5017", check["observed_summary"])
        self.assertIn("HTTP 分布：200×1", check["observed_summary"])

    def test_cc_summary_is_bounded_without_truncating_original_evidence(self):
        detail = "完整原始断言" * 300
        source = {"suite": "ccmax_acceptance", "checks": [{"id": "tool_stream", "status": "failed"}],
                  "samples": [{"id": "sse-1", "response": {"status": 200}, "termination": "eof", "evidence": {"sse": {"tools": [{"name": "tool" * 500}] }},
                               "assessments": [{"check": "tool_stream", "status": "failed", "detail": detail}]}]}
        report = content.build_report_data(source)
        check = next(item for item in report["checks"] if item["id"] == "tool_stream")
        finding = next(item for item in report["findings"] if item["check_id"] == "tool_stream")
        self.assertEqual(report["title"], "CCMax渠道验收报告")
        self.assertLessEqual(len(check["observed_summary"]), 400)
        self.assertIn("通过 0/1 条；失败 1；无法判定 0", check["observed_summary"])
        self.assertIn(detail, check["observed"])
        self.assertEqual(finding["observation_summary"], check["observed_summary"])
        self.assertEqual(finding["observation"], check["observed"])

    def test_cc_error_body_and_usage_values_come_from_evidence(self):
        source = {"suite": "ccmax_acceptance", "checks": [{"id": "error_format", "status": "failed"}, {"id": "usage_cache", "status": "passed"}],
                  "samples": [{"id": "invalid-1", "response": {"status": 503, "body": json.dumps({"error": {"code": "model_not_found", "message": "missing model"}})},
                               "evidence": {}, "assessments": [{"check": "error_format", "status": "failed", "detail": "HTTP mapping"}]},
                              {"id": "sse-1", "response": {"status": 200}, "evidence": {"sse": {"usage": [{"source": "message_delta", "value": {"output_tokens": 713}}]}},
                               "assessments": [{"check": "usage_cache", "status": "passed", "detail": "usage checked"}]}]}
        checks = {item["id"]: item for item in content.build_report_data(source)["checks"]}
        self.assertIn("model_not_found", checks["error_format"]["observed"])
        self.assertIn("503", checks["error_format"]["observed"])
        self.assertIn("713", checks["usage_cache"]["observed"])
        self.assertIn("没有核对账单", checks["usage_cache"]["meaning"])

    def test_kvv_parameter_values_are_specific_to_each_node(self):
        prefix = "tests/params/test_params.py::test_wrong_param_rejected"
        report = content.build_report_data(kvv([case(prefix + "[thinking-temperature=2.0]", "failed"), case(prefix + "[non-thinking-top_p=0.8]", "inconclusive", "ReadTimeout")]))
        first, second = report["checks"]
        self.assertEqual(first["metadata"]["parameter"], {"name": "temperature", "value": "2.0"})
        self.assertEqual(second["metadata"]["parameter"], {"name": "top_p", "value": "0.8"})
        self.assertIn("thinking-temperature=2.0", first["method"])
        self.assertIn("超时", second["expected"])
        self.assertIn("不能记为通过", second["meaning"])

    def test_kvv_baseline_titles_distinguish_thinking_branches(self):
        prefix = "tests/params/test_params.py::test_no_param_succeeds"
        checks = content.build_report_data(kvv([case(prefix + "[non-thinking]"), case(prefix + "[thinking]")]))["checks"]
        self.assertNotEqual(checks[0]["title"], checks[1]["title"])
        self.assertIn("非思考（non-thinking）", checks[0]["title"])
        self.assertIn("思考（thinking）", checks[1]["title"])

    def test_kvv_schema_mode_and_exact_case_id_are_retained(self):
        node = "tests/tool_call_json_schema/test_tool_call_json_schema.py::test_tool_call_schema_matches_case_schema[TestAdditionalProperties:12:non-stream]"
        check = content.build_report_data(kvv([case(node, "failed", "E AssertionError: arguments validation failed: tool call arguments are missing")]))["checks"][0]
        self.assertEqual(check["id"], node)
        self.assertEqual(check["metadata"]["schema_reference"], {"suite": "TestAdditionalProperties", "line": 12, "mode": "non-stream"})
        self.assertIn("额外属性", check["title"])
        self.assertIn("不能替代 tool_calls", check["expected"])
        self.assertIn("arguments are missing", check["observed"])

    def test_token_range_is_extracted_without_inventing_missing_baseline(self):
        prefix = "tests/prompt_tokens/test_prompt_tokens.py::test_prompt_tokens_match_groundtruth"
        source = kvv([case(prefix + "[one]", "failed", "E AssertionError: case one: prompt_tokens outside accepted range: got 86, expected [99, 102] (diff -13)"),
                      case(prefix + "[two]", "passed"), case("tests/k3_features/test_tokenization_groundtruth.py::test_prompt_tokens_match_groundtruth", "failed", "E AssertionError: prompt_tokens=211, expected=204")])
        a, b, c = content.build_report_data(source)["checks"]
        self.assertEqual(a["metadata"]["token_comparison"]["difference_from_minimum"], -13)
        self.assertIn("[99, 102]", a["expected"])
        self.assertNotIn("token_comparison", b["metadata"])
        self.assertIn("不推测具体范围", b["expected"])
        self.assertEqual(c["metadata"]["token_comparison"]["maximum"], 204)
        self.assertIn("不能据此认定计费作弊", a["meaning"])

    def test_requests_bind_only_to_matching_case_and_preserve_partial_stream_assertion(self):
        nodes = ["tests/prompt_tokens/test_prompt_tokens.py::test_prompt_tokens_match_groundtruth[%s]" % name for name in ("a", "b")]
        requests = [{"case_id": nodes[0], "request_id": "request-a", "http_status": 200, "termination": "recorder_closed", "upstream_request_ids": [{"header": "x-request-id", "value": "up-a"}]},
                    {"case_id": nodes[1], "request_id": "request-b", "http_status": 500, "infrastructure_error": True}]
        source = kvv([case(nodes[0], "failed", "E AssertionError: got 9, expected [10, 13]", request_ids=["request-b"]), case(nodes[1], "inconclusive", "upstream failed")], requests)
        a, b = content.build_report_data(source)["checks"]
        self.assertEqual(a["status"], "failed")
        self.assertEqual(a["request_ids"], ["request-a"])
        self.assertEqual(a["upstream_request_ids"], ["up-a"])
        self.assertNotIn("request-b", a["observed"])
        self.assertEqual(b["status"], "inconclusive")

    def test_full_suite_retains_every_node_and_separates_local_and_skipped(self):
        cases = [case("tests/prompt_tokens/test_prompt_tokens.py::test_prompt_tokens_match_groundtruth[case-%s]" % i,
                      "skipped" if i < 7 else "inconclusive" if i == 7 else "failed" if i == 8 else "passed") for i in range(607)]
        cases += [case("tests/prompt_tokens/test_prompt_tokens.py::test_prompt_token_tolerance_boundaries[%s]" % i) for i in range(4)]
        report = content.build_report_data(kvv(cases, suite="kvvfull"))
        self.assertEqual(len(report["checks"]), 611)
        self.assertEqual([check["id"] for check in report["checks"]], [item["id"] for item in cases])
        self.assertEqual(report["remote_case_counts"]["total"], 607)
        self.assertEqual(report["remote_case_counts"]["passed"], 598)
        self.assertEqual(report["remote_case_counts"]["skipped"], 7)
        self.assertEqual(report["local_case_counts"]["passed"], 4)
        self.assertTrue(all(item["local_only"] for item in report["checks"][-4:]))

    def test_inconsistent_rejection_pass_is_flagged_without_mutating_verdict(self):
        node = "tests/params/test_params.py::test_wrong_param_rejected[thinking-n=2]"
        source = kvv([case(node)], [{"case_id": node, "request_id": "r1", "infrastructure_error": True, "termination": "total_timeout"}])
        before = deepcopy(source)
        report = content.build_report_data(source)
        self.assertEqual(source, before)
        self.assertEqual(report["checks"][0]["status"], "passed", "semantic layer does not rewrite stored verdicts")
        self.assertIn("不能证明参数被正确拒绝", report["checks"][0]["meaning"])
        self.assertEqual(report["findings"][0]["status"], "inconclusive")

    def test_plain_strings_leave_html_escaping_to_renderer_and_raw_is_copied(self):
        node = "tests/params/test_params.py::test_no_param_succeeds[thinking]"
        source = kvv([case(node, "failed", 'E AssertionError: <img src=x onerror="bad()">')])
        before = deepcopy(source)
        report = content.build_report_data(source)
        check = report["checks"][0]
        for field in ("id", "title", "status", "method", "expected", "observed", "meaning", "next_step"):
            self.assertIsInstance(check[field], str)
        self.assertIn("<img", check["observed"])
        self.assertNotIn("&lt;", check["observed"])
        check["raw"]["case"]["detail"] = "changed copy"
        self.assertEqual(source, before)
        json.dumps(report, ensure_ascii=False)

    def test_all_installed_k3_function_names_have_specific_chinese_metadata(self):
        directory = ROOT / "Kimi-Vendor-Verifier" / "tests" / "k3_features"
        if not directory.exists():
            self.skipTest("official source checkout not installed")
        for file in directory.glob("test_*.py"):
            for function in ast.walk(ast.parse(file.read_text())):
                if isinstance(function, ast.FunctionDef) and function.name.startswith("test_"):
                    node = "tests/k3_features/%s::%s" % (file.name, function.name)
                    with self.subTest(node=node):
                        metadata = content._kvv_metadata(node, "")
                        self.assertNotEqual(metadata[1], function.name)
                        self.assertRegex(metadata[1], r"[\u4e00-\u9fff]")

    def test_stored_redacted_cc57_and_kvv11_reports_are_read_only_regressions(self):
        paths = {"ccmax_acceptance": ROOT / "reports" / "edd9fc02a6c54a0e84e6d03029acadae" / "report.json",
                 "kvv11": ROOT / "reports" / "11ff7e5642b54282925951eb1b68318c" / "report.json"}
        if not all(path.is_file() for path in paths.values()):
            self.skipTest("stored integration reports are not available")
        results = {}
        for suite, path in paths.items():
            raw = path.read_bytes()
            source = json.loads(raw)
            original = deepcopy(source)
            results[suite] = content.build_report_data(source)
            self.assertEqual(source, original)
            self.assertEqual(path.read_bytes(), raw)
        cc = {row["id"]: row for row in results["ccmax_acceptance"]["checks"]}
        self.assertIn("通过 45/51 条", cc["connection"]["observed"])
        self.assertIn("90003", cc["connection"]["observed"])
        self.assertEqual(cc["stream_error"]["counts"]["inconclusive"], 6)
        self.assertEqual(cc["signature"]["counts"]["failed"], 5)
        self.assertIn("响应流结束失败 6/51 条", cc["connection"]["observed_summary"])
        self.assertIn("超关闭宽限 6/51 个样本", cc["connection"]["observed_summary"])
        self.assertIn("90003", cc["connection"]["observed_summary"])
        self.assertIn("HTTP 分布：200×51", cc["connection"]["observed_summary"])
        self.assertTrue(all(len(row["observed_summary"]) <= 400 for row in cc.values()))
        self.assertTrue(all("observation_summary" in finding for finding in results["ccmax_acceptance"]["findings"]))
        kvv_checks = results["kvv11"]["checks"]
        self.assertEqual(len(kvv_checks), 11)
        self.assertEqual(results["kvv11"]["remote_case_counts"]["failed"], 6)
        self.assertTrue(all(check["request_ids"] for check in kvv_checks))
        token = next(row for row in kvv_checks if row["id"].endswith("[k3_tool_required]"))
        self.assertIn("[207, 210]", token["expected"])
        self.assertIn("prompt_tokens=171", token["observed"])


if __name__ == "__main__":
    unittest.main()
