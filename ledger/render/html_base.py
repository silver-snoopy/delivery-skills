"""HTML primitives shared by the report modules: escaping, ticket links,
trend bars and the status cell."""
from html import escape
from typing import Dict, List, Optional

from .fmt import (DASH, STATUS_CLASS, days, effective_label, status_detail,
                  ticket_url)


def e(text) -> str:
    return escape("" if text is None else str(text), quote=True)


def link(report: Dict, tid) -> str:
    return '<a class="id" href="%s">#%d</a>' % (e(ticket_url(report, tid)),
                                                int(tid))


def spark(values: List[Optional[float]], what: str) -> str:
    nums = [v or 0 for v in values]
    top = max(nums) if nums else 0
    bars = "".join('<i style="height:%d%%"></i>' % (
        round(100.0 * v / top) if top else 0) for v in nums)
    label = "%s: %s" % (what, ", ".join(DASH if v is None else days(v)
                                        for v in values))
    return '<span class="spark" role="img" aria-label="%s">%s</span>' % (
        e(label), bars)


def status_cell(status: Dict) -> str:
    label = effective_label(status) or DASH
    out = '<span class="pill %s">%s</span>' % (
        STATUS_CLASS.get(label, "info"), e(label))
    detail = status_detail(status)
    if detail:
        out += ' <span class="small muted">%s</span>' % e(detail)
    return out
