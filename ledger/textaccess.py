"""What a command may print: the agent_text_access setting applied to
command output.

An agent reads what the tool prints, so the setting governs every
command's summary and JSON result. Report and brief files on disk are not
governed by it: they hold ticket titles and are for the delivery manager.
`--show-text` lifts the limit for the delivery manager's own terminal."""
from typing import Any, Dict, Optional

from .errors import LedgerError
from .home import Home

NONE_MARKER = "[text hidden: agent_text_access is none]"
LEVELS = ("none", "titles", "full")


class TextAccess:
    """The level in force for one command run."""

    def __init__(self, level: str):
        self.level = level if level in LEVELS else "none"

    def _marker(self) -> str:
        return "[text hidden: agent_text_access is %s]" % self.level

    def title(self, text: Optional[str]) -> str:
        """A ticket, parent or feature title: shown at titles and full."""
        if self.level in ("titles", "full") or not text:
            return text or ""
        return self._marker()

    def body(self, text: Optional[str]) -> str:
        """A description, an annotation note or a passage of report text:
        shown at full only."""
        if self.level == "full" or not text:
            return text or ""
        return self._marker()

    @property
    def titles(self) -> bool:
        return self.level in ("titles", "full")

    @property
    def full(self) -> bool:
        return self.level == "full"


def redact(cfg: Any, title: Optional[str] = None,
           description: Optional[str] = None,
           show_text: bool = False) -> Dict[str, str]:
    """The given texts as a command may print them under cfg."""
    access = TextAccess("full" if show_text else cfg.agent_text_access)
    out = {}
    if title is not None:
        out["title"] = access.title(title)
    if description is not None:
        out["description"] = access.body(description)
    return out


def for_command(args: Any, home: Home, cfg: Any = None) -> TextAccess:
    """The access for this run: full with --show-text, else the configured
    level; "none" when the configuration cannot be read."""
    if getattr(args, "show_text", False):
        return TextAccess("full")
    if cfg is None:
        try:
            from .config import load_config
            cfg = load_config(home)
        except LedgerError:
            return TextAccess("none")
    return TextAccess(getattr(cfg, "agent_text_access", "none"))
