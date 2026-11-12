"""Evidence briefs as self-contained HTML, built from the shared page
style, table, pill, trend-bar, link and evidence-card helpers."""
from typing import Dict, List

from . import html_dm, html_team
from .fmt import (DASH, agreed_text, clip, date_range, days, fmt_date,
                  person_days, planned_text, received_text, returns_text,
                  with_elsewhere)
from .html import MARK
from .html_base import e, link, spark, status_cell
from .style import css
from ..history import CARRIED as CARRIED_FLAG

DISCLAIMER = ("This brief lists what the board recorded. It does not "
              "measure effort and is not an assessment of the person.")
NO_FLAGS = "No flags raised"


def _page(title: str, brief: Dict, lead: List[str], body: List[str],
          restricted: bool) -> str:
    period = brief["period"]
    left = ["<h1>%s</h1>" % e(title)] + lead + [
        '<p class="muted">%s · generated %s</p>' % (
            e(date_range(period["start"], period["finish"])),
            e(fmt_date(brief["generated"])))]
    right = '<div><span class="restricted">%s</span></div>' % MARK \
        if restricted else ""
    head = '<header class="head"><div>%s</div>%s</header>' % (
        "".join(left), right)
    foot = '<p class="muted small">%s</p>' % e(brief["footnote"])
    variant = "dm" if restricted else "exec"
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            '<meta name="viewport" content="width=device-width, '
            'initial-scale=1"><title>%s</title><style>%s</style></head>\n'
            "<body%s>\n<div class=\"page\">%s</div>\n</body></html>\n" % (
                e(title), css(variant),
                ' class="view-dm"' if restricted else "",
                "\n".join(x for x in [head] + body + [foot] if x)))


def _table(cols, rows: List[str]) -> str:
    head = "".join("<th>%s</th>" % e(c) for c in cols)
    return ('<div class="tablewrap"><table><thead><tr>%s</tr></thead>'
            "<tbody>%s</tbody></table></div>" % (head, "".join(rows)))


def _section(title: str, inner: str) -> str:
    return "<section><h2>%s</h2>%s</section>" % (e(title), inner)


def _none(text: str) -> str:
    return '<p class="muted small">%s</p>' % e(text)


def _pills(items: List[str], cls) -> str:
    return " ".join('<span class="pill %s">%s</span>' % (cls(x), e(x))
                    for x in items)


def _flags(flags: List[str]) -> str:
    if not flags:
        return '<span class="pill info">%s</span>' % NO_FLAGS
    return _pills(flags, lambda f: "crit" if f == CARRIED_FLAG else "warn")


def _sprint_cell(r: Dict) -> str:
    marker = '<span class="marker">backfilled from history</span>' \
        if r.get("backfilled") else ""
    return "<td>%s%s</td><td class=\"small\">%s</td>" % (
        e(r["sprint"]), marker, e(date_range(r["start"], r["finish"])))


def _ticket_title(brief: Dict, tid, title: str) -> str:
    return '<td class="wide">%s %s</td>' % (link(brief, tid),
                                            e(clip(title or "")))


def _cards(brief: Dict) -> str:
    cards = brief.get("cards") or []
    if not cards:
        return ""
    return _section("Flagged tickets: who held them and what changed", "".join(
        html_dm._card(brief, {"annotations": c.get("annotations") or {}}, c)
        for c in cards))


# ---- person ----------------------------------------------------------------

_PERSON_COLS = ("Team", "Sprint", "Dates", "Available days (of nominal)",
                "Delivered, worked on", "Handed off", "In progress now",
                "Carried over", "Backward moves", "Returns",
                "Days in progress (median)", "Days with nothing in progress",
                "Last board change")


def _person_row(r: Dict) -> str:
    prog, car = r["in_progress"], r["carried"]
    in_prog = "%d" % prog["count"]
    if prog["count"]:
        in_prog += " (oldest %s d)" % days(prog.get("oldest_days"))
    carried = "%d" % car["count"]
    if car["count"]:
        n = car["longest_sprints"]
        carried += " (longest %d sprint%s)" % (n, "" if n == 1 else "s")
    cells = [
        "<td>%s</td>" % e(r["team"]), _sprint_cell(r),
        '<td class="c num">%s of %s</td>' % (days(r["available_days"]),
                                             days(r["nominal_days"])),
        '<td class="c num">%s</td>' % with_elsewhere(
            r["delivered_worked_on"], r.get("delivered_elsewhere") or 0),
        '<td class="c num">%s</td>' % with_elsewhere(
            r["handed_off"], r.get("handed_off_elsewhere") or 0),
        '<td class="c num">%s</td>' % in_prog,
        '<td class="c num">%s</td>' % carried,
        '<td class="c num">%d</td>' % r["backward_moves"],
        '<td class="c num">%d</td>' % r["returns"],
        '<td class="c num">%s</td>' % days(r["median_days_in_progress"]),
        '<td class="c num">%s%s</td>' % (
            days(r["days_nothing_in_progress"]),
            " (%d assigned, not started)" % r["todo_assigned_max"]
            if r.get("todo_assigned_max") else ""),
        "<td>%s</td>" % e(fmt_date(r["last_board_change"])),
    ]
    return "<tr>%s</tr>" % "".join(cells)


def _assignments(brief: Dict) -> str:
    items = []
    for a in brief["person"]["assignments"]:
        items.append("<li>%s: from %s%s, allocation %d%%</li>" % (
            e(a["team"]), e(fmt_date(a["from"])),
            " to %s" % e(fmt_date(a["to"])) if a["to"] else " (open)",
            round(100 * a["allocation"])))
    return _section("Assignments", "<ul>%s</ul>" % "".join(items))


def _totals(brief: Dict) -> str:
    t = brief["totals"]
    cells = [(str(t["delivered_worked_on"]), "Delivered, worked on"),
             (str(t["handed_off"]), "Handed off"),
             (person_days(t["available_days"], t["nominal_days"]),
              "Available days, of nominal"),
             ("%s d" % days(t["days_nothing_in_progress"]),
              "Days with nothing in progress")]
    inner = "".join("<div><b>%s</b><span>%s</span></div>" % (e(v), e(k))
                    for v, k in cells)
    return ('<div class="figures">%s</div><p class="muted small">Sums over '
            "the sprints listed; no averages across teams.</p>" % inner)


def _flags_by_sprint(brief: Dict) -> str:
    rows = ["<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
        e(x["team"]), e(x["sprint"]), _flags(x["flags"]),
        _pills(x["context"], lambda c: "info") or DASH)
        for x in brief["flags_by_sprint"]]
    return _section("Flags by sprint",
                    _table(("Team", "Sprint", "Flags", "Context"), rows))


def _tickets(brief: Dict, title: str, items: List[Dict]) -> str:
    if not items:
        return _section(title, _none("No tickets."))
    rows = ["<tr>%s<td>%s</td><td>%s</td><td>%s</td>"
            '<td class="c num">%s</td></tr>' % (
                _ticket_title(brief, x["id"], x["title"]), e(x["type"]),
                e(x["team"]), e(x["sprint"]), days(x["days"]))
            for x in items]
    return _section(title, _table(("Ticket", "Type", "Team", "Sprint",
                                   "Days in progress with this person"),
                                  rows))


def _now(brief: Dict) -> str:
    items = brief["in_progress_now"]
    if not items:
        return _section("In progress now", _none("Nothing held at the end "
                                                 "of the latest sprint."))
    rows = []
    for x in items:
        a = x.get("answer") or {}
        rows.append("<tr>%s<td class=\"c num\">%d</td><td>%s</td><td>%s</td>"
                    "<td>%s</td><td class=\"wide\">%s</td></tr>" % (
                        _ticket_title(brief, x["id"], x["title"]),
                        x["sprints_carried"], e(x["carried_since"] or DASH),
                        e(agreed_text(a)), e(a.get("reason") or DASH),
                        e(a.get("note") or "")))
    return _section("In progress now", _table(
        ("Ticket", "Sprints carried", "Carried since", "Agreed with PO?",
         "Reason", "Note"), rows))


def _context(brief: Dict) -> str:
    parts = ['<p class="muted small">These are the whole team\'s figures '
             "for each sprint, not this person's.</p>"]
    for c in brief["context"]:
        parts.append("<div><h3>%s · %s (team figures)</h3>%s"
                     '<p class="muted small">Team work as received: %s.</p>'
                     "</div>" % (
                         e(c["team"]), e(c["sprint"]),
                         html_team._time_split(c["team_time_split"]),
                         e(received_text(c["team_received"]))))
    return _section("Team context", "".join(parts))


def render_person_brief(brief: Dict) -> str:
    rows = [_person_row(r) for r in brief["rows"]]
    body = [
        _assignments(brief),
        _section("Sprints", _table(_PERSON_COLS, rows) + _totals(brief)),
        _flags_by_sprint(brief),
        _tickets(brief, "Delivered, worked on", brief["delivered"]),
        _tickets(brief, "Handed off", brief["handed_off"]),
        _now(brief), _cards(brief), _context(brief),
    ]
    lead = ["<p>%s</p>" % e(DISCLAIMER)]
    return _page("Evidence brief: %s" % brief["person"]["name"], brief,
                 lead, body, restricted=True)


# ---- team ------------------------------------------------------------------

_TEAM_COLS = ("Sprint", "Dates", "Delivered", "Squad total", "Planned",
              "Finished, awaiting acceptance", "Carried over",
              "With another party", "Returns",
              "Days with a vendor engineer (median)",
              "Elapsed days (median)", "Available person-days (of nominal)",
              "Status")


def _team_row(r: Dict) -> str:
    awaiting = "%d" % r["awaiting"]
    if r["awaiting"]:
        awaiting += " (longest %s d)" % days(r["awaiting_longest_days"])
    carried = "%d" % r["carried"]
    if r["carried"]:
        carried += (" (%d agreed in advance, %d not, %d not yet answered)"
                    % (r["carried_agreed"], r["carried_not_agreed"],
                       r["carried_unanswered"]))
    cells = [
        _sprint_cell(r),
        '<td class="c num">%d</td>' % r["delivered"],
        '<td class="c num">%d</td>' % r["squad_delivered"],
        '<td class="small">%s</td>' % e(planned_text(r["planned"])),
        '<td class="num">%s</td>' % e(awaiting),
        '<td class="num">%s</td>' % e(carried),
        '<td class="c num">%d</td>' % r["with_other_party"],
        '<td class="num">%s</td>' % e(returns_text(r["returns"])),
        '<td class="c num">%s</td>' % days(r["median_days_with_vendor"]),
        '<td class="c num">%s</td>' % days(r["median_elapsed_days"]),
        '<td class="c num">%s</td>' % e(person_days(r["available"],
                                                     r["nominal"])),
        "<td>%s</td>" % status_cell(r["status"]),
    ]
    return "<tr>%s</tr>" % "".join(cells)


def _flagged(brief: Dict) -> str:
    items = brief["flagged"]
    if not items:
        return _section("Flagged tickets, latest sprint",
                        _none("No ticket meets a flag threshold."))
    rows = ["<tr>%s<td>%s</td><td class=\"c num\">%d</td>"
            "<td class=\"c num\">%d</td></tr>" % (
                _ticket_title(brief, x["id"], x["title"]),
                _pills(x["reasons"], lambda r: "warn"),
                x["sprints_carried"], x["returns"]) for x in items]
    return _section("Flagged tickets, latest sprint", _table(
        ("Ticket", "Flags", "Sprints carried", "Returns"), rows))


def _risks(brief: Dict) -> str:
    items = []
    for r in brief["risks"]:
        kind = r.get("kind") or "Risk"
        body = ""
        if r.get("text") or r.get("tickets"):
            body += "<p>%s %s</p>" % (e(r.get("text") or ""), " ".join(
                link(brief, t) for t in r.get("tickets") or []))
        if r.get("ask"):
            body += "<p><b>Ask:</b> %s</p>" % e(r["ask"])
        items.append('<div class="risk"><span class="pill %s">%s</span>'
                     "<b>%s</b>%s</div>" % (
                         "crit" if kind == "Issue" else "warn", e(kind),
                         e(r.get("title") or ""), body))
    return _section("Open risks", "<div>%s</div>" % "".join(items)
                    if items else _none("No open risks for this team."))


def _team_context(brief: Dict) -> str:
    parts = []
    for r in brief["rows"]:
        reasons = ('<p class="muted small">%s.</p>'
                   % e(r["returns_reason_text"])) \
            if r.get("returns_reason_text") else ""
        parts.append("<div><h3>%s</h3>%s<p class=\"muted small\">Work as "
                     "received: %s.</p>%s</div>" % (
                         e(r["sprint"]), html_team._time_split(
                             r["time_split"]),
                         e(received_text(r["received"])), reasons))
    return _section("Where ticket time went, by sprint", "".join(parts))


def render_team_brief(brief: Dict) -> str:
    trend = '<p>Delivered per sprint %s</p>' % spark(
        brief["trend_delivered"], "Delivered per sprint")
    body = [
        _section("Sprints", _table(_TEAM_COLS, [_team_row(r)
                                                for r in brief["rows"]])
                 + trend),
        _flagged(brief), _risks(brief), _team_context(brief),
        "" if brief.get("executive") else _cards(brief),
    ]
    return _page("Evidence brief: %s" % brief["team"], brief, [], body,
                 restricted=not brief.get("executive"))
