"""HTTP layer for Azure DevOps. Read-only: GET, plus POST for the query endpoint.

Credentials are only ever sent to the configured organisation host over https.
"""

import json
import math
import random
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ledger.ado.auth import ADO_RESOURCE, AZ_LOGIN_HINT
from ledger.errors import LedgerError

DEFAULT_TIMEOUT = 60
ALLOWED_METHODS = ("GET", "POST")
MAX_RETRY_AFTER = 300


class TransportError(LedgerError):
    """A connection-level failure (no HTTP response); safe to retry."""


@dataclass
class Response:
    status: int
    headers: Dict[str, str]
    body: Any
    text: Optional[str] = field(default=None)


def _safe(url: str) -> str:
    return url.encode("ascii", "backslashreplace").decode("ascii")[:200]


def _check_request(method: str, url: str, allowed_host: str) -> str:
    method = method.upper()
    if method not in ALLOWED_METHODS:
        raise LedgerError("Refusing %s request: the tool is read-only." % method)
    refusal = LedgerError(
        "Refusing to contact %s: only https://%s is allowed." % (_safe(url), allowed_host),
        "Check the organisation URL in the configuration.",
    )
    parts = urllib.parse.urlsplit(url)
    netloc = parts.netloc
    if any(c in netloc for c in "@\\%") or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in netloc):
        raise refusal
    if parts.username is not None or parts.password is not None:
        raise refusal
    try:
        port = parts.port
    except ValueError:
        raise refusal
    if port is not None and port != 443:
        raise refusal
    if parts.scheme.lower() != "https" or (parts.hostname or "").lower() != allowed_host.lower():
        raise refusal
    return method


def _parse_body(raw: bytes):
    """Return (parsed JSON or None, raw text when the body was not JSON)."""
    text = raw.decode("utf-8", errors="replace") if raw else ""
    if not text.strip():
        return None, None
    try:
        return json.loads(text), None
    except ValueError:
        return None, text


def _make_response(status: int, headers, raw: bytes) -> Response:
    lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
    body, text = _parse_body(raw)
    return Response(status, lowered, body, text)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: the Authorization header must not leave the organisation host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibTransport:
    timeout = DEFAULT_TIMEOUT

    def __init__(self, auth, allowed_host: str, opener=None):
        self.auth = auth
        self.allowed_host = allowed_host
        if opener is None:
            # Default handlers: HTTPS_PROXY / SSL_CERT_FILE honoured, certificates verified.
            built = urllib.request.build_opener(_NoRedirect)
            opener = lambda req, timeout: built.open(req, timeout=timeout)  # noqa: E731
        self._opener = opener

    def request(self, method: str, url: str, body: Optional[dict] = None) -> Response:
        method = _check_request(method, url, self.allowed_host)
        headers = {"Authorization": self.auth.header(), "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            resp = self._opener(req, self.timeout)
            return _make_response(resp.status, resp.headers, resp.read())
        except urllib.error.HTTPError as exc:
            try:
                return _make_response(exc.code, exc.headers, exc.read())
            finally:
                exc.close()
        except (urllib.error.URLError, OSError) as exc:  # timeouts and resets are OSErrors
            raise TransportError("Connection to %s failed: %s" % (self.allowed_host, exc))


_STATUS_WORDS = r"(?:Bad Request|Unauthorized|Forbidden|Not Found|Too Many Requests)"
_STATUS_PATTERNS = [
    re.compile(r"\((\d{3})\)"),
    re.compile(r"status code\D{0,3}(\d{3})", re.I),
    re.compile(_STATUS_WORDS + r"\D{0,4}(\d{3})", re.I),
    re.compile(r"\b(\d{3})\s+" + _STATUS_WORDS, re.I),
]


def _status_from_stderr(stderr: str) -> Optional[int]:
    for pattern in _STATUS_PATTERNS:
        m = pattern.search(stderr or "")
        if m and 400 <= int(m.group(1)) <= 599:
            return int(m.group(1))
    return None


class AzRestTransport:
    """Fallback that lets `az rest` do the HTTP, using the signed-in account."""

    mode = "az-rest"

    def __init__(self, allowed_host: str, run=subprocess.run):
        self.allowed_host = allowed_host
        self._run = run

    def request(self, method: str, url: str, body: Optional[dict] = None) -> Response:
        method = _check_request(method, url, self.allowed_host)
        argv = ["az", "rest", "--method", method.lower(), "--url", url,
                "--resource", ADO_RESOURCE, "-o", "json"]
        if body is not None:
            argv += ["--body", json.dumps(body), "--headers", "Content-Type=application/json"]
        try:
            proc = self._run(argv, capture_output=True, text=True)
        except OSError as exc:
            raise LedgerError("Could not run the az CLI: %s" % exc, AZ_LOGIN_HINT)
        if proc.returncode == 0:
            parsed, text = _parse_body((proc.stdout or "").encode("utf-8"))
            return Response(200, {}, parsed, text)
        status = _status_from_stderr(proc.stderr)
        if status is not None:
            return Response(status, {}, None)
        raise TransportError("az rest failed: %s" % (proc.stderr or "").strip()[:300])


class PacedTransport:
    """Retries, backs off and honours Retry-After around another transport."""

    def __init__(self, inner, sleep=time.sleep, rand=random.random, max_attempts: int = 6):
        self.inner = inner
        self._sleep = sleep
        self._rand = rand
        self.max_attempts = max_attempts
        self._pending_delay = 0.0

    @staticmethod
    def _retry_after(resp: Response) -> Optional[float]:
        try:
            value = float(resp.headers.get("retry-after"))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value) or value < 0:
            return None
        return min(value, float(MAX_RETRY_AFTER))

    def _backoff(self, n: int, resp: Optional[Response]) -> float:
        if resp is not None:
            hinted = self._retry_after(resp)
            if hinted is not None:
                return hinted
        return min(60, 2 ** n) * (0.5 + self._rand() / 2)

    def request(self, method: str, url: str, body: Optional[dict] = None) -> Response:
        if self._pending_delay:
            delay, self._pending_delay = self._pending_delay, 0.0
            self._sleep(delay)
        failures = 0
        reacquired = False
        while True:
            resp = None
            try:
                resp = self.inner.request(method, url, body)
                failure = None
            except TransportError as exc:
                failure = "connection error: %s" % exc.message
            if resp is not None:
                if resp.status == 401:
                    if reacquired:
                        raise LedgerError(
                            "Azure DevOps rejected the credentials (HTTP 401).",
                            "Run `az login` (or check the personal access token), then try again.",
                        )
                    reacquired = True
                    auth = getattr(self.inner, "auth", None)
                    if hasattr(auth, "reacquire"):
                        auth.reacquire()
                    continue
                if resp.status not in (429, 503):
                    hinted = self._retry_after(resp)
                    if hinted:
                        self._pending_delay = hinted
                    return resp
                failure = "HTTP %d" % resp.status
            failures += 1
            if failures >= self.max_attempts:
                raise LedgerError(
                    "Azure DevOps request failed after %d attempts (last: %s)."
                    % (failures, failure),
                    "Wait a few minutes and try again; check network and proxy settings.",
                )
            self._sleep(self._backoff(failures, resp))
