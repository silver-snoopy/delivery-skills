import base64
import json
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace

from ledger.ado.auth import ADO_RESOURCE, AzTokenAuth, PatAuth
from ledger.ado.transport import (
    MAX_RETRY_AFTER,
    AzRestTransport,
    PacedTransport,
    Response,
    TransportError,
    UrllibTransport,
    _NoRedirect,
)
from ledger.errors import LedgerError

URL = "https://dev.azure.com/org/proj/_apis/wit/workitems/1"


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def az_token_run(calls, expires_in=3600, clock=None, field="expires_on"):
    def run(argv, capture_output=None, text=None):
        calls.append(argv)
        n = len(calls)
        exp = (clock() if clock else 1000.0) + expires_in
        payload = {"accessToken": "tok%d" % n, field: int(exp)}
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
    return run


class AuthTests(unittest.TestCase):
    def test_expiry_fields_never_contains_token(self):
        for field in ("expires_on", "expiresOn"):
            calls, clock = [], FakeClock()
            auth = AzTokenAuth(run=az_token_run(calls, 3600, clock, field),
                               now=clock)
            got = auth.expiry_fields()
            self.assertEqual(got, [field])
            self.assertNotIn("tok1", json.dumps(got))
            self.assertEqual(len(calls), 1)

    def test_expiry_fields_empty_when_absent(self):
        run = lambda argv, **kw: SimpleNamespace(  # noqa: E731
            returncode=0, stdout=json.dumps({"accessToken": "t"}), stderr="")
        self.assertEqual(AzTokenAuth(run=run, now=FakeClock()).expiry_fields(),
                         [])

    def test_token_cached_and_refreshed_before_expiry(self):
        calls, clock = [], FakeClock()
        auth = AzTokenAuth(run=az_token_run(calls, 3600, clock), now=clock)
        self.assertEqual(auth.header(), "Bearer tok1")
        self.assertEqual(auth.header(), "Bearer tok1")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:3], ["az", "account", "get-access-token"])
        self.assertIn(ADO_RESOURCE, calls[0])
        clock.t += 3600 - 200  # 200 s left, under the 300 s margin
        self.assertEqual(auth.header(), "Bearer tok2")
        self.assertEqual(len(calls), 2)

    def test_token_not_refreshed_with_ample_time(self):
        calls, clock = [], FakeClock()
        auth = AzTokenAuth(run=az_token_run(calls, 3600, clock), now=clock)
        auth.header()
        clock.t += 3600 - 400
        auth.header()
        self.assertEqual(len(calls), 1)

    def test_reacquire_discards_cache(self):
        calls, clock = [], FakeClock()
        auth = AzTokenAuth(run=az_token_run(calls, 3600, clock), now=clock)
        auth.header()
        auth.reacquire()
        self.assertEqual(auth.header(), "Bearer tok2")

    def test_expires_on_local_time_fallback(self):
        import time
        calls = []
        clock = FakeClock(time.time())
        exp = time.strftime("%Y-%m-%d %H:%M:%S.000000", time.localtime(clock() + 3600))

        def run(argv, capture_output=None, text=None):
            calls.append(argv)
            body = {"accessToken": "t", "expiresOn": exp}
            return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")
        auth = AzTokenAuth(run=run, now=clock)
        auth.header()
        auth.header()
        self.assertEqual(len(calls), 1)
        clock.t += 3600 - 100
        auth.header()
        self.assertEqual(len(calls), 2)

    def test_unparseable_expiry_valid_for_600_seconds(self):
        calls, clock = [], FakeClock()

        def run(argv, capture_output=None, text=None):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout='{"accessToken": "t"}', stderr="")
        auth = AzTokenAuth(run=run, now=clock)
        auth.header()
        clock.t += 200
        auth.header()
        self.assertEqual(len(calls), 1)
        clock.t += 150  # 350 s in, 250 s left
        auth.header()
        self.assertEqual(len(calls), 2)

    def test_az_failure_hint(self):
        def run(argv, capture_output=None, text=None):
            return SimpleNamespace(returncode=1, stdout="", stderr="Please run 'az login'")
        with self.assertRaises(LedgerError) as cm:
            AzTokenAuth(run=run, now=FakeClock()).header()
        self.assertIn("az login", cm.exception.hint)

    def test_az_missing_gives_hint(self):
        def run(argv, capture_output=None, text=None):
            raise FileNotFoundError("az")
        with self.assertRaises(LedgerError) as cm:
            AzTokenAuth(run=run, now=FakeClock()).header()
        self.assertIn("az login", cm.exception.hint)

    def test_az_garbage_output_is_ledger_error(self):
        def run(argv, capture_output=None, text=None):
            return SimpleNamespace(returncode=0, stdout="not json", stderr="")
        with self.assertRaises(LedgerError):
            AzTokenAuth(run=run, now=FakeClock()).header()

    def test_pat_header_and_mode(self):
        auth = PatAuth("secret")
        self.assertEqual(auth.header(), "Basic " + base64.b64encode(b":secret").decode())
        self.assertEqual(auth.mode, "pat")
        self.assertEqual(AzTokenAuth().mode, "az")
        auth.reacquire()  # no-op, must not raise


class FakeResp:
    def __init__(self, status=200, headers=None, body=b"{}"):
        self.status = status
        self.headers = headers or {}
        self._body = body

    def read(self):
        return self._body


class UrllibTransportTests(unittest.TestCase):
    def make(self, opener):
        return UrllibTransport(PatAuth("p"), "dev.azure.com", opener=opener)

    def test_urllib_refuses_other_host(self):
        calls = []
        t = self.make(lambda req, timeout: calls.append(req))
        for url in ("https://example.com/x", "https://dev.azure.com.evil.com/x",
                    "https://dev.azure.com@evil.com/x", "http://dev.azure.com/x"):
            with self.assertRaises(LedgerError):
                t.request("GET", url)
        self.assertEqual(calls, [])

    def test_host_compare_case_insensitive(self):
        t = self.make(lambda req, timeout: FakeResp())
        self.assertEqual(t.request("GET", "https://DEV.Azure.com/x").status, 200)

    def test_refuses_other_methods(self):
        calls = []
        t = self.make(lambda req, timeout: calls.append(req))
        for m in ("DELETE", "PATCH", "PUT"):
            with self.assertRaises(LedgerError):
                t.request(m, URL)
        self.assertEqual(calls, [])

    def test_get_sends_auth_and_parses(self):
        seen = {}

        def opener(req, timeout):
            seen["req"], seen["timeout"] = req, timeout
            return FakeResp(200, {"Retry-After": "3", "Content-Type": "application/json"}, b'{"a": 1}')
        r = self.make(opener).request("GET", URL)
        self.assertEqual((r.status, r.body), (200, {"a": 1}))
        self.assertEqual(r.headers["retry-after"], "3")
        self.assertEqual(seen["req"].get_method(), "GET")
        self.assertTrue(seen["req"].get_header("Authorization").startswith("Basic "))
        self.assertEqual(seen["timeout"], 60)

    def test_post_sends_json_body(self):
        seen = {}

        def opener(req, timeout):
            seen["req"] = req
            return FakeResp()
        self.make(opener).request("POST", URL, {"query": "x"})
        req = seen["req"]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(json.loads(req.data.decode()), {"query": "x"})
        self.assertEqual(req.get_header("Content-type"), "application/json")

    def test_empty_and_non_json_bodies(self):
        r = self.make(lambda req, timeout: FakeResp(body=b"")).request("GET", URL)
        self.assertIsNone(r.body)
        r = self.make(lambda req, timeout: FakeResp(body=b"<html>no</html>")).request("GET", URL)
        self.assertIsNone(r.body)
        self.assertEqual(r.text, "<html>no</html>")

    def test_http_error_becomes_response(self):
        import io

        def opener(req, timeout):
            raise urllib.error.HTTPError(URL, 429, "Too Many", {"Retry-After": "9"},
                                         io.BytesIO(b'{"m": "slow"}'))
        r = self.make(opener).request("GET", URL)
        self.assertEqual(r.status, 429)
        self.assertEqual(r.headers["retry-after"], "9")
        self.assertEqual(r.body, {"m": "slow"})

    def test_connection_failures_are_transport_errors(self):
        for exc in (urllib.error.URLError("down"), TimeoutError("t"), ConnectionResetError("r")):
            def opener(req, timeout, exc=exc):
                raise exc
            with self.assertRaises(TransportError):
                self.make(opener).request("GET", URL)

    def test_no_redirect_handler_refuses_to_follow(self):
        h = _NoRedirect()
        req = urllib.request.Request(URL)
        self.assertIsNone(h.redirect_request(req, None, 302, "Found", {}, "https://evil.com/"))


class AzRestTransportTests(unittest.TestCase):
    def make(self, returncode=0, stdout="{}", stderr=""):
        calls = []

        def run(argv, capture_output=None, text=None):
            calls.append(argv)
            return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
        return AzRestTransport("dev.azure.com", run=run), calls

    def test_az_rest_transport_builds_command(self):
        t, calls = self.make(stdout='{"value": []}')
        r = t.request("POST", URL, {"query": "q"})
        argv = calls[0]
        self.assertEqual(argv[:2], ["az", "rest"])
        self.assertEqual(argv[argv.index("--method") + 1], "post")
        self.assertEqual(argv[argv.index("--url") + 1], URL)
        self.assertEqual(argv[argv.index("--resource") + 1], ADO_RESOURCE)
        self.assertEqual(json.loads(argv[argv.index("--body") + 1]), {"query": "q"})
        self.assertIn("Content-Type=application/json", argv)
        self.assertEqual(argv[argv.index("-o") + 1], "json")
        self.assertEqual((r.status, r.body), (200, {"value": []}))
        self.assertEqual(t.mode, "az-rest")
        self.assertFalse(hasattr(t, "auth"))

    def test_get_has_no_body(self):
        t, calls = self.make()
        t.request("GET", URL)
        self.assertNotIn("--body", calls[0])
        self.assertEqual(calls[0][calls[0].index("--method") + 1], "get")

    def test_refuses_host_and_method(self):
        t, calls = self.make()
        with self.assertRaises(LedgerError):
            t.request("GET", "https://example.com/x")
        with self.assertRaises(LedgerError):
            t.request("DELETE", URL)
        self.assertEqual(calls, [])

    def test_status_parsed_from_stderr(self):
        cases = {
            "ERROR: Bad Request 400": 400,
            "Unauthorized(401)": 401,
            "ERROR: status code 404 returned": 404,
            "ERROR: Too Many Requests: 429": 429,
            "Forbidden (403)": 403,
        }
        for stderr, status in cases.items():
            t, _ = self.make(returncode=1, stdout="", stderr=stderr)
            r = t.request("GET", URL)
            self.assertEqual((r.status, r.body), (status, None), stderr)

    def test_unknown_failure_is_transport_error(self):
        t, _ = self.make(returncode=1, stdout="", stderr="connection reset by peer")
        with self.assertRaises(TransportError):
            t.request("GET", URL)

    def test_empty_stdout_is_none_body(self):
        t, _ = self.make(stdout="")
        self.assertIsNone(t.request("GET", URL).body)


class ScriptedInner:
    def __init__(self, items, auth=None):
        self.items = list(items)
        self.calls = 0
        if auth is not None:
            self.auth = auth

    def request(self, method, url, body=None):
        self.calls += 1
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def resp(status=200, **headers):
    return Response(status, dict(headers), {})


class PacedTransportTests(unittest.TestCase):
    def make(self, inner, **kw):
        sleeps = []
        kw.setdefault("rand", lambda: 1.0)
        return PacedTransport(inner, sleep=sleeps.append, **kw), sleeps

    def test_retry_after_on_success_is_obeyed(self):
        inner = ScriptedInner([resp(200, **{"retry-after": "7"}), resp(200)])
        t, sleeps = self.make(inner)
        t.request("GET", URL)
        self.assertEqual(sleeps, [])  # not before returning
        t.request("GET", URL)
        self.assertEqual(sleeps, [7])

    def test_pending_delay_applies_once(self):
        inner = ScriptedInner([resp(200, **{"retry-after": "7"}), resp(200), resp(200)])
        t, sleeps = self.make(inner)
        for _ in range(3):
            t.request("GET", URL)
        self.assertEqual(sleeps, [7])

    def test_429_then_success(self):
        inner = ScriptedInner([resp(429), resp(503), resp(200)])
        t, sleeps = self.make(inner)
        self.assertEqual(t.request("GET", URL).status, 200)
        self.assertEqual(inner.calls, 3)
        self.assertEqual(sleeps, [2.0, 4.0])  # rand()=1 -> factor 1.0

    def test_backoff_jitter_and_cap(self):
        inner = ScriptedInner([resp(429)] * 5 + [resp(200)])
        t, sleeps = self.make(inner, rand=lambda: 0.0, max_attempts=6)
        t.request("GET", URL)
        self.assertEqual(sleeps, [1.0, 2.0, 4.0, 8.0, 16.0])
        inner = ScriptedInner([resp(429)] * 8 + [resp(200)])
        t, sleeps = self.make(inner, max_attempts=9)
        t.request("GET", URL)
        self.assertEqual(max(sleeps), 60.0)

    def test_retry_after_header_on_429_used_as_delay(self):
        inner = ScriptedInner([resp(429, **{"retry-after": "11"}), resp(200)])
        t, sleeps = self.make(inner)
        t.request("GET", URL)
        self.assertEqual(sleeps, [11])

    def test_transport_error_retried(self):
        inner = ScriptedInner([TransportError("reset"), resp(200)])
        t, sleeps = self.make(inner)
        self.assertEqual(t.request("GET", URL).status, 200)
        self.assertEqual(len(sleeps), 1)

    def test_gives_up_after_max_attempts(self):
        inner = ScriptedInner([resp(429)] * 3)
        t, sleeps = self.make(inner, max_attempts=3)
        with self.assertRaises(LedgerError) as cm:
            t.request("GET", URL)
        self.assertIn("429", cm.exception.message)
        self.assertEqual(inner.calls, 3)
        self.assertEqual(len(sleeps), 2)

    def test_gives_up_naming_transport_error(self):
        inner = ScriptedInner([TransportError("boom")] * 2)
        t, _ = self.make(inner, max_attempts=2)
        with self.assertRaises(LedgerError) as cm:
            t.request("GET", URL)
        self.assertIn("boom", cm.exception.message)

    def test_other_errors_returned_unchanged(self):
        for status in (400, 404, 500):
            inner = ScriptedInner([resp(status)])
            t, sleeps = self.make(inner)
            self.assertEqual(t.request("GET", URL).status, status)
            self.assertEqual((inner.calls, sleeps), (1, []))

    def test_401_reacquires_once(self):
        auth = SimpleNamespace(count=0)
        auth.reacquire = lambda: setattr(auth, "count", auth.count + 1)
        inner = ScriptedInner([resp(401), resp(200)], auth=auth)
        t, _ = self.make(inner)
        self.assertEqual(t.request("GET", URL).status, 200)
        self.assertEqual(auth.count, 1)

    def test_second_401_raises_with_hint(self):
        auth = SimpleNamespace(reacquire=lambda: None)
        inner = ScriptedInner([resp(401), resp(401)], auth=auth)
        t, _ = self.make(inner)
        with self.assertRaises(LedgerError) as cm:
            t.request("GET", URL)
        self.assertIn("az login", cm.exception.hint)
        self.assertEqual(inner.calls, 2)

    def test_401_without_auth_attribute_still_retries_once(self):
        inner = ScriptedInner([resp(401), resp(200)])
        t, _ = self.make(inner)
        self.assertEqual(t.request("GET", URL).status, 200)


class HardeningTests(unittest.TestCase):
    BAD_URLS = [
        "https://evil.example\\@dev.azure.com/x",
        "https://evil.example%2f@dev.azure.com/x",
        "https://user:pw@dev.azure.com/x",
        "https://user@dev.azure.com/x",
        "https://dev.azure.com\t.evil.com/x",
        "https://dev.azure.com :443/x",
        "https://dev.azure.com:8443/x",
    ]

    def transports(self):
        calls = []
        yield UrllibTransport(PatAuth("p"), "dev.azure.com",
                              opener=lambda r, t: calls.append(r)), calls
        runs = []

        def run(argv, capture_output=None, text=None):
            runs.append(argv)
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")
        yield AzRestTransport("dev.azure.com", run=run), runs

    def test_ambiguous_urls_refused(self):
        for url in self.BAD_URLS:
            for t, calls in self.transports():
                with self.assertRaises(LedgerError, msg=url):
                    t.request("GET", url)
                self.assertEqual(calls, [], url)

    def test_port_443_accepted(self):
        t = UrllibTransport(PatAuth("p"), "dev.azure.com", opener=lambda r, to: FakeResp())
        self.assertEqual(t.request("GET", "https://dev.azure.com:443/x").status, 200)

    def test_az_rest_failed_call_with_2xx_in_stderr_is_error(self):
        for stderr in ("something (200) odd", "status code 302"):
            def run(argv, capture_output=None, text=None, stderr=stderr):
                return SimpleNamespace(returncode=1, stdout="", stderr=stderr)
            with self.assertRaises(TransportError):
                AzRestTransport("dev.azure.com", run=run).request("GET", URL)

    def test_retry_after_clamped(self):
        self.assertEqual(MAX_RETRY_AFTER, 300)
        inner = ScriptedInner([resp(429, **{"retry-after": "86400"}), resp(200, **{"retry-after": "99999"}), resp(200)])
        sleeps = []
        t = PacedTransport(inner, sleep=sleeps.append, rand=lambda: 1.0)
        t.request("GET", URL)
        t.request("GET", URL)
        self.assertEqual(sleeps, [300, 300])

    def test_non_finite_retry_after_ignored(self):
        for bad in ("inf", "1e999", "abc", "nan", "-5"):
            inner = ScriptedInner([resp(429, **{"retry-after": bad}), resp(200, **{"retry-after": bad}), resp(200)])
            sleeps = []
            t = PacedTransport(inner, sleep=sleeps.append, rand=lambda: 1.0)
            t.request("GET", URL)
            t.request("GET", URL)
            self.assertEqual(sleeps, [2.0], bad)


if __name__ == "__main__":
    unittest.main()
