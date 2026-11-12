"""Authentication for Azure DevOps: an `az` access token or a personal access token."""

import base64
import json
import subprocess
import time
from datetime import datetime
from typing import List

from ledger.errors import LedgerError

ADO_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"

REFRESH_MARGIN_SECONDS = 300
DEFAULT_VALIDITY_SECONDS = 600
EXPIRY_KEYS = ("expires_on", "expiresOn")
AZ_LOGIN_HINT = "Run `az login`, then try again."


def _parse_expiry(data: dict, acquired_at: float) -> float:
    """Epoch seconds at which the token expires."""
    raw = data.get("expires_on")
    try:
        return float(int(raw))
    except (TypeError, ValueError):
        pass
    text = data.get("expiresOn")
    if isinstance(text, str):
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(text, fmt).timestamp()  # naive: local time
            except ValueError:
                continue
    return acquired_at + DEFAULT_VALIDITY_SECONDS


class AzTokenAuth:
    mode = "az"

    def __init__(self, run=subprocess.run, now=time.time):
        self._run = run
        self._now = now
        self._token = None
        self._expires_at = 0.0
        self._expiry_fields = []

    def header(self) -> str:
        if self._token is None or self._expires_at - self._now() < REFRESH_MARGIN_SECONDS:
            self._acquire()
        return "Bearer " + self._token

    def reacquire(self) -> None:
        self._token = None

    def expiry_fields(self) -> List[str]:
        """Which expiry keys the last token response carried (never the token)."""
        if self._token is None:
            self._acquire()
        return list(self._expiry_fields)

    def _acquire(self) -> None:
        argv = ["az", "account", "get-access-token", "--resource", ADO_RESOURCE, "-o", "json"]
        try:
            proc = self._run(argv, capture_output=True, text=True)
        except OSError as exc:
            raise LedgerError("Could not run the az CLI: %s" % exc, AZ_LOGIN_HINT)
        if proc.returncode != 0:
            raise LedgerError("az could not provide an access token.", AZ_LOGIN_HINT)
        try:
            data = json.loads(proc.stdout)
            token = data["accessToken"]
        except (ValueError, KeyError, TypeError):
            raise LedgerError("az returned an unreadable access token response.", AZ_LOGIN_HINT)
        self._token = token
        self._expiry_fields = [k for k in EXPIRY_KEYS if k in data]
        self._expires_at = _parse_expiry(data, self._now())


class PatAuth:
    mode = "pat"

    def __init__(self, pat: str):
        self._header = "Basic " + base64.b64encode((":" + pat).encode("utf-8")).decode("ascii")

    def header(self) -> str:
        return self._header

    def reacquire(self) -> None:
        return None
