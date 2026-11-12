"""The probe export: the only probe artefact allowed to leave the customer machine.

The rule for this module: every exported value is a module constant (matched
by equality and returned as the constant itself), a rebuilt finite number or
boolean, or a field reference name that fully matches STANDARD_FIELD. Nothing
else from the report may be copied: no keys, no strings, no nested objects.
Anything not on the allowlist is dropped. `export_markdown` renders only from
the result of `export`.
"""
import math
import re
from typing import Any, Dict, Optional, Sequence

from .. import __version__

CHECK_IDS = ("ENV-python", "ENV-timezone", "ENV-home", "A1", "A2", "A3", "A4",
             "A5", "A6", "A7", "A8", "A9", "A10", "A11", "A12", "A13", "A14",
             "A15", "A16")
STATUSES = ("observed", "failed", "not checked")
DETAIL_CODES = ("ok", "http_error", "transport_error", "ado_error", "empty",
                "missing_field", "mismatch", "not_contiguous", "unsupported",
                "no_sample", "exception", "dependency_failed", "unknown")
CATEGORIES = ("Proposed", "InProgress", "Resolved", "Completed", "Removed")
STANDARD_FIELD = re.compile(r"^(System|Microsoft\.VSTS)\.[A-Za-z0-9.]+$")
COUNT_KEYS = {"transport_urllib_ok": bool, "transport_az_rest_ok": bool,
              "wiql_ids": int, "tickets_sampled": int, "revisions_sampled": int,
              "identities": int, "iterations_total": int,
              "iterations_dated": int, "board_columns": int,
              "done_reasons": int, "discovery_vendor_unfinished": int,
              "discovery_vendor_recent": int, "discovery_squad": int}
TIMING_KEYS = {"revision_fetches": int, "seconds": float,
               "retry_after_seen": bool, "rate_limit_headers": int,
               "discovery_seconds": float}


def _num(value: Any, kind: type) -> Any:
    if kind is bool:
        return value is True
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value):
        return kind(0)
    return kind(value)


def _pick(value: Any, allowed: Sequence[str], default: Optional[str]) -> Any:
    for a in allowed:
        if value == a:
            return a  # the constant, never the incoming object
    return default


def export(report: Dict) -> Dict:
    """The shareable export: only allowlisted keys, values constructed anew."""
    checks = []
    for c in report.get("checks") or []:
        if not isinstance(c, dict):
            continue
        cid = _pick(c.get("id"), CHECK_IDS, None)
        status = _pick(c.get("status"), STATUSES, None)
        if cid is None or status is None:
            continue
        checks.append({"id": cid, "status": status,
                       "sample": _num(c.get("sample"), int),
                       "detail": _pick(c.get("code"), DETAIL_CODES,
                                       "unknown")})
    timing = report.get("timing") or {}
    counts = report.get("counts") or {}
    shapes = {}
    for endpoint in ("revisions", "boards"):
        names = [n for n in (report.get("shapes") or {}).get(endpoint) or []
                 if isinstance(n, str)]
        standard = sorted({str(n) for n in names if STANDARD_FIELD.fullmatch(n)})
        shapes[endpoint] = {"standard": standard,
                            "other_count": len(names) - len(standard)}
    tally = {c: 0 for c in CATEGORIES + ("other",)}
    for states in (report.get("states") or {}).values():
        for s in states or []:
            cat = s.get("category") if isinstance(s, dict) else None
            tally[_pick(cat, CATEGORIES, "other")] += 1
    return {
        "tool_version": _pick(report.get("tool_version"), (__version__,),
                              "unknown"),
        "checks": checks,
        "timing": {k: _num(timing.get(k), t) for k, t in TIMING_KEYS.items()},
        "shapes": shapes,
        "categories": [{"category": c, "count": n} for c, n in tally.items()],
        "counts": {k: _num(counts.get(k), t) for k, t in COUNT_KEYS.items()},
    }


def export_markdown(report: Dict) -> str:
    e = export(report)
    lines = ["# Delivery Ledger probe export", "",
             "Tool version: %s" % e["tool_version"], "",
             "| Check | Status | Sample | Detail |", "|---|---|---|---|"]
    lines += ["| %s | %s | %d | %s |" % (c["id"], c["status"], c["sample"],
                                         c["detail"]) for c in e["checks"]]
    lines += ["", "## Timing", ""]
    lines += ["- %s: %s" % (k, v) for k, v in e["timing"].items()]
    lines += ["", "## State categories", ""]
    lines += ["- %s: %d" % (c["category"], c["count"])
              for c in e["categories"]]
    lines += ["", "## Response shapes", ""]
    for endpoint, s in e["shapes"].items():
        lines.append("- %s: %s (+%d other fields)" % (
            endpoint, ", ".join(s["standard"]) or "none", s["other_count"]))
    lines += ["", "## Counts", ""]
    lines += ["- %s: %s" % (k, v) for k, v in e["counts"].items()]
    return "\n".join(lines) + "\n"
