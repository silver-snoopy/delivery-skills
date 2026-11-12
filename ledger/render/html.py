"""The executive and delivery manager reports as self-contained HTML."""
from typing import Dict, List

from . import html_team
from .html_base import e, link, spark, status_cell
from .fmt import (carried_split, date_range, days, fmt_date, planned_text,
                  ratio, require_dm, require_exec, returns_text,
                  team_markers, waiting_days)
from ..risks import STALE_NOTE
from .style import css

MARK = "Restricted: delivery manager"
_SCRIPT = """<script>
(function(){
  var b = document.body, e = document.getElementById("v-exec"),
      d = document.getElementById("v-dm");
  function set(dm){
    b.classList.toggle("view-dm", dm);
    e.setAttribute("aria-pressed", String(!dm));
    d.setAttribute("aria-pressed", String(dm));
  }
  e.addEventListener("click", function(){ set(false); });
  d.addEventListener("click", function(){ set(true); });
})();
</script>"""


def _figures(report: Dict, t: Dict) -> str:
    split = t["time_split"]
    cells = [
        ("%s" % e(ratio(t["delivered"], t["squad_delivered"])),
         "Delivered by %s, of the squad's delivered items"
         % report["vendor_label"]),
        ("%d" % t["awaiting"], "Finished, awaiting acceptance"),
        ("%d" % t["carried"], "Carried over: %s" % carried_split(t)),
        ("%s d" % days(split.get("vendor", 0.0)),
         "In progress with a vendor engineer, against %s d waiting"
         % days(waiting_days(split))),
        (e(ratio(int(t["available"]), int(t["nominal"])))
         if float(t["available"]).is_integer()
         and float(t["nominal"]).is_integer()
         else "%s of %s" % (days(t["available"]), days(t["nominal"])),
         "Available person-days, of nominal"),
    ]
    inner = "".join("<div><b>%s</b><span>%s</span></div>" % (v, e(k))
                    for v, k in cells)
    return '<div class="figures">%s</div>' % inner


_COLS = ("Team", "Sprint", "Engineers", "Delivered, last sprints",
         "Delivered", "Squad total", "Planned", "Carried over",
         "With another party", "Returns",
         "Days with a vendor engineer (median)", "Status")


def _team_row(team: Dict) -> str:
    f = team["metrics"]["figures"]
    c = f["carried"]
    markers = "".join('<span class="marker">%s</span>' % e(m)
                      for m in team_markers(team))
    cells = [
        '<td><b>%s</b>%s</td>' % (e(team["name"]), markers),
        "<td>%s</td>" % e(team["sprint"]["name"]),
        '<td class="c num">%d</td>' % team["engineers"],
        "<td>%s</td>" % spark(f.get("trend_delivered") or [],
                              "Delivered per sprint"),
        '<td class="c num">%d</td>' % f["delivered"],
        '<td class="c num">%d</td>' % f["squad_delivered"],
        '<td class="small">%s</td>' % e(planned_text(f["planned"])),
        '<td class="c num">%d</td>' % (c["in_progress"] + c["not_started"]),
        '<td class="c num">%d</td>' % c["with_other_party"],
        '<td class="num">%s</td>' % e(returns_text(f["returns"])),
        '<td class="c num">%s</td>' % days(f.get("median_days_with_vendor")),
        "<td>%s</td>" % status_cell(team["status"]),
    ]
    return "<tr>%s</tr>" % "".join(cells)


def _team_table(teams: List[Dict]) -> str:
    head = "".join("<th>%s</th>" % e(c) for c in _COLS)
    rows = "".join(_team_row(t) for t in teams)
    return ('<div class="tablewrap"><table><thead><tr>%s</tr></thead>'
            "<tbody>%s</tbody></table></div>" % (head, rows))


def _group(report: Dict, g: Dict, variant: str, evidence: Dict) -> str:
    parts = ['<section class="group"><div><h2>Sprints ending %s</h2>'
             '<p class="muted">%s</p></div>' % (
                 e(fmt_date(g["finish"])), e(date_range(g["start"],
                                                        g["finish"]))),
             _figures(report, g["totals"]), _team_table(g["teams"]),
             "</section>"]
    for team in g["teams"]:
        parts.append(html_team.team_block(report, team, variant,
                                          evidence.get(team["name"], [])))
    return "".join(parts)


def _risks(report: Dict) -> str:
    risks = report.get("risks") or []
    if not risks:
        return ""
    items = []
    for r in risks:
        if not isinstance(r, dict):
            r = {"title": r}
        kind = r.get("kind") or "Risk"
        body = ""
        if r.get("text") or r.get("tickets"):
            body += "<p>%s %s</p>" % (e(r.get("text") or ""), " ".join(
                link(report, t) for t in r.get("tickets") or []))
        if r.get("ask"):
            body += "<p><b>Ask:</b> %s</p>" % e(r["ask"])
        if report.get("variant") == "dm" and r.get("evidence_ok") is False:
            body += '<p class="muted small">%s</p>' % e(STALE_NOTE)
        items.append('<div class="risk"><span class="pill %s">%s</span>'
                     "<b>%s</b>%s</div>" % (
                         "crit" if kind == "Issue" else "warn", e(kind),
                         e(r.get("title") or ""), body))
    return "<section><h2>Risks and asks</h2><div>%s</div></section>" % \
        "".join(items)


def _header(report: Dict, variant: str) -> str:
    left = ["<h1>Delivery report: %s contribution</h1>"
            % e(report["vendor_label"]),
            '<p class="muted">As of %s.</p>' % e(fmt_date(report["as_of"]))]
    if len(report.get("groups", [])) > 1:
        left.append('<p class="muted">Teams are grouped by sprint dates; '
                    "totals are shown per group.</p>")
    if report.get("missing_teams"):
        left.append('<p class="muted small">No analysed sprint for: %s.</p>'
                    % e(", ".join(report["missing_teams"])))
    right = ""
    if variant == "dm":
        right = ('<div><span class="restricted">%s</span>'
                 '<div class="switch" role="group" aria-label="Report view">'
                 '<button id="v-exec" type="button" aria-pressed="false">'
                 "Executive view</button>"
                 '<button id="v-dm" type="button" aria-pressed="true">'
                 "Delivery manager view</button></div></div>" % MARK)
    return '<header class="head"><div>%s</div>%s</header>' % (
        "".join(left), right)


def render_html(report: Dict, variant: str) -> str:
    if variant == "exec":
        require_exec(report)
    elif variant == "dm":
        require_dm(report)
    else:
        raise ValueError("variant must be 'exec' or 'dm', not %r" % variant)
    evidence = report.get("evidence") or {} if variant == "dm" else {}
    body = [_header(report, variant)]
    if report.get("summary"):
        body.append("<section><p>%s</p></section>" % e(report["summary"]))
    body.append(_risks(report))
    for g in report.get("groups", []):
        body.append(_group(report, g, variant, evidence))
    body.append('<p class="muted small">%s</p>' % e(report["footnote"]))
    if report.get("rules_note"):
        body.append('<p class="muted small">%s</p>'
                    % e(report["rules_note"]))
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            '<meta name="viewport" content="width=device-width, '
            'initial-scale=1"><title>Delivery report %s</title>'
            "<style>%s</style></head>\n<body%s>\n<div class=\"page\">%s</div>"
            "\n%s</body></html>\n" % (
                e(report["as_of"]), css(variant),
                ' class="view-dm"' if variant == "dm" else "",
                "\n".join(b for b in body if b),
                _SCRIPT + "\n" if variant == "dm" else ""))
