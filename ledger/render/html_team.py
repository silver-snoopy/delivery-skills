"""One team's block: the narrative lists, where ticket time went, the
state of work when received and the carried-over tables."""
from typing import Dict, List

from .html_base import e, link, status_cell

from . import html_dm
from .fmt import (agreed_text, bucket_kind, bucket_label, carried_rows,
                  carry_answer, clip, date_range, days, list_lines,
                  other_party_rows,
                  outside_rows, received_text, returns_reason_text,
                  scope_text, load_text,
                  split_order, team_markers)

_LISTS = (("delivered", "Delivered"),
          ("awaiting", "Finished, awaiting acceptance"),
          ("not_delivered", "Not delivered"))


def _titled(report: Dict, team: Dict, key: str, x: Dict) -> str:
    rows = []
    for tid, title, suffix in list_lines(team, key, x):
        if title is None:
            rows.append("<li>%s</li>" % link(report, tid))
            continue
        rows.append("<li>%s %s%s</li>" % (
            link(report, tid), e(title),
            ' <span class="muted small">%s</span>' % e(suffix)
            if suffix else ""))
    return "<li><b>%s.</b><ul>%s</ul></li>" % (e(x.get("heading")),
                                               "".join(rows))


def _list(report: Dict, team: Dict, key: str, entries: List[Dict]) -> str:
    if not entries:
        return '<p class="muted small">No tickets.</p>'
    items = []
    for x in entries:
        if not x.get("text"):
            items.append(_titled(report, team, key, x))
            continue
        ids = " ".join(link(report, t) for t in x.get("tickets") or [])
        items.append("<li><b>%s.</b> %s %s</li>" % (
            e(x.get("heading")), e(x["text"]), ids))
    return "<ul>%s</ul>" % "".join(items)


def _time_split(split: Dict[str, float]) -> str:
    parts = split_order(split)
    total = sum(v for _, v in parts)
    if not total:
        return ("<div><h4>Where ticket time went</h4>"
                '<p class="muted small">No ticket time recorded.</p></div>')
    label = "; ".join("%s %s working days" % (bucket_label(k), days(v))
                      for k, v in parts)
    bar = "".join('<span class="%s" style="width:%.1f%%"></span>' % (
        bucket_kind(k), 100.0 * v / total) for k, v in parts)
    legend = "".join('<span><i class="%s"></i>%s %s d</span>' % (
        bucket_kind(k), e(bucket_label(k)), days(v)) for k, v in parts)
    return ('<div><h4>Where ticket time went</h4><div class="bar" '
            'role="img" aria-label="%s">%s</div><div class="legend">%s</div>'
            "</div>" % (e(label), bar, legend))


def _title_cell(report: Dict, tid: str, t: Dict) -> str:
    return '<td class="wide">%s %s</td>' % (link(report, tid),
                                            e(clip(t.get("title") or "")))


def _carried(report: Dict, team: Dict, variant: str) -> str:
    rows = carried_rows(team)
    if not rows:
        return ('<div><h4>Carried over</h4><p class="muted small">'
                "Nothing carried over.</p></div>")
    owner_th = '<th class="dm-only">Owner</th>' if variant == "dm" else ""
    body = []
    for tid, t in rows:
        a = carry_answer(team, tid)
        owner = ('<td class="dm-only">%s</td>' % e(
            html_dm.owner_name(report, t))) if variant == "dm" else ""
        body.append(
            "<tr>%s%s<td>%s</td><td>%s</td><td class=\"c num\">%s</td>"
            "<td>%s</td><td>%s</td><td class=\"wide\">%s</td></tr>" % (
                _title_cell(report, tid, t), owner, e(t.get("state")),
                e(t.get("carried_since") or "–"),
                t.get("sprints_carried") or 0, e(agreed_text(a)),
                e(a.get("reason") or "–"), e(a.get("note") or "")))
    return ("<div><h4>Carried over</h4><div class=\"tablewrap\"><table>"
            "<thead><tr><th>Ticket</th>%s<th>State</th><th>Carried since"
            "</th><th class=\"c\">Sprints carried</th><th>Agreed with PO?"
            "</th><th>Reason</th><th>Note</th></tr></thead><tbody>%s"
            "</tbody></table></div></div>" % (owner_th, "".join(body)))


def _short_table(report: Dict, title: str, rows) -> str:
    if not rows:
        return ""
    body = "".join("<tr>%s<td>%s</td></tr>" % (
        _title_cell(report, tid, t), e(t.get("state"))) for tid, t in rows)
    return ('<div><h4>%s</h4><div class="tablewrap"><table><thead><tr>'
            "<th>Ticket</th><th>State</th></tr></thead><tbody>%s</tbody>"
            "</table></div></div>" % (e(title), body))


def team_block(report: Dict, team: Dict, variant: str,
               cards: List[Dict]) -> str:
    m = team["metrics"]
    s = team["sprint"]
    n = team.get("narrative") or {}
    markers = "".join('<span class="marker">%s</span>' % e(x)
                      for x in team_markers(team))
    lists = "".join("<div><h4>%s</h4>%s</div>" % (e(label),
                                                  _list(report, team, k,
                                                        n.get(k)))
                    for k, label in _LISTS)
    parts = [
        '<section class="team" id="team-%s"><div class="team-head"><div>'
        "<h2>%s</h2><p class=\"muted small\">%s · %s</p>%s</div><div>%s"
        "</div></div>" % (e("".join(c if c.isalnum() else "-"
                                      for c in team["name"].lower())),
                          e(team["name"]), e(s["name"]),
                          e(date_range(s["start"], s["finish"])), markers,
                          status_cell(team["status"])),
        '<div class="cols">%s</div>' % lists,
        _time_split(m["figures"].get("time_split") or {}),
        ('<p class="muted small">%s.</p>' % e(scope_text(m["figures"])))
        if scope_text(m["figures"]) else "",
        ('<p class="muted small">%s.</p>' % e(load_text(m["figures"])))
        if load_text(m["figures"]) else "",
        '<p class="muted small">Work as received: %s.</p>' % e(
            received_text(m["figures"].get("received") or {})),
        ('<p class="muted small">%s.</p>' % e(returns_reason_text(team)))
        if returns_reason_text(team) else "",
        _carried(report, team, variant),
        _short_table(report, "In progress with another party",
                     other_party_rows(team)),
        _short_table(report, "Active outside a sprint", outside_rows(team)),
    ]
    if variant == "dm":
        parts.append(html_dm.dm_parts(report, team, cards))
    parts.append("</section>")
    return "".join(p for p in parts if p)
