"""One metrics document per team sprint, plus a ledger naming the tickets
behind every figure so each number can be traced."""
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .buckets import first_vendor_hold, time_split
from .classify import (CarryAge, carry_age, held_by_vendor_at_end,
                       involvement, is_member, is_planned, left_sprint,
                       outcome, workers)
from .config import Config, Roster
from .model import Phase, Revision, Snapshot, Sprint, Ticket, _dt_in
from .people import person_observations
from .phases import PhaseMapper
from .received import received_state
from .scope import party, placed_by, removed_by, split
from .timeline import Event, Timeline, build_timeline, find_bulk_edits
from .workcal import WorkCalendar, display_days

RULES_VERSION = "1"
SCHEMA = 1

_TICK = timedelta(microseconds=1)
_CARRIED = ("carried_in_progress", "carried_not_started")
_PERSON_DURATIONS = ("available_days", "nominal_days",
                     "days_nothing_in_progress", "concurrency_avg",
                     "pickup_median", "joiner_days_to_first_delivery")


def _round(x: Optional[float]) -> Optional[float]:
    return None if x is None else display_days(x)


def _median(xs: List[float]) -> Optional[float]:
    return display_days(statistics.median(xs)) if xs else None


def _rev_at(ticket: Ticket, at: datetime) -> Optional[Revision]:
    last = None
    for r in sorted(ticket.revisions, key=lambda r: (r.changed, r.rev)):
        if r.changed <= at:
            last = r
    return last


class _Ctx:
    def __init__(self, snap: Snapshot, cfg: Config, roster: Roster,
                 team: str, sprint: Sprint):
        self.snap, self.cfg, self.roster = snap, cfg, roster
        self.team, self.sprint = team, sprint
        self.cal = WorkCalendar(cfg.timezone, cfg.holidays)
        self.cutoff = _dt_in(snap.meta["cutoff"])
        self.last = sprint.end - _TICK
        self.team_sprints = snap.sprints[team]
        idx = self.team_sprints.index(sprint)
        self.earlier = self.team_sprints[:idx]
        self.upto = self.team_sprints[:idx + 1]

    def owner(self, ticket: Ticket) -> Optional[str]:
        """Team owning the ticket's area at the sprint's last instant, or
        None; also None-safe for tickets not yet created."""
        r = _rev_at(ticket, min(self.last, self.cutoff - _TICK))
        return None if r is None else self.cfg.team_for_area(r.area)

    def counted_type(self, ticket: Ticket) -> bool:
        r = _rev_at(ticket, min(self.last, self.cutoff - _TICK))
        return r is not None and r.type in self.cfg.unit_types

    def timeline(self, ticket: Ticket, bulk) -> Timeline:
        owner = self.owner(ticket) or self.team
        mapper = PhaseMapper(self.cfg.team(owner), self.cfg)
        return build_timeline(ticket, mapper, self.cutoff, bulk)

    def in_period(self, e: Event) -> bool:
        return not e.admin and self.sprint.period_start <= e.at < \
            self.sprint.end

    def vendor(self, actor: Optional[str], at: datetime) -> bool:
        return self.roster.vendor_on(actor, self.team, at, self.cal.tz)


@dataclass
class _Rec:
    ticket: Ticket
    tl: Timeline
    inv: str
    out: str
    outside: bool
    workers: Dict[str, float]
    carry: CarryAge
    held: bool
    holder: Optional[str]

    @property
    def id(self) -> int:
        return self.ticket.id


def _holder_label(ctx: _Ctx, actor: Optional[str]) -> Optional[str]:
    """A roster id, "other" or None; never a pseudonym."""
    if not actor:
        return None
    if actor.startswith("p:") and any(p.id == actor[2:]
                                      for p in ctx.roster.people):
        return actor[2:]
    return "other"


def _collect(ctx: _Ctx) -> Tuple[List[_Rec], Dict[int, Timeline]]:
    """The team's ticket records, and the timeline of every collected
    ticket of a counted type whatever its area: a person's own figures
    are measured over all of those."""
    th = ctx.cfg.thresholds
    bulk = find_bulk_edits(ctx.snap.tickets, int(th["bulk_edit_count"]),
                           int(th["bulk_edit_seconds"]))
    recs = []
    every: Dict[int, Timeline] = {}
    for t in ctx.snap.tickets:
        if not ctx.counted_type(t):
            continue
        owner = ctx.owner(t)
        tl = ctx.timeline(t, bulk)
        every[t.id] = tl
        if owner is not None and owner != ctx.team:
            continue
        inv = involvement(tl, ctx.sprint, ctx.team, ctx.roster, ctx.cal,
                          float(th["worked_min_days"]))
        out = outcome(tl, ctx.sprint, ctx.earlier)
        if inv is None or out is None:
            continue
        w = workers(tl, ctx.sprint.end, ctx.team, ctx.roster, ctx.cal)
        end = tl.at(ctx.last)
        holder = end.holder if end else None
        if owner is None and not (w or ctx.vendor(holder, ctx.last)):
            continue
        recs.append(_Rec(
            t, tl, inv, out, owner is None, w,
            carry_age(tl, ctx.upto, ctx.team, ctx.roster, ctx.cal),
            held_by_vendor_at_end(tl, ctx.sprint, ctx.team, ctx.roster,
                                  ctx.cal),
            holder))
    return recs, every


# ---- per-ticket helpers ----------------------------------------------------

def _first_doing(tl: Timeline) -> Optional[datetime]:
    i = tl.first(lambda i: i.phase == Phase.DOING)
    return i.start if i else None


def _delivered_at(tl: Timeline, before: datetime) -> Optional[datetime]:
    at = None
    for e in tl.events:
        if e.to_phase == Phase.DONE and e.from_phase != Phase.DONE \
                and e.at < before:
            at = e.at
    return at


def _days_waiting(ctx: _Ctx, r: _Rec) -> float:
    if r.out != "awaiting_acceptance":
        return 0.0
    ivs = r.tl.intervals
    k = next(k for k, i in enumerate(ivs) if i.start <= ctx.last < i.end)
    while k > 0 and ivs[k - 1].phase == Phase.HANDOFF \
            and ivs[k - 1].end == ivs[k].start:
        k -= 1
    return ctx.cal.working_days(ivs[k].start, ctx.sprint.end)


def _handoffs(ctx: _Ctx, r: _Rec) -> List[Event]:
    """Hand-offs by a vendor person on the team inside the period."""
    return [e for e in r.tl.events
            if e.to_handoff and ctx.in_period(e)
            and ctx.vendor(e.holder_before, e.at)]


def _handed_off_by(ctx: _Ctx, r: _Rec) -> List[str]:
    """Vendor actors who handed the ticket off inside the period."""
    return [e.holder_before for e in _handoffs(ctx, r)]


def _attributed(ctx: _Ctx, tl: Timeline, e: Event) -> Optional[str]:
    """Roster id holding the latest DOING interval before the event, when
    they were on the team then."""
    best = None
    for i in tl.intervals:
        if i.phase == Phase.DOING and i.start < e.at:
            best = i
    if best is None or not ctx.vendor(best.holder, min(best.end, e.at)
                                      - _TICK):
        return None
    return best.holder[2:]


def _since_vendor(ctx: _Ctx, tl: Timeline) -> Tuple[List[Event],
                                                    List[Event]]:
    """Non-administrative events before the sprint's end, split into those
    before the first vendor hold on the team and those from it on. Only
    the second list is counted in a figure, a flag, a status or a risk."""
    first = first_vendor_hold(tl, ctx.team, ctx.roster, ctx.cal.tz)
    upto = [e for e in tl.events if not e.admin and e.at < ctx.sprint.end]
    if first is None:
        return upto, []
    return ([e for e in upto if e.at < first],
            [e for e in upto if e.at >= first])


def _ticket_record(ctx: _Ctx, r: _Rec, received: Dict[str, Any]
                   ) -> Dict[str, Any]:
    last_rev = _rev_at(r.ticket, ctx.last)
    end = r.tl.at(ctx.last)
    before, upto = _since_vendor(ctx, r.tl)
    handoffs = _handoffs(ctx, r)
    member = is_member(r.tl, ctx.sprint)
    at_start = member and is_planned(
        r.tl, ctx.sprint, ctx.cal,
        int(ctx.cfg.thresholds["planned_grace_days"]))
    placed = placed_by(r.tl, ctx.sprint) if member else None
    left = left_sprint(r.tl, ctx.sprint, ctx.team_sprints)
    removed = removed_by(r.tl, ctx.sprint) if left else None
    return {
        "type": last_rev.type, "title": r.ticket.title,
        "parent": last_rev.parent, "parent_title": r.ticket.parent_title,
        "state": end.state, "outcome": r.out, "involvement": r.inv,
        "planned": r.inv == "worked" and at_start,
        "left_sprint": left,
        "in_sprint_at_start": at_start,
        "placed_by": party(placed.by) if placed is not None else None,
        "removed_by": party(removed.by) if removed is not None else None,
        "workers": {p: display_days(d) for p, d in sorted(r.workers.items())},
        "sprints_carried": r.carry.sprints_carried,
        "carried_since": r.carry.carried_since, "trail": r.carry.trail,
        "sprints_waiting": r.carry.sprints_waiting,
        "days_waiting": display_days(_days_waiting(ctx, r)),
        "wait_label": end.wait_label if end.phase == Phase.HANDOFF else None,
        "backward_moves": sum(1 for e in upto if e.backward),
        "returns": [{"rev": e.rev, "at": e.at.isoformat(),
                     "reopen": e.reopen, "in_sprint": ctx.in_period(e)}
                    for e in upto if e.is_return],
        "backward_moves_before_vendor": sum(1 for e in before if e.backward),
        "returns_before_vendor": sum(1 for e in before if e.is_return),
        "handed_off_in_sprint": bool(handoffs),
        "handed_off_at": handoffs[0].at.isoformat() if handoffs else None,
        "received": received,
        "outside_teams": r.outside,
        "held_by_vendor_at_end": r.held,
        "holder_at_end": _holder_label(ctx, r.holder),
    }


# ---- team figures ----------------------------------------------------------

class _Ledger(dict):
    def count(self, key: str, items: list) -> int:
        self[key] = list(items)
        return len(items)


def _outcome_figures(L: _Ledger, recs: List[_Rec], tickets) -> Dict:
    worked = [r for r in recs if r.inv == "worked"]

    def by(name, pool=worked):
        return [r.id for r in pool if r.out == name]

    carried = [r for r in recs if r.out in _CARRIED]
    planned = [r for r in worked if tickets[str(r.id)]["planned"]]

    def plan(r):
        if r.out == "closed_not_delivered":
            return "not_delivered"
        if tickets[str(r.id)]["left_sprint"]:
            return "left"
        if r.out in ("delivered", "reclosed"):
            return "delivered"
        if r.out == "awaiting_acceptance":
            return "awaiting"
        if r.out in _CARRIED:
            return "carried" if r.held else "with_other_party"
        return "left"

    return {
        "delivered": L.count("figures.delivered", by("delivered")),
        "closed_not_delivered": L.count("figures.closed_not_delivered",
                                        by("closed_not_delivered")),
        "reclosed": L.count("figures.reclosed", by("reclosed")),
        "carried": {
            "in_progress": L.count("figures.carried.in_progress", [
                r.id for r in carried
                if r.held and r.out == "carried_in_progress"]),
            "not_started": L.count("figures.carried.not_started", [
                r.id for r in carried
                if r.held and r.out == "carried_not_started"]),
            "with_other_party": L.count("figures.carried.with_other_party",
                                        [r.id for r in carried
                                         if not r.held])},
        "active_outside_sprint": L.count("figures.active_outside_sprint",
                                         by("active_outside_sprint")),
        "assigned_outside_sprint": L.count(
            "figures.assigned_outside_sprint",
            by("assigned_outside_sprint", recs)),
        "planned": dict(
            total=L.count("figures.planned.total", [r.id for r in planned]),
            **{k: L.count("figures.planned." + k,
                          [r.id for r in planned if plan(r) == k])
               for k in ("delivered", "awaiting", "carried",
                         "with_other_party", "left", "not_delivered")}),
    }


def _scope_figures(L: _Ledger, recs: List[_Rec], tickets) -> Dict:
    """Who decided the sprint's scope, over the team's vendor tickets:
    in the sprint at its start (worked on or only assigned), added after
    the start, and taken out of the sprint before its end."""
    def group(name: str, pick, field: str) -> Dict[str, Any]:
        ids = [r.id for r in recs if pick(tickets[str(r.id)])]
        out: Dict[str, Any] = {"total": L.count("figures.scope.%s.total"
                                                % name, ids)}
        parties = split([tickets[str(i)][field] for i in ids])
        for p in parties:
            L.count("figures.scope.%s.parties.%s" % (name, p),
                    [i for i in ids if tickets[str(i)][field] == p])
        out["parties"] = parties
        return out

    return {
        "at_start": group("at_start", lambda t: t["in_sprint_at_start"],
                          "placed_by"),
        "added": group("added", lambda t: t["placed_by"] is not None
                       and not t["in_sprint_at_start"], "placed_by"),
        "removed": group("removed", lambda t: t["removed_by"] is not None,
                         "removed_by"),
    }


def _flow_figures(ctx: _Ctx, L: _Ledger, recs: List[_Rec], tickets) -> Dict:
    worked = [r for r in recs if r.inv == "worked"]
    waiting = [r for r in worked if r.out == "awaiting_acceptance"]
    events = [{"ticket": r.id, "rev": e.rev} for r in worked
              for e in _since_vendor(ctx, r.tl)[1]
              if e.is_return and ctx.in_period(e)]
    delivered = [r for r in worked if r.out == "delivered"]
    elapsed = {}
    for r in delivered:
        a, z = _first_doing(r.tl), _delivered_at(r.tl, ctx.sprint.end)
        if a is not None and z is not None and a < z:
            elapsed[r.id] = ctx.cal.working_days(a, z)
    split: Dict[str, float] = {}
    for r in worked:
        for k, v in time_split(r.tl, ctx.sprint.start, ctx.sprint.end,
                               ctx.cal, ctx.team, ctx.roster).items():
            split[k] = split.get(k, 0.0) + v
    split = {k: display_days(v) for k, v in sorted(split.items())
             if display_days(v) > 0}
    L["figures.time_split"] = [r.id for r in worked]
    for k in split:
        L["figures.time_split." + k] = [r.id for r in worked]
    L["figures.awaiting_acceptance.longest_wait_days"] = [r.id for r in
                                                          waiting]
    L["figures.median_days_with_vendor"] = [r.id for r in delivered]
    L["figures.median_elapsed_days"] = sorted(elapsed)
    return {
        "awaiting_acceptance": {
            "count": L.count("figures.awaiting_acceptance.count",
                             [r.id for r in waiting]),
            "longest_wait_days": _round(max(
                (_days_waiting(ctx, r) for r in waiting), default=None))},
        "returns": {
            "events": L.count("figures.returns.events", events),
            "tickets": L.count("figures.returns.tickets",
                               sorted({e["ticket"] for e in events})),
            "handed_off": L.count("figures.returns.handed_off", [
                r.id for r in worked
                if tickets[str(r.id)]["handed_off_in_sprint"]])},
        "median_days_with_vendor": _median(
            [sum(r.workers.values()) for r in delivered]),
        "median_elapsed_days": _median(list(elapsed.values())),
        "time_split": split,
    }


def _received_figures(ctx: _Ctx, L: _Ledger, recs: List[_Rec],
                      tickets) -> Dict:
    lo, hi = ctx.sprint.period_start, ctx.sprint.end
    got = []
    for r in recs:
        at = tickets[str(r.id)]["received"]["received_at"]
        if at is not None and lo <= _dt_in(at) < hi:
            got.append((r.id, tickets[str(r.id)]["received"]))
    pick = {"no_criteria": lambda x: x["no_criteria"],
            "text_changed": lambda x: x["text_changes_after"] > 0,
            "added_mid_sprint": lambda x: x["added_mid_sprint"],
            "assigned_late": lambda x: x["assigned_late"]}
    out = {"received": L.count("figures.received.received",
                               [i for i, _ in got])}
    for k, f in pick.items():
        out[k] = L.count("figures.received." + k, [i for i, x in got if f(x)])
    return out


# ---- people ----------------------------------------------------------------

def _own_doing_days(ctx: _Ctx, tl: Timeline, actor: str) -> float:
    return sum(ctx.cal.working_days(i.start, min(i.end, ctx.sprint.end))
               for i in tl.intervals
               if i.phase == Phase.DOING and i.holder == actor
               and i.start < ctx.sprint.end)


def _elsewhere(ctx: _Ctx, actor: str, recs: List[_Rec],
               every: Dict[int, Timeline]) -> Tuple[List[int], List[int]]:
    """Tickets outside the team's own records that the person delivered or
    handed off in this sprint: (delivered ids, handed-off ids)."""
    own = {r.id for r in recs}
    need = float(ctx.cfg.thresholds["worked_min_days"])
    delivered, handed = [], []
    for tid, tl in sorted(every.items()):
        if tid in own:
            continue
        days = _own_doing_days(ctx, tl, actor)
        if days > 0 and days >= need and \
                outcome(tl, ctx.sprint, ctx.earlier) == "delivered":
            delivered.append(tid)
        if any(e.to_handoff and ctx.in_period(e)
               and e.holder_before == actor for e in tl.events):
            handed.append(tid)
    return delivered, handed


def _person(ctx: _Ctx, L: _Ledger, pid: str, recs: List[_Rec],
            tickets, capacity: Dict[str, float],
            every: Dict[int, Timeline]) -> Dict[str, Any]:
    """recs: the team's own tickets. every: all collected timelines; the
    person's days, concurrency, pickup and board changes are measured over
    every ticket they held, whatever its area or owning team."""
    actor, key = "p:" + pid, "people." + pid
    obs = person_observations(pid, ctx.team, ctx.sprint, every, ctx.cal,
                              ctx.roster, ctx.cfg, ctx.team_sprints)
    for k in ("available", "nominal"):
        capacity[k] += obs[k + "_days"]
    for k in _PERSON_DURATIONS:
        obs[k] = _round(obs[k])
    L[key + ".allocation"] = [a.start.isoformat() for a in
                              ctx.roster.person(pid).assignments
                              if a.team == ctx.team]
    held_any = [tid for tid, tl in sorted(every.items())
                if any(i.holder == actor for i in tl.intervals)]
    for k in ("available_days", "nominal_days", "days_nothing_in_progress",
              "concurrency_avg", "concurrency_max", "todo_assigned_max",
              "pickup_median", "joiner_days_to_first_delivery"):
        L[key + "." + k] = held_any
    L[key + ".board_change_days"] = sorted({
        ctx.cal.local_date(rv.changed).isoformat() for tl in every.values()
        for rv in tl.revisions if rv.by == actor
        and ctx.sprint.period_start <= rv.changed < ctx.sprint.end})

    delivered = [r for r in recs if r.out == "delivered"
                 and r.inv == "worked" and pid in r.workers]
    d_else, h_else = _elsewhere(ctx, actor, recs, every)
    ends_with = [r for r in recs if r.holder == actor]
    prog = [r for r in ends_with
            if r.out in ("carried_in_progress", "active_outside_sprint")]
    carried = [r for r in ends_with if r.out in _CARRIED]
    back, rets = [], []
    for r in recs:
        for e in r.tl.events:
            if e.backward and ctx.in_period(e) and \
                    _attributed(ctx, r.tl, e) == pid:
                back.append({"ticket": r.id, "rev": e.rev})
                if e.is_return:
                    rets.append({"ticket": r.id, "rev": e.rev})
    oldest = [ctx.cal.working_days(_first_doing(r.tl), ctx.sprint.end)
              for r in prog if _first_doing(r.tl) is not None]
    L[key + ".in_progress.oldest_days"] = [r.id for r in prog]
    L[key + ".carried.longest_sprints"] = [r.id for r in carried]
    L[key + ".median_days_in_progress"] = [r.id for r in delivered]
    obs.update({
        "delivered_worked_on": L.count(key + ".delivered_worked_on",
                                       [r.id for r in delivered]),
        "handed_off": L.count(key + ".handed_off", [
            r.id for r in recs if actor in _handed_off_by(ctx, r)]),
        "delivered_elsewhere": L.count(key + ".delivered_elsewhere", d_else),
        "handed_off_elsewhere": L.count(key + ".handed_off_elsewhere",
                                        h_else),
        "in_progress": {
            "count": L.count(key + ".in_progress.count",
                             [r.id for r in prog]),
            "oldest_days": _round(max(oldest, default=None))},
        "carried": {
            "count": L.count(key + ".carried.count", [r.id for r in carried]),
            "longest_sprints": max((tickets[str(r.id)]["sprints_carried"]
                                    for r in carried), default=0)},
        "backward_moves": L.count(key + ".backward_moves", back),
        "returns": L.count(key + ".returns", rets),
        "median_days_in_progress": _median([r.workers[pid]
                                            for r in delivered]),
    })
    return obs


# ---- entry points ----------------------------------------------------------

def squad_delivered(snap: Snapshot, cfg: Config, team: str,
                    sprint: Sprint) -> List[int]:
    """Tickets of the team's area delivered in the sprint, whoever held
    them. A vendor ticket the squad file lacks (it left the area after the
    sprint, so the area query no longer finds it) is taken from the vendor
    file: the vendor's delivered tickets are always among the squad's."""
    ctx = _Ctx(snap, cfg, Roster([]), team, sprint)
    out = []
    have = {t.id for t in snap.squad}
    for t in list(snap.squad) + [t for t in snap.tickets
                                 if t.id not in have]:
        if ctx.owner(t) != team or not ctx.counted_type(t):
            continue
        tl = ctx.timeline(t, set())
        if outcome(tl, sprint, ctx.earlier) == "delivered":
            out.append(t.id)
    return out


def analyze_team_sprint(snap: Snapshot, cfg: Config, roster: Roster,
                        team: str, sprint: Sprint) -> Tuple[Dict, Dict]:
    ctx = _Ctx(snap, cfg, roster, team, sprint)
    recs, every = _collect(ctx)
    tickets = {str(r.id): _ticket_record(ctx, r, received_state(
        r.ticket, r.tl, sprint, ctx.cal, team, roster, cfg)) for r in recs}
    mine = [r for r in recs if not r.outside]
    L = _Ledger()
    figures = _outcome_figures(L, mine, tickets)
    figures.update(_flow_figures(ctx, L, mine, tickets))
    figures["scope"] = _scope_figures(L, mine, tickets)
    figures["received"] = _received_figures(ctx, L, mine, tickets)
    squad = squad_delivered(snap, cfg, team, sprint)
    figures["squad_delivered"] = L.count("figures.squad_delivered", squad)

    first = ctx.cal.local_date(sprint.start)
    last = ctx.cal.local_date(ctx.last)
    capacity = {"available": 0.0, "nominal": 0.0}
    people = {p.id: _person(ctx, L, p.id, mine, tickets, capacity, every)
              for p in roster.people_on(team, first, last)}
    figures["capacity"] = {k: display_days(v) for k, v in capacity.items()}
    for k in capacity:
        L["figures.capacity." + k] = sorted(people)

    warnings = []
    extra = sorted(set(L["figures.delivered"]) - set(squad))
    if extra:
        warnings.append("vendor delivered is not a subset of squad "
                        "delivered: %s" % extra)
    cov = snap.meta.get("coverage") or {}
    metrics = {
        "schema": SCHEMA, "rules_version": RULES_VERSION, "team": team,
        "sprint": {"id": sprint.id, "name": sprint.name,
                   "start": first.isoformat(), "finish": last.isoformat()},
        "cutoff": ctx.cutoff.isoformat(), "backfilled": False,
        "collected_as": snap.meta.get("account"),
        "figures": figures, "people": people,
        "tickets": tickets,
        "coverage": {"missing": list(cov.get("missing", [])),
                     "unreadable": list(cov.get("unreadable", [])),
                     "malformed": list(cov.get("malformed", [])),
                     "alarm": bool(snap.meta.get("coverage_alarm"))},
        "warnings": warnings,
    }
    return metrics, dict(L)
