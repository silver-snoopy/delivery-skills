"""Names check: find vendor engineers' names in the executive report text,
and the `names-check` command that records approved occurrences."""
import argparse
import html
import re
import unicodedata
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from .config import Roster, load_roster
from .errors import LedgerError
from .home import Home
from .textaccess import for_command

APPROVED = "names-approved.json"
EXEC_FILES = ("executive.html", "executive.md")
_RADIUS = 40
MIN_PART = 3  # name parts shorter than this are not scanned for
# words of account and mail domains that are no part of a person's name
_DOMAIN_WORDS = frozenset((
    "com", "org", "net", "edu", "gov", "int", "biz", "info", "example",
    "mail", "email", "corp", "local", "lan", "www", "onmicrosoft",
    "outlook", "hotmail", "gmail", "live", "domain"))
_NON_LETTER = re.compile(r"[\W\d_]+")
# characters ledger.render.markdown.md() escapes with a backslash
_MD_UNESCAPE = re.compile(r"\\([\\|*_`\[\]<>])")


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def fold(text: str) -> str:
    """NFC text with accents removed (combining marks dropped after NFD)."""
    return _nfc("".join(c for c in unicodedata.normalize("NFD", text)
                        if not unicodedata.combining(c)))


def _terms(roster: Roster) -> List[str]:
    out: List[str] = []
    seen = set()

    def add(term: str) -> None:
        term = _nfc(term.strip())
        if term and term.lower() not in seen:
            seen.add(term.lower())
            out.append(term)

    for p in roster.people:
        add(p.display_name)
        for a in p.aliases:
            add(a)
        for part in _NON_LETTER.split(_nfc(p.display_name)):
            if len(part) >= MIN_PART:
                add(part)
        for a in p.aliases:
            # the part before "@" of a sign-in name; a mail domain is not
            # a person's name
            for part in _NON_LETTER.split(_nfc(a).split("@")[0]):
                if len(part) >= MIN_PART and part.lower() not in _DOMAIN_WORDS:
                    add(part)
    return out


def name_patterns(roster: Roster) -> List[Tuple[str, "re.Pattern"]]:
    """(term, whole-word case-insensitive pattern) for every roster name
    term: display names, aliases, and parts of three or more letters of
    display names and of sign-in aliases. Match it
    against NFC-normalised text."""
    out = []
    for t in _terms(roster):
        forms = [t] if fold(t) == t else [t, fold(t)]
        out.append((t, re.compile(r"(?<!\w)(?:%s)(?!\w)" % "|".join(
            re.escape(x) for x in forms), re.IGNORECASE)))
    return out


def name_hits(text: str, roster: Roster) -> List[str]:
    """Roster terms found in one piece of text, matched exactly and
    accent-folded."""
    found = []
    for term, rx in name_patterns(roster):
        if rx.search(_nfc(text)) or rx.search(fold(text)):
            found.append(term)
    return found


def _approval_covers(a: Dict, term: str, context: str) -> bool:
    if _nfc(str(a.get("term", ""))).lower() != term.lower():
        return False
    actx = _collapse(_nfc(str(a.get("context", ""))))
    if not actx:
        return True
    return (term.lower() in actx.lower() or fold(term).lower()
            in fold(actx).lower()) and (actx in context
                                        or fold(actx) in context)


def _covered(term: str, context: str, approved: List[Dict]) -> bool:
    return any(_approval_covers(a, term, context) for a in approved)


def _forms(name: str, text: str) -> List[str]:
    """The raw text and, when it differs, the text with the renderer's
    escaping undone, both NFC-normalised."""
    raw = _nfc(text)
    if name.endswith(".html"):
        plain = _nfc(html.unescape(text))
    elif name.endswith(".md"):
        plain = _MD_UNESCAPE.sub(r"\1", raw)
    else:
        plain = raw
    return [raw] if plain == raw else [raw, plain]


def names_check(texts: Dict[str, str], roster: Roster,
                approved: List[Dict]) -> List[Dict[str, str]]:
    """Uncovered occurrences of roster names, de-duplicated, in file order."""
    patterns = name_patterns(roster)
    hits: List[Dict[str, str]] = []
    seen = set()
    for name, text in texts.items():
        found: List[Tuple[int, int, int, Dict[str, str]]] = []
        forms = _forms(name, text)
        sources = list(range(len(forms)))
        for f in range(len(forms)):  # accent-folded second pass
            if fold(forms[f]) != forms[f]:
                forms.append(fold(forms[f]))
                sources.append(f)
        at = set()
        for f, form in enumerate(forms):
            for i, (term, rx) in enumerate(patterns):
                for m in rx.finditer(form):
                    spot = (sources[f], m.start(), i)
                    if spot in at:
                        continue  # same occurrence, found unfolded
                    at.add(spot)
                    ctx = _collapse(form[max(0, m.start() - _RADIUS):
                                         m.end() + _RADIUS])
                    if _covered(term, ctx, approved):
                        continue
                    found.append((f, m.start(), i,
                                  {"file": name, "term": term, "context": ctx}))
        for *_, hit in sorted(found, key=lambda x: x[:3]):
            key = (hit["file"], hit["term"].lower(), hit["context"])
            if key not in seen:
                seen.add(key)
                hits.append(hit)
    return hits


# ---- draft helpers (shared with issue) -------------------------------------

def latest_draft_date(home: Home) -> date:
    root = home.path("reports")
    dates = []
    if root.is_dir():
        for d in root.iterdir():
            if (d / "draft").is_dir():
                try:
                    dates.append(date.fromisoformat(d.name))
                except ValueError:
                    continue
    if not dates:
        raise LedgerError("No report draft exists.",
                          "Run `ledger report` first.")
    return max(dates)


def draft_texts(home: Home, as_of: date) -> Dict[str, str]:
    draft = home.path("reports", as_of.isoformat(), "draft")
    if not all((draft / f).is_file() for f in EXEC_FILES):
        raise LedgerError("No report draft for %s." % as_of.isoformat(),
                          "Run `ledger report --as-of %s` first."
                          % as_of.isoformat())
    return {f: (draft / f).read_text(encoding="utf-8") for f in EXEC_FILES}


def approvals(home: Home, as_of: date) -> List[Dict]:
    return home.read_json("reports", as_of.isoformat(), APPROVED,
                          default=[]) or []


# ---- command ---------------------------------------------------------------

def resolve_as_of(text: str) -> date:
    """argparse type for --as-of."""
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD, got %r" % text)


def _names_check_cmd(args, home: Home) -> Dict[str, Any]:
    roster = load_roster(home)
    access = for_command(args, home)
    as_of: Optional[date] = args.as_of or latest_draft_date(home)
    texts = draft_texts(home, as_of)
    approved = approvals(home, as_of)
    if args.approve is None and (args.context is not None
                                 or args.all_occurrences):
        raise LedgerError("--context and --all-occurrences need --approve.",
                          "Pass --approve TERM with one of them.")
    if args.context is not None and args.all_occurrences:
        raise LedgerError("Use --context or --all-occurrences, not both.",
                          "Approve one occurrence, or every occurrence.")
    if args.approve is not None:
        term = _nfc(args.approve.strip())
        if not term:
            raise LedgerError("--approve needs a term.",
                              "Pass the name exactly as names-check showed it.")
        if args.all_occurrences:
            context = ""
        elif args.context and _collapse(args.context):
            context = _collapse(_nfc(args.context))
        else:
            raise LedgerError(
                "An approval needs --context or --all-occurrences.",
                "Pass --context with text around the occurrence, or "
                "--all-occurrences to approve every occurrence of the term.")
        entry = {"term": term, "context": context}
        if context:
            if term.lower() not in context.lower():
                raise LedgerError(
                    "The context %r does not contain %r." % (context, term),
                    "Pass --context with the name and the words around it, "
                    "as names-check showed them.")
            open_hits = [h for h in names_check(texts, roster, approved)
                         if h["term"].lower() == term.lower()]
            if not any(_approval_covers(entry, h["term"], h["context"])
                       for h in open_hits):
                shown = "; ".join('"%s"' % access.body(h["context"])
                                  for h in open_hits)
                raise LedgerError(
                    "The context %r matches no open occurrence of %r."
                    % (context, term),
                    "Use text from one of these contexts: %s"
                    % (shown or "(no open occurrences of this term)"))
        if entry not in approved:
            approved = approved + [entry]
            home.write_json(approved, "reports", as_of.isoformat(), APPROVED)
    found = names_check(texts, roster, approved)
    hits = [dict(h, context=access.body(h["context"])) for h in found]
    if hits:
        lines = ["%d name occurrence(s) in the executive draft for %s:"
                 % (len(hits), as_of.isoformat())]
        lines += ['  %s: %s  "...%s..."' % (h["file"], h["term"], h["context"])
                  for h in hits]
        if not access.full:
            lines.append("The text around each name is hidden under "
                         "agent_text_access. The delivery manager reviews "
                         "it in their own terminal with `ledger --show-text "
                         "names-check`.")
        lines.append("Edit the text, or approve with --approve TERM "
                     "--context TEXT.")
    else:
        lines = ["No names found in the executive draft for %s."
                 % as_of.isoformat()]
    return {"summary": "\n".join(lines), "hits": hits, "ok": not hits}


def add_parser(subparsers) -> None:
    p = subparsers.add_parser(
        "names-check", help="scan the executive draft for engineers' names")
    p.add_argument("--as-of", type=resolve_as_of,
                   help="report date YYYY-MM-DD (default: latest draft)")
    p.add_argument("--approve", metavar="TERM",
                   help="approve an occurrence of TERM")
    p.add_argument("--context", metavar="TEXT",
                   help="text around the approved occurrence")
    p.add_argument("--all-occurrences", action="store_true",
                   help="approve every occurrence of TERM")
    p.set_defaults(func=_names_check_cmd)
