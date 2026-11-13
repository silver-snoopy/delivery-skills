"""`init`: read the board, suggest teams, join dates and counting levels, write config.json and team.csv."""
import os
from collections import Counter
from datetime import date
from typing import Callable, Dict, List, Optional

from ledger.ado import AdoError, Client, az_defaults, cache_path, check_rows, fetch, save_cache
from ledger.config import (CONFIG_FILE, DEFAULT_NOT_DELIVERED, Config, Person, load_config, merge_staff,
                           parse_staff, read_team, save_config, write_team)
from ledger.metrics import histories, level_of
from ledger.replay import History, local_tz, parse_ts
from ledger.xlsx import match_name, read_vacations, resolve_name

WAITING_WORDS = ("review", "qa", "test", "accept", "wait", "blocked", "verify", "uat")
TASKS_FACTOR = 5
TEAM_FILE = "team.csv"


def _main_area(rows: List[dict]) -> str:
    counts = Counter(r["area"] for r in rows if r.get("area"))
    return min(counts, key=lambda a: (-counts[a], a)) if counts else ""


def count_stats(data: dict, members: Dict[str, List[str]], hs: Optional[Dict[int, History]] = None) -> dict:
    """{team: {"stories": n, "tasks": n, "count": level}} from work items done by each team's members."""
    hs = hs if hs is not None else histories(data, DEFAULT_NOT_DELIVERED)
    out = {}
    for team, emails in members.items():
        mine = {e.lower() for e in emails}
        n = {"stories": 0, "tasks": 0}
        for h in hs.values():
            done = h.done_at
            if done is None or not (h.assignees_active(done) & mine):
                continue
            level = level_of(h, data.get("levels") or {}, hs, done)
            if level:
                n[level] += 1
        out[team] = {"count": "tasks" if n["tasks"] > TASKS_FACTOR * n["stories"] else "stories", **n}
    return out


JOINED_MARGIN = 30  # a first activity this close to the window start says nothing about joining


def suggest(data: dict, staff: Dict[str, List[str]], known: List[Person],
            squads: Optional[Dict[str, str]] = None, tz: str = "UTC",
            since: Optional[date] = None) -> dict:
    """Team, join date and counting level suggestions; `since` is the history window start."""
    squads = squads or {}
    zone = local_tz(tz)
    by_email = {}  # type: Dict[str, str]
    for name, emails in staff.items():
        for e in emails:
            by_email[e.lower()] = name
    mine = {name: [] for name in staff}  # type: Dict[str, List[dict]]
    for r in data["revisions"]:
        owner = by_email.get((r.get("assigned") or "").lower())
        if owner:
            mine[owner].append(r)
    known_by = {p.name: p for p in known}
    area_of = {n: _main_area(rows) for n, rows in mine.items()}
    chosen = {}  # type: Dict[str, str]  main area -> team of a colleague
    for n, p in known_by.items():
        if n in staff and p.team and area_of[n]:
            chosen.setdefault(area_of[n], p.team)
    people = []
    for name, emails in staff.items():
        k = known_by.get(name)
        area = area_of[name]
        if k and k.team:
            team = k.team
        elif squads.get(name):
            team = squads[name].strip().upper()
        elif area in chosen:
            team = chosen[area]
        else:
            team = area.replace("/", "\\").split("\\")[-1].upper() if area else ""
        if team and area:
            chosen.setdefault(area, team)
        joined = k.joined if k and k.joined else None
        if joined is None and mine[name]:
            first = min(parse_ts(r["changed"]) for r in mine[name]).astimezone(zone).date()
            if since is None or (first - since).days > JOINED_MARGIN:
                joined = first
        merged = list(k.emails) if k else []
        merged += [e for e in emails if e not in merged]
        people.append({"name": name, "emails": merged, "team": team, "joined": joined,
                       "items": len({r["id"] for r in mine[name]}), "area": area})
    members = {}  # type: Dict[str, List[str]]
    for p in people:
        if p["team"]:
            members.setdefault(p["team"], []).extend(p["emails"])
    states = {}  # type: Dict[str, str]
    for r in data["revisions"]:
        states.setdefault(r["state"], r["category"])
    names = sorted(states)
    waiting = [s for s in names if states[s] in ("InProgress", "Resolved")
               and any(w in s.lower() for w in WAITING_WORDS)]
    return {"people": people, "teams": count_stats(data, members),
            "states": [{"name": s, "category": states[s]} for s in names], "waiting": waiting}


def machine_timezone() -> Optional[str]:
    """The machine's IANA time zone name, or None when it cannot be found."""
    names = [os.environ.get("TZ")]
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            names.append(target.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    for name in names:
        try:
            if name:
                local_tz(name)
                return name
        except ValueError:
            pass
    return None


def _exists(path: str) -> str:
    if not os.path.isfile(path):
        raise ValueError("file not found: %s" % path)
    return path


def _read(path: str) -> str:
    with open(_exists(path), encoding="utf-8-sig") as f:
        return f.read()


def _ask_valid(ask, prompt, default, convert):
    while True:
        answer = ask(prompt, default).strip() or default
        try:
            return convert(answer)
        except ValueError:
            if answer == default:
                raise
            prompt = "%s (not valid, try again)" % prompt


def _match_calendar(vac, staff, cfg, ask):
    """Return ({staff name: squad}, [unmatched calendar names]); new answers go to cfg.name_aliases."""
    squads, unmatched = {}, []
    for cal in vac.days_off:
        target = resolve_name(cal, list(staff), cfg.name_aliases)
        if target is None:
            answer = ask("Which person in staff.txt is '%s'? (blank if none)" % cal, "").strip()
            target = match_name(answer, staff) if answer else None
            if target is not None:
                cfg.name_aliases[cal] = target
        if target is None:
            unmatched.append(cal)
        elif vac.squad.get(cal):
            squads[target] = vac.squad[cal]
    return squads, unmatched


def run_init(args, client: Optional[Client], ask: Callable[[str, str], str], out) -> int:
    d = args.dir
    today = date.today()
    path = os.path.join(d, CONFIG_FILE)
    had_config = os.path.exists(path)
    warnings = []  # type: List[str]
    cfg = load_config(d, warnings.append) if had_config else Config()
    org = args.org or cfg.organization or getattr(client, "org", None)
    project = args.project or cfg.project or getattr(client, "project", None)
    if client is None:
        if not (org and project):
            dorg, dproject = az_defaults()
            org, project = org or dorg, project or dproject
        if not (org and project):
            raise AdoError("the organisation and project are not set",
                           "pass --org and --project, or run az devops configure --defaults "
                           "organization=URL project=NAME")
        client = Client(org, project)
    staff = merge_staff(parse_staff(_read(args.staff), warnings))
    vac = read_vacations(_exists(args.vacations), cfg.half_day_codes)
    squads, unmatched = _match_calendar(vac, staff, cfg, ask)
    tz = args.timezone or (cfg.timezone if had_config else machine_timezone())
    if not tz:
        tz = "UTC"
        warnings.append("could not detect the time zone; using UTC. Set \"timezone\" in config.json "
                        "(for example Europe/Zurich)")
    local_tz(tz)
    team_path = os.path.join(d, TEAM_FILE)
    known = read_team(team_path) if os.path.exists(team_path) else []
    emails = sorted({e for es in staff.values() for e in es})
    data = fetch(client, args.query, emails, today, cfg.history_days,
                 lambda line: print("warning: %s" % line, file=out))
    check_rows(data)
    seen = {r["assigned"] for r in data["revisions"] if r["assigned"]}
    warnings += ["%s is never assigned on the board; check the sign-in email" % e
                 for e in emails if e not in seen]
    sug = suggest(data, staff, known, squads, tz,
                  date.fromordinal(today.toordinal() - cfg.history_days))
    known_by = {p.name: p for p in known}
    people = []
    for s in sug["people"]:
        k = known_by.get(s["name"])
        if k:
            k.emails, k.left = s["emails"], None
            people.append(k)
            continue
        team = (ask("Team? (%s)" % s["name"], s["team"]).strip() or s["team"]).upper()
        default = s["joined"].isoformat() if s["joined"] else ""
        joined = _ask_valid(ask, "Joined the team on? (%s) YYYY-MM-DD" % s["name"], default,
                            lambda t: date.fromisoformat(t) if t else None)
        people.append(Person(s["name"], s["emails"], team, joined, None, 1.0))
    for k in known:
        if k.name not in staff:
            k.left = k.left or today
            people.append(k)
    active = [p for p in people if not p.left]
    members = {}  # type: Dict[str, List[str]]
    for p in active:
        if p.team:
            members.setdefault(p.team, []).extend(p.emails)
    stats = count_stats(data, members)
    for team, st in stats.items():
        entry = cfg.teams.setdefault(team, {})
        if not entry.get("count"):
            prompt = ("Count stories or tasks? (%s: last %d days, %d tasks, %d stories done)"
                      % (team, cfg.history_days, st["tasks"], st["stories"]))
            entry["count"] = _ask_valid(ask, prompt, st["count"], _level)
    cfg.organization, cfg.project, cfg.query, cfg.timezone = org or "", project or "", args.query, tz
    cfg.staff, cfg.vacations = os.path.abspath(args.staff), os.path.abspath(args.vacations)
    if not cfg.waiting_states:
        cfg.waiting_states = sug["waiting"]
    save_cache(d, data)
    cache = cache_path(d)
    write_team(team_path, people)
    save_config(d, cfg)
    _summary(out, people, stats, cfg, sug, unmatched, warnings + vac.warnings,
             [path, team_path, cache])
    return 0


def _level(text: str) -> str:
    if text not in ("stories", "tasks"):
        raise ValueError("answer stories or tasks")
    return text


def _summary(out, people, stats, cfg, sug, unmatched, warnings, paths) -> None:
    w = lambda line="": print(line, file=out)  # noqa: E731
    w("People:")
    for p in people:
        w("  %s | team %s | joined %s%s" % (p.name, p.team or "(none)", p.joined or "unknown",
                                            " | left %s" % p.left if p.left else ""))
    w("Counting level:")
    for team, st in stats.items():
        w("  %s counts %s (done in the window: %d tasks, %d stories)"
          % (team, cfg.teams[team]["count"], st["tasks"], st["stories"]))
    w("States (waiting states in the config are marked *):")
    for s in sug["states"]:
        w("  %s%s: %s" % ("*" if s["name"] in cfg.waiting_states else " ", s["name"], s["category"]))
    w("Proposed waiting states: %s" % (", ".join(sug["waiting"]) or "none"))
    if unmatched:
        w("Calendar names not matched to staff.txt: %s" % ", ".join(unmatched))
    for line in warnings:
        w("warning: %s" % line)
    w("Written:")
    for p in paths:
        w("  %s" % p)
