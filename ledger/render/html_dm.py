"""Delivery-manager-only parts of a team block: the people table, the
coverage note and warnings, and the evidence cards."""
from typing import Dict, List

from ..history import CARRIED as CARRIED_FLAG
from .fmt import (DASH, NAMES_NOTE, OUTCOMES, bucket_label, clip, days,
                  fmt_date, outside_team_rows, with_elsewhere)
from .html_base import e, link, spark

_COLS = ("Engineer", "Available days (of nominal)", "Delivered, last sprints",
         "Delivered, worked on", "Handed off", "In progress now",
         "Carried over", "Backward moves", "Returns",
         "Days in progress (median)", "Days with nothing in progress",
         "Last board change", "Flags", "Context")


def owner_name(report: Dict, t: Dict) -> str:
    who = t.get("holder_at_end")
    if who is None:
        return "unassigned"
    return (report.get("people_names") or {}).get(who, "another party")


def _pills(items: List[str], cls) -> str:
    return " ".join('<span class="pill %s">%s</span>' % (cls(x), e(x))
                    for x in items)


def _person_row(report: Dict, pid: str, p: Dict) -> str:
    name = (report.get("people_names") or {}).get(pid, "another party")
    prog = p.get("in_progress") or {}
    car = p.get("carried") or {}
    in_prog = "%d" % (prog.get("count") or 0)
    if prog.get("count"):
        in_prog += " (oldest %s d)" % days(prog.get("oldest_days"))
    carried = "%d" % (car.get("count") or 0)
    if car.get("count"):
        n = car.get("longest_sprints") or 0
        carried += " (longest %d sprint%s)" % (n, "" if n == 1 else "s")
    flags = p.get("flags") or []
    flag_html = _pills(flags, lambda f: "crit" if f == CARRIED_FLAG
                       else "warn") if flags \
        else '<span class="pill info">No flags raised</span>'
    cells = [
        "<td><b>%s</b></td>" % e(name),
        '<td class="c num">%s of %s</td>' % (days(p.get("available_days")),
                                             days(p.get("nominal_days"))),
        "<td>%s</td>" % spark(p.get("trend_delivered") or [],
                                "Delivered per sprint"),
        '<td class="c num">%s</td>' % with_elsewhere(
            p.get("delivered_worked_on") or 0,
            p.get("delivered_elsewhere") or 0),
        '<td class="c num">%s</td>' % with_elsewhere(
            p.get("handed_off") or 0, p.get("handed_off_elsewhere") or 0),
        '<td class="c num">%s</td>' % in_prog,
        '<td class="c num">%s</td>' % carried,
        '<td class="c num">%d</td>' % (p.get("backward_moves") or 0),
        '<td class="c num">%d</td>' % (p.get("returns") or 0),
        '<td class="c num">%s</td>' % days(p.get("median_days_in_progress")),
        '<td class="c num">%s%s</td>' % (
            days(p.get("days_nothing_in_progress")),
            " (%d assigned, not started)" % p["todo_assigned_max"]
            if p.get("todo_assigned_max") else ""),
        "<td>%s</td>" % e(fmt_date(p.get("last_board_change"))),
        "<td>%s</td>" % flag_html,
        "<td>%s</td>" % (_pills(p.get("context") or [], lambda c: "info")
                         or DASH),
    ]
    return "<tr>%s</tr>" % "".join(cells)


def _people(report: Dict, team: Dict) -> str:
    people = team["metrics"].get("people") or {}
    if not people:
        return ('<div class="dm-only"><h4>People</h4><p class="muted small">'
                "No engineers on this team in this sprint.</p></div>")
    names = report.get("people_names") or {}
    rows = "".join(_person_row(report, pid, people[pid]) for pid in
                   sorted(people, key=lambda x: names.get(x, x)))
    head = "".join("<th>%s</th>" % e(c) for c in _COLS)
    return ('<div class="dm-only"><h4>People</h4><div class="tablewrap">'
            "<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>"
            '<p class="muted small">%s</p></div>' % (head, rows,
                                                     e(NAMES_NOTE)))


def _outside(report: Dict, team: Dict) -> str:
    rows = outside_team_rows(team)
    if not rows:
        return ""
    body = "".join(
        '<tr><td class="wide">%s %s</td><td>%s</td><td>%s</td><td>%s</td>'
        "</tr>" % (link(report, tid), e(clip(t.get("title") or "")),
                   e(t.get("state")), e(owner_name(report, t)),
                   e(OUTCOMES.get(t.get("outcome"), t.get("outcome") or "")))
        for tid, t in rows)
    return ('<div class="dm-only"><h4>Outside configured teams</h4>'
            '<p class="muted small">Tickets a vendor engineer held under an '
            "area no configured team owns. They are not in any team figure; "
            "they count only in the engineer's own days and \"elsewhere\" "
            'counts.</p><div class="tablewrap"><table><thead><tr>'
            "<th>Ticket</th><th>State</th><th>Held by</th><th>Outcome</th>"
            "</tr></thead><tbody>%s</tbody></table></div></div>" % body)


def _coverage(team: Dict) -> str:
    m = team["metrics"]
    cov = m.get("coverage") or {}
    lines = []
    if m.get("collected_as") or m.get("cutoff"):
        cut = (m.get("cutoff") or "")[:16].replace("T", " ")
        lines.append("Collected as: %s; snapshot cut-off %s UTC." % (
            m.get("collected_as") or "unknown", cut or "unknown"))
    if cov.get("missing"):
        lines.append("Coverage: %d item(s) could not be collected: %s." % (
            len(cov["missing"]), ", ".join(str(x) for x in cov["missing"])))
    if cov.get("unreadable"):
        lines.append("Coverage: %d item(s) could not be read: %s." % (
            len(cov["unreadable"]),
            ", ".join(str(x) for x in cov["unreadable"])))
    if cov.get("malformed"):
        lines.append("Coverage: %d item(s) had a history that could not be "
                     "used: %s." % (len(cov["malformed"]), ", ".join(
                         str(x) for x in cov["malformed"])))
    if cov.get("alarm"):
        lines.insert(0, "Coverage alarm: more than a fifth of this team's "
                        "tickets could not be collected. The figures rest "
                        "on a partial board.")
    for w in m.get("warnings") or []:
        lines.append("Warning: %s" % w)
    return "".join('<p class="dm-only muted small">%s</p>' % e(x)
                   for x in lines)


def _answers(team: Dict, tid: int) -> List[str]:
    out = []
    for a in (team.get("annotations") or {}).values():
        if a.get("ticket") != tid:
            continue
        if a.get("kind") == "carry":
            s = "Carryover answer: agreed with PO: %s" % {
                "yes": "Yes", "no": "No"}.get(a.get("agreed") or "",
                                              "Unknown")
        else:
            s = "Return answer"
        if a.get("reason"):
            s += "; reason: %s" % a["reason"]
        if a.get("note"):
            s += "; note: %s" % a["note"]
        out.append(s + ".")
    return out


def _who(segment: Dict) -> str:
    """"<holder>, <phase>", or the phase alone when nobody is named."""
    return "%s, %s" % (segment["holder"], segment["phase"]) \
        if segment["holder"] else segment["phase"]


def _card(report: Dict, team: Dict, c: Dict) -> str:
    total = sum(s["days"] for s in c["segments"])
    segs = "".join('<span class="%s" style="width:%.1f%%" title="%s">%s'
                   "</span>" % (
                       s["kind"], 100.0 * s["days"] / total,
                       e("%s, %s d" % (_who(s), days(s["days"]))),
                       e("%s d" % days(s["days"])))
                   for s in c["segments"]) if total else ""
    keys = []
    for s in c["segments"]:
        if (s["kind"], s["holder"], s["phase"]) not in keys:
            keys.append((s["kind"], s["holder"], s["phase"]))
    legend = "".join('<span><i class="%s"></i>%s</span>' % (
        k, e(_who({"holder": holder, "phase": phase})))
        for k, holder, phase in keys)
    rows = "".join("<tr><td class=\"num\">%s</td><td>%s</td><td>%s</td>"
                   "</tr>" % (e(fmt_date(x["date"])), e(x["what"]),
                              e(x["by"])) for x in c["changes"])
    answers = _answers(team, c["ticket"])
    notes = "".join('<p class="muted small">%s</p>' % e(a)
                    for a in answers) or \
        '<p class="muted small">No answers recorded for this ticket.</p>'
    sentence = ("%s for %s working days; waiting for %s working days." % (
        bucket_label("vendor"), days(c["vendor_days"]),
        days(c["waiting_days"])))
    before = c.get("returns_before_vendor") or 0
    if before:
        sentence += (" %d return%s before a vendor engineer held it (not "
                     "counted)." % (before, "" if before == 1 else "s"))
    if c.get("outside"):
        sentence += " This ticket is outside the configured teams."
    return (
        '<article class="card dm-only" id="evidence-%d"><div class="team-head">'
        "<h3>Evidence: ticket %s %s</h3><div>%s</div></div><p>%s</p>"
        '<div class="timeline" role="img" aria-label="Who held the ticket">'
        '%s</div><div class="legend">%s</div><div class="tablewrap"><table>'
        "<thead><tr><th>Date</th><th>What changed</th><th>Changed by</th>"
        "</tr></thead><tbody>%s</tbody></table></div>%s</article>" % (
            c["ticket"], link(report, c["ticket"]),
            e(clip(c.get("title") or "")),
            " ".join('<span class="pill warn">%s</span>' % e(r)
                     for r in c["reasons"]),
            e(sentence), segs, legend, rows, notes))


def dm_parts(report: Dict, team: Dict, cards: List[Dict]) -> str:
    return (_people(report, team) + _outside(report, team)
            + _coverage(team)
            + "".join(_card(report, team, c) for c in cards))
