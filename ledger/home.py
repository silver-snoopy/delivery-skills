"""The data home: where the ledger keeps its files."""
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping, Optional

from .errors import LedgerError

ENV_VAR = "DELIVERY_LEDGER_HOME"
DEFAULT_DIRNAME = "delivery-ledger-data"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class Home:
    def __init__(self, root: Path):
        self.root = Path(root)

    @classmethod
    def resolve(cls, arg: Optional[str], env: Mapping[str, str]) -> "Home":
        raw = arg or env.get(ENV_VAR)
        root = Path(raw).expanduser() if raw else Path.home() / DEFAULT_DIRNAME
        real = os.path.realpath(str(root))
        if real == "/mnt" or real.startswith("/mnt/"):
            raise LedgerError(
                "data home %s is on a Windows mount" % root,
                "Use a directory on the Linux filesystem, for example "
                "~/%s, via --home or %s." % (DEFAULT_DIRNAME, ENV_VAR),
            )
        return cls(root)

    def ensure(self) -> None:
        self._mkdirs(self.root)
        os.chmod(str(self.root), 0o700)

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def read_json(self, *parts: str, default: Any = None) -> Any:
        p = self.path(*parts)
        try:
            with open(str(p), encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return default
        except ValueError as e:  # json.JSONDecodeError, UnicodeDecodeError
            raise LedgerError("%s could not be read: %s" % (p, e),
                              "The file is not valid JSON; fix or replace it.")

    def write_json(self, obj: Any, *parts: str) -> None:
        target = self.path(*parts)
        self._mkdirs(target.parent)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(obj, f, indent=2, sort_keys=True)
                f.write("\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, str(target))
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def _mkdirs(self, directory: Path) -> None:
        """Create directory and any missing parents at 0700 (umask-proof)."""
        missing = []
        d = directory
        while not d.exists() and d != d.parent:
            missing.append(d)
            d = d.parent
        for m in reversed(missing):
            m.mkdir(mode=0o700)
            os.chmod(str(m), 0o700)

    def team_sprint_dir(self, team: str, sprint_id: str) -> Path:
        if not sprint_id or sprint_id in (".", "..") or "/" in sprint_id \
                or "\\" in sprint_id:
            raise LedgerError("Invalid sprint id %r." % sprint_id,
                              "Pass a sprint id as `ledger analyze` shows it.")
        return self.path("teams", slug(team), "sprints", sprint_id)
