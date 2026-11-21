"""Offline Gemini transport tests and fixtures for published-governor integration."""

from copy import deepcopy
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ruleset_gemini", ROOT / "adapters/gemini-flash-lite/adapter.py")
gemini = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gemini)


class GeminiTests(unittest.TestCase):
    def setUp(self):
        sleep = patch.object(gemini.time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        self.verdict = {
            "verdict": "PASS",
            "reason": "Specific imperative summary",
            "findings": [{"path": None, "line": 1, "message": "The action and target are specific."}],
        }
        self.request = {
            "protocol_version": 1,
            "prompt": "Captured header only",
            "response_schema": {"type": "object"},
            "reviews": [{"id": "quality", "parameters": {"model": "gemini-3.5-flash-lite"}}],
        }

    def expected(self, code=0, verdict=None):
        document = dict(verdict or self.verdict)
        document.pop("verdict")
        return code, {"protocol_version": 1, **document}

    def envelope(self, verdict=None):
        return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(verdict or self.verdict)}]}}]}

    def response(self, raw):
        response = MagicMock()
        response.status = 200
        response.read.return_value = raw
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        return opener

    def test_protocol_zero_and_one_preserve_the_request_version_without_extra_calls(self):
        for version in (0, 1):
            for status, code in (("PASS", 0), ("FAIL", 1)):
                request = dict(self.request, protocol_version=version)
                verdict = {"reason": "Version preserved", "findings": []}
                with self.subTest(version=version, status=status), patch.object(gemini, "run", return_value=(status, verdict)) as run:
                    self.assertEqual(gemini.review(request), (code, {"protocol_version": version, **verdict}))
                    run.assert_called_once()

    def test_structured_schema_model_and_only_prepared_prompt_reach_gemini(self):
        opener = self.response(json.dumps(self.envelope()).encode())
        with patch.dict(os.environ, GEMINI_API_KEY="synthetic-key"), patch.object(gemini, "build_opener", return_value=opener):
            result = gemini.review(self.request)
        self.assertEqual(result, self.expected())
        sent = opener.open.call_args.args[0]
        self.assertEqual(sent.full_url, "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent")
        self.assertEqual(opener.open.call_args.kwargs, {"timeout": 20})
        payload = json.loads(sent.data)
        self.assertEqual(payload["contents"], [{"role": "user", "parts": [{"text": self.request["prompt"]}]}])
        schema = payload["generationConfig"]["responseJsonSchema"]
        self.assertEqual(schema, gemini.with_verdict(self.request["response_schema"]))
        self.assertEqual(schema["properties"]["verdict"]["enum"], ["PASS", "FAIL"])
        self.assertEqual(schema["required"], ["verdict"])
        self.assertEqual(self.request["response_schema"], {"type": "object"})
        self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(sent.get_header("X-goog-api-key"), "synthetic-key")
        self.assertNotIn("tools", payload)
        self.assertNotIn("synthetic-key", str(payload))

    def test_pass_and_fail_map_to_the_exit_status_and_anything_else_is_an_error(self):
        for status, code in (("PASS", 0), ("FAIL", 1)):
            verdict = deepcopy(self.verdict)
            verdict["verdict"] = status
            opener = self.response(json.dumps(self.envelope(verdict)).encode())
            with patch.dict(os.environ, GEMINI_API_KEY="synthetic-key"), patch.object(gemini, "build_opener", return_value=opener):
                self.assertEqual(gemini.review(self.request), self.expected(code, verdict))
            stdout = io.StringIO()
            with (
                patch.dict(os.environ, GEMINI_API_KEY="synthetic-key"),
                patch.object(gemini, "build_opener", return_value=opener),
                patch.object(sys, "stdin", io.StringIO(json.dumps(self.request))),
                patch.object(sys, "stdout", stdout),
                self.assertRaises(SystemExit) as exited,
            ):
                gemini.main()
            self.assertEqual(exited.exception.code, code)
            self.assertEqual(json.loads(stdout.getvalue()), self.expected(code, verdict)[1])
        for bad in ("ERROR", "pass", None):
            verdict = deepcopy(self.verdict)
            verdict["verdict"] = bad
            opener = self.response(json.dumps(self.envelope(verdict)).encode())
            with (
                self.subTest(verdict=bad),
                patch.dict(os.environ, GEMINI_API_KEY="synthetic-key"),
                patch.object(gemini, "build_opener", return_value=opener),
                self.assertRaises(gemini.ProviderError),
            ):
                gemini.review(self.request)

    def test_missing_credentials_http_errors_timeouts_and_redirects_never_pass(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(gemini, "build_opener") as opener:
            with self.assertRaisesRegex(gemini.ProviderError, "GEMINI_API_KEY"):
                gemini.review(self.request)
            opener.assert_not_called()
        for error in [
            HTTPError("https://example.invalid", code, "reason", {}, io.BytesIO(b"original-body")) for code in (302, 401, 429, 500)
        ]:
            opener = MagicMock()
            opener.open.side_effect = error
            with patch.dict(os.environ, GEMINI_API_KEY="synthetic-key"), patch.object(gemini, "build_opener", return_value=opener):
                with self.assertRaises(gemini.ProviderError) as raised:
                    gemini.review(self.request)
            if error.code not in gemini.RETRYABLE:
                self.assertEqual(raised.exception.detail, "original-body")
            self.assertEqual(opener.open.call_count, 3 if error.code in gemini.RETRYABLE else 1)
        for error in (TimeoutError("original details"), URLError("original details")):
            opener = MagicMock()
            opener.open.side_effect = error
            with patch.dict(os.environ, GEMINI_API_KEY="synthetic-key"), patch.object(gemini, "build_opener", return_value=opener):
                with self.assertRaises(gemini.ProviderError) as raised:
                    gemini.review(self.request)
            self.assertIn("original details", raised.exception.detail)
        self.assertIsNone(gemini.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.invalid"))

    def test_malformed_blocked_truncated_duplicate_and_oversized_responses_are_errors(self):
        malformed = [
            b"not JSON",
            b"x" * (gemini.LIMIT + 1),
            b'{"candidates":[],"candidates":[]}',
            json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}).encode(),
        ]
        for transform in (
            lambda e: e["candidates"][0].update(finishReason="MAX_TOKENS"),
            lambda e: e["candidates"].append(e["candidates"][0]),
            lambda e: e["candidates"][0]["content"].update(parts=[]),
            lambda e: e["candidates"][0]["content"]["parts"][0].update(thought=True),
            lambda e: e["candidates"][0]["content"]["parts"][0].update(text="```json\n{}\n```"),
            lambda e: e["candidates"][0]["content"]["parts"][0].update(text='{"reason":"x","findings":[]}'),
            lambda e: e["candidates"][0]["content"]["parts"][0].update(text='{"verdict":"PASS","verdict":"FAIL"}'),
        ):
            envelope = self.envelope()
            transform(envelope)
            malformed.append(json.dumps(envelope).encode())
        for raw in malformed:
            with (
                self.subTest(raw=raw[:60]),
                patch.dict(os.environ, GEMINI_API_KEY="synthetic-key"),
                patch.object(gemini, "build_opener", return_value=self.response(raw)),
            ):
                with self.assertRaises(gemini.ProviderError):
                    gemini.review(self.request)

    def test_rejected_api_key_is_named_without_retry(self):
        body = json.dumps({"error": {"code": 400, "status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}}).encode()
        opener = MagicMock()
        opener.open.side_effect = HTTPError("https://example.invalid", 400, "private", {}, io.BytesIO(body))
        with patch.dict(os.environ, GEMINI_API_KEY="synthetic-key"), patch.object(gemini, "build_opener", return_value=opener):
            with self.assertRaises(gemini.ProviderError) as raised:
                gemini.review(self.request)
        self.assertEqual(str(raised.exception), "Gemini rejected GEMINI_API_KEY as invalid (API_KEY_INVALID).")
        self.assertEqual(raised.exception.detail, body.decode())
        self.assertEqual(opener.open.call_count, 1)

    def test_a_technical_failure_exits_with_status_2_and_the_original_error_on_stderr(self):
        body = '{"error": {"code": 400, "message": "Original Google message", "status": "INVALID_ARGUMENT"}}'
        opener = MagicMock()
        opener.open.side_effect = HTTPError("https://example.invalid", 400, "reason", {}, io.BytesIO(body.encode()))
        stderr = io.StringIO()
        with (
            patch.dict(os.environ, GEMINI_API_KEY="synthetic-key"),
            patch.object(gemini, "build_opener", return_value=opener),
            patch.object(sys, "stdin", io.StringIO(json.dumps(self.request))),
            patch.object(sys, "stderr", stderr),
            self.assertRaises(SystemExit) as exited,
        ):
            gemini.main()
        self.assertEqual(exited.exception.code, 2)
        self.assertEqual(stderr.getvalue(), f"Gemini returned HTTP 400.\n{body}\n")
        self.assertNotIn("synthetic-key", stderr.getvalue())

    def test_transient_failure_recovers_with_short_backoff_and_no_extra_calls(self):
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps(self.envelope()).encode()
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.side_effect = [HTTPError("https://example.invalid", 503, "private", {}, io.BytesIO()), TimeoutError(), response]
        with patch.dict(os.environ, GEMINI_API_KEY="synthetic-key"), patch.object(gemini, "build_opener", return_value=opener):
            self.assertEqual(gemini.review(self.request), self.expected())
        self.assertEqual(opener.open.call_count, 3)
        self.assertEqual([call.args for call in self.sleep.call_args_list], [(1,), (3,)])

    def test_invalid_requests_and_mixed_models_never_call_provider(self):
        invalid = []
        for version in (True, False, -1, 2, 3, 1.0, "0", None):
            request = deepcopy(self.request)
            request["protocol_version"] = version
            invalid.append(request)
        for field, value in (("reviews", []), ("prompt", None), ("response_schema", None)):
            request = deepcopy(self.request)
            request[field] = value
            invalid.append(request)
        for parameters in ({}, None, {"model": " "}):
            request = deepcopy(self.request)
            request["reviews"][0]["parameters"] = parameters
            invalid.append(request)
        request = deepcopy(self.request)
        request["reviews"].append({"id": "other", "parameters": {"model": "different-model"}})
        invalid.append(request)
        with patch.object(gemini, "run") as run:
            for request in invalid:
                with self.subTest(request=request), self.assertRaises(gemini.ProviderError):
                    gemini.review(request)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
