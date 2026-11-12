"""Pseudonymisation and revision normalisation: the privacy boundary.

Every identity that is not a vendor engineer on the roster is replaced by a
keyed pseudonym here, before anything is written to disk.
"""
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import (Any, Callable, Dict, Iterable, List, Optional, Tuple,
                    Union)

from ..config import Roster, Team
from ..errors import LedgerError
from ..home import Home
from ..model import Revision

KEY_FILE = "secret.key"
INDEX_FILE = "identities.json"
KEY_BYTES = 32
_STRING_IDENTITY = re.compile(r"^\s*(.*?)\s*<([^<>]*)>\s*$")
_CODE_TAG = re.compile(r"(</?)(?:style|script)\b", re.IGNORECASE)
_FRACTION = re.compile(r"\.(\d+)")


def load_or_create_key(home: Home) -> bytes:
    path = home.path(KEY_FILE)
    if path.exists():
        if os.stat(str(path)).st_mode & 0o077:
            os.chmod(str(path), 0o600)
        key = path.read_bytes()
        if len(key) != KEY_BYTES:
            raise LedgerError(
                "%s is not %d bytes long" % (path, KEY_BYTES),
                "Restore the original key file to keep pseudonyms stable; "
                "deleting it breaks continuity with earlier data.")
        return key
    home.ensure()
    key = secrets.token_bytes(KEY_BYTES)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    os.chmod(str(path), 0o600)
    return key


def _parse_identity(identity: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(identity, dict):
        return {"id": identity.get("id") or None,
                "uniqueName": identity.get("uniqueName") or "",
                "displayName": identity.get("displayName") or ""}
    m = _STRING_IDENTITY.match(identity)
    if m:
        return {"id": None, "displayName": m.group(1), "uniqueName": m.group(2)}
    return {"id": None, "displayName": identity.strip(), "uniqueName": ""}


def alias_hash(key: bytes, name: str) -> str:
    """Keyed hash of one sign-in or display name, case-insensitive. The
    identity index holds only these, so a backlog role in config.json can
    be matched to a pseudonym without any name being stored with data."""
    return hmac.new(key, ("alias:" + name.strip().lower()).encode("utf-8"),
                    hashlib.sha256).hexdigest()[:32]


def load_index(home: Home) -> Dict[str, List[str]]:
    """Pseudonym -> alias hashes seen for it, from every collection."""
    raw = home.read_json(INDEX_FILE, default={}) or {}
    if not isinstance(raw, dict):
        return {}
    return {k: [h for h in v if isinstance(h, str)]
            for k, v in raw.items() if isinstance(v, list)}


def role_map(home: Home, roles: Iterable[Any], team: str
             ) -> Dict[str, str]:
    """Pseudonym -> backlog role id for the roles that apply to team.
    Empty when no role is configured or nothing has been collected since
    the index was introduced."""
    roles = [r for r in roles if r.applies_to(team)]
    if not roles or not home.path(KEY_FILE).exists():
        return {}
    key = load_or_create_key(home)
    wanted = {alias_hash(key, a): r.id for r in roles for a in r.aliases}
    out: Dict[str, str] = {}
    for pseudonym, hashes in load_index(home).items():
        for h in hashes:
            if h in wanted:
                out[pseudonym] = wanted[h]
                break
    return out


class Pseudonymizer:
    def __init__(self, key: bytes, roster: Roster,
                 service_identities: Iterable[str],
                 index: Optional[Dict[str, List[str]]] = None,
                 on_index: Optional[Callable[[Dict[str, List[str]]],
                                             None]] = None):
        self._key = key
        self._roster = roster
        self._services = {s.lower() for s in service_identities}
        self._seen: Dict[str, str] = {}
        self.index: Dict[str, List[str]] = dict(index or {})
        self._on_index = on_index

    def _remember(self, pseudonym: str, names: Iterable[str]) -> None:
        have = self.index.setdefault(pseudonym, [])
        new = [h for h in (alias_hash(self._key, n) for n in names if n)
               if h not in have]
        if new:
            have.extend(new)
            have.sort()
            if self._on_index is not None:
                self._on_index(self.index)

    def actor(self, identity: Union[None, str, Dict[str, Any]]
              ) -> Optional[str]:
        if identity is None:
            return None
        ident = _parse_identity(identity)
        unique, display = ident["uniqueName"], ident["displayName"]
        person = self._roster.match_identity(ident["id"], [unique, display])
        if person:
            return person
        if not (ident["id"] or unique or display):
            return None
        if ident["id"]:
            source = "id:" + ident["id"]
        else:
            source = "name:" + (unique or display).lower()
        digest = hmac.new(self._key, source.encode("utf-8"),
                          hashlib.sha256).hexdigest()[:32]
        names = {unique.lower(), display.lower()} - {""}
        prefix = "s:" if names & self._services else "o:"
        pseudonym = prefix + digest
        previous = self._seen.setdefault(pseudonym, source)
        if previous != source:
            raise LedgerError(
                "pseudonym collision between two different identities",
                "This should not happen; report it rather than continuing.")
        self._remember(pseudonym, (unique, display))
        return pseudonym


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "td", "th", "table",
             "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "hr",
             "dl", "dt", "dd", "section", "article", "header", "footer",
             "form", "fieldset", "figure", "img", "thead", "tbody", "tfoot",
             "caption", "nav", "aside", "main", "address", "style", "script"}
    SKIP = {"style", "script"}

    def __init__(self, skip_code: bool = True) -> None:
        # convert_charrefs unescapes entities once; do not unescape again.
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0
        self._skip_code = skip_code

    @property
    def unclosed(self) -> bool:
        return self._skip > 0

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)

    def _break(self, tag: str) -> None:
        if tag in self.BLOCK:
            self.parts.append(" ")

    def handle_starttag(self, tag, attrs) -> None:
        self._break(tag)
        if self._skip_code and tag in self.SKIP:
            self._skip += 1

    def handle_endtag(self, tag) -> None:
        self._break(tag)
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_startendtag(self, tag, attrs) -> None:
        self._break(tag)


def normalise_text(html_text: Optional[str]) -> str:
    if not html_text:
        return ""
    p = _TextExtractor()
    p.feed(html_text)
    p.close()
    if p.unclosed:
        # An unclosed <style>/<script> would swallow the rest of the text;
        # losing text is worse than counting code as text, so keep it all.
        # Neutralise the tags so no Python version enters CDATA mode.
        p = _TextExtractor(skip_code=False)
        p.feed(_CODE_TAG.sub(lambda m: m.group(1) + "div", html_text))
        p.close()
    text = "".join(p.parts).replace(" ", " ")
    return " ".join(text.split())


def _parse_changed(value: str) -> datetime:
    s = value.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    s = _FRACTION.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), s, 1)
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        raise LedgerError("unreadable System.ChangedDate %r" % value,
                          "Check the ADO payload for this revision.")
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def _tags(value: Optional[str]) -> Tuple[str, ...]:
    if not value:
        return ()
    return tuple(t.strip() for t in value.split(";") if t.strip())


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pick(fields: Dict[str, Any], configured: Optional[str],
          default: str) -> Any:
    if configured and configured in fields:
        return fields[configured]
    return fields.get(default)


def _structure(raw: Dict[str, Any], team: Team) -> Dict[str, Any]:
    f = raw["fields"]
    column = _pick(f, team.column_field, "System.BoardColumn")
    done = _pick(f, team.done_field, "System.BoardColumnDone")
    parent = f.get("System.Parent")
    return dict(
        rev=int(f.get("System.Rev", raw.get("rev"))),
        changed=_parse_changed(f["System.ChangedDate"]),
        state=f.get("System.State") or "",
        reason=f.get("System.Reason") or "",
        column=None if column is None else str(column),
        column_done=None if done is None else bool(done),
        iteration=f.get("System.IterationPath") or "",
        area=f.get("System.AreaPath") or "",
        type=f.get("System.WorkItemType") or "",
        project=f.get("System.TeamProject") or "",
        parent=None if parent is None else int(parent),
        tags=_tags(f.get("System.Tags")),
    )


def ticket_text(raw: Dict[str, Any]) -> Dict[str, str]:
    f = raw["fields"]
    return {"title": f.get("System.Title") or "",
            "description": normalise_text(f.get("System.Description")),
            "acceptance": normalise_text(
                f.get("Microsoft.VSTS.Common.AcceptanceCriteria"))}


def to_revision(raw: Dict[str, Any], pseud: Pseudonymizer,
                team: Team) -> Revision:
    f = raw["fields"]
    desc = normalise_text(f.get("System.Description"))
    ac = normalise_text(f.get("Microsoft.VSTS.Common.AcceptanceCriteria"))
    return Revision(
        by=pseud.actor(f.get("System.ChangedBy")),
        assignee=pseud.actor(f.get("System.AssignedTo")),
        desc_hash=_hash(desc), desc_len=len(desc),
        ac_hash=_hash(ac), ac_len=len(ac),
        **_structure(raw, team))


def to_squad_revision(raw: Dict[str, Any], team: Team) -> Revision:
    return Revision(by=None, assignee=None, desc_hash="", desc_len=0,
                    ac_hash="", ac_len=0, **_structure(raw, team))
