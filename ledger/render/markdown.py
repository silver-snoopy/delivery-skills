"""The executive report as Markdown: no HTML tags, every text escaped."""
import re
from typing import Dict, List

from .fmt import (DASH, agreed_text, bucket_label, carried_rows,
                  carried_split, carry_answer, clip, date_range, days,
                  effective_label, list_lines, other_party_rows,
                  outside_rows, person_days, fmt_date, planned_text, ratio,
                  received_text, require_exec, returns_reason_text,
                  returns_text, split_order, status_detail, team_markers,
                  ticket_url, waiting_days)

_SPECIAL = re.compile(r"([\\|*_`\[\]<>])")
_LISTS = (("delivered", "Delivered"),
          ("awaiting", "Finished, awaiting acceptance"),
          ("not_delivered", "Not delivered"))


def md(text) -> str:
    """Escape Markdown and inline-HTML characters; collapse newlines."""
    text = " ".join(("" if text is None else str(text)).split())
    return _SPECIAL.sub(r"\\\1", text)


def _link(report: Dict, tid) -> str:
    return "[#%d](%s)" % (int(tid), ticket_url(report, tid))


def _table(head: List[str], rows: List[List[str]]) -> List[str]:
    out = ["| %s |" % " | ".join(head),
           "|%s|" % "|".join("---" for _ in head)]
    out += ["| %s |" % " | ".join(r) for r in rows]
    return out


def _status(status: Dict) -> str:
    label = effective_label(status) or DASH
    detail = status_detail(status)
    return md(label + (" (%s)" % detail if detail else ""))


def _team_row(team: Dict) -> List[str]:
    f = team["metrics"]["figures"]
    c = f["carried"]
    name = md(team["name"])
    marks = team_markers(team)
    if marks:
        name += " (%s)" % md("; ".join(marks))
    trend = ", ".join(DASH if v is None else days(v)
                      for v in f.get("trend_delivered") or [])
    return [name, md(team["sprint"]["name"]), "%d" % team["engineers"],
            trend or DASH, "%d" % f["delivered"], "%d" % f["squad_delivered"],
            md(planned_text(f["planned"])),
            "%d" % (c["in_progress"] + c["not_started"]),
            "%d" % c["with_other_party"], md(returns_text(f["returns"])),
            days(f.get("median_days_with_vendor")), _status(team["status"])]


def _team(report: Dict, team: Dict) -> List[str]:
    s = team["sprint"]
    out = ["### %s" % md(team["name"]), "",
           "%s · %s · %s" % (md(s["name"]),
                             md(date_range(s["start"], s["finish"])),
                             _status(team["status"]))]
    marks = team_markers(team)
    if marks:
        out += ["", "_%s_" % md("; ".join(marks))]
    n = team.get("narrative") or {}
    for key, label in _LISTS:
        out += ["", "**%s**" % label, ""]
        entries = n.get(key) or []
        if not entries:
            out.append("- No tickets.")
        for x in entries:
            if x.get("text"):
                ids = " ".join(_link(report, t)
                               for t in x.get("tickets") or [])
                out.append("- **%s.** %s %s" % (md(x.get("heading")),
                                                md(x["text"]), ids))
                continue
            out.append("- **%s.**" % md(x.get("heading")))
            for tid, title, suffix in list_lines(team, key, x):
                line = "  - " + _link(report, tid)
                if title is not None:
                    line += " " + md(title)
                if suffix:
                    line += " " + md(suffix)
                out.append(line)
    f = team["metrics"]["figures"]
    out += ["", "**Where ticket time went**", ""]
    parts = split_order(f.get("time_split") or {})
    if parts:
        out += _table(["Where", "Working days"],
                      [[md(bucket_label(k)), days(v)] for k, v in parts])
    else:
        out.append("No ticket time recorded.")
    out += ["", "Work as received: %s." % md(
        received_text(f.get("received") or {}))]
    if returns_reason_text(team):
        out += ["", "%s." % md(returns_reason_text(team))]
    rows = carried_rows(team)
    out += ["", "**Carried over**", ""]
    if not rows:
        out.append("Nothing carried over.")
    else:
        body = []
        for tid, t in rows:
            a = carry_answer(team, tid)
            body.append([
                "%s %s" % (_link(report, tid), md(clip(t.get("title") or ""))),
                md(t.get("state")), md(t.get("carried_since") or DASH),
                "%d" % (t.get("sprints_carried") or 0), md(agreed_text(a)),
                md(a.get("reason") or DASH), md(a.get("note") or "")])
        out += _table(["Ticket", "State", "Carried since", "Sprints carried",
                       "Agreed with PO?", "Reason", "Note"], body)
    for title, short in (("In progress with another party",
                          other_party_rows(team)),
                         ("Active outside a sprint", outside_rows(team))):
        if short:
            out += ["", "**%s**" % title, ""]
            out += _table(["Ticket", "State"], [[
                "%s %s" % (_link(report, tid), md(clip(t.get("title") or ""))),
                md(t.get("state"))] for tid, t in short])
    return out + [""]


def _group(report: Dict, g: Dict) -> List[str]:
    t = g["totals"]
    split = t["time_split"]
    out = ["## Sprints ending %s" % fmt_date(g["finish"]), "",
           date_range(g["start"], g["finish"]), "",
           "- Delivered by %s: %s of the squad's delivered items" % (
               md(report["vendor_label"]),
               ratio(t["delivered"], t["squad_delivered"])),
           "- Finished, awaiting acceptance: %d" % t["awaiting"],
           "- Carried over: %d (%s)" % (t["carried"], carried_split(t)),
           "- In progress with a vendor engineer: %s working days; "
           "waiting: %s working days" % (days(split.get("vendor", 0.0)),
                                         days(waiting_days(split))),
           "- Available person-days: %s" % person_days(t["available"],
                                                      t["nominal"]), ""]
    out += _table(["Team", "Sprint", "Engineers", "Delivered, last sprints",
                   "Delivered", "Squad total", "Planned", "Carried over",
                   "With another party", "Returns",
                   "Days with a vendor engineer (median)", "Status"],
                  [_team_row(x) for x in g["teams"]])
    out.append("")
    for team in g["teams"]:
        out += _team(report, team)
    return out


def _risks(report: Dict) -> List[str]:
    risks = report.get("risks") or []
    if not risks:
        return []
    out = ["## Risks and asks", ""]
    for r in risks:
        if not isinstance(r, dict):
            r = {"title": r}
        line = "- **%s: %s**" % (md(r.get("kind") or "Risk"),
                                 md(r.get("title")))
        if r.get("text"):
            line += " " + md(r["text"])
        if r.get("tickets"):
            line += " " + " ".join(_link(report, t) for t in r["tickets"])
        if r.get("ask"):
            line += " Ask: " + md(r["ask"])
        out.append(line)
    return out + [""]


def render_markdown(exec_report: Dict) -> str:
    require_exec(exec_report)
    r = exec_report
    out = ["# Delivery report: %s contribution" % md(r["vendor_label"]), "",
           "As of %s." % fmt_date(r["as_of"])]
    if len(r.get("groups", [])) > 1:
        out += ["", "Teams are grouped by sprint dates; totals are shown per "
                "group."]
    if r.get("missing_teams"):
        out += ["", "No analysed sprint for: %s." % md(
            ", ".join(r["missing_teams"]))]
    out.append("")
    if r.get("summary"):
        out += [md(r["summary"]), ""]
    out += _risks(r)
    for g in r.get("groups", []):
        out += _group(r, g)
    out += ["---", "", md(r["footnote"]), ""]
    if r.get("rules_note"):
        out += [md(r["rules_note"]), ""]
    return "\n".join(out)
