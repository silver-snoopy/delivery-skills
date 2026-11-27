"""`init`: read the board, suggest teams, join dates and counting levels, write config.json and team.csv."""

import os
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import date

from ledger.ado import AdoError, Client, az_defaults, cache_path, cached_ids, fetch, save_cache
from ledger.config import (
    CONFIG_FILE,
    DEFAULT_NOT_DELIVERED,
    Config,
    Person,
    load_config,
    merge_staff,
    parse_staff,
    read_team,
    save_config,
    team_key,
    write_team,
)
from ledger.metrics import histories, level_of
from ledger.replay import History, local_tz, parse_ts
from ledger.xlsx import match_name, read_vacations, resolve_name

WAITING_WORDS = ("review", "qa", "test", "accept", "wait", "blocked", "verify", "uat")
HOLD_WORDS = ("hold", "blocked", "impeded")
TEST_WORDS = ("qa", "test", "review", "verify", "uat", "accept", "resolved")
TASKS_FACTOR = 5
TEAM_FILE = "team.csv"
JOINED_MARGIN = 30


def _main_area(rows: list[dict]) -> str:
    counts = Counter(r["area"] for r in rows if r.get("area"))
    return min(counts, key=lambda a: (-counts[a], a)) if counts else ""


def count_stats(data: dict, members: dict[str, list[str]], hs: dict[int, History] | None = None) -> dict:
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


def _team_emails(pairs: Iterable[tuple[str, list[str]]]) -> dict[str, list[str]]:
    """Every email per team; entries without a team are skipped."""
    members: dict[str, list[str]] = {}
    for team, emails in pairs:
        if team:
            members.setdefault(team, []).extend(emails)
    return members


def suggest(
    data: dict,
    staff: dict[str, list[str]],
    known: list[Person],
    squads: dict[str, str] | None = None,
    tz: str = "UTC",
    since: date | None = None,
) -> dict:
    """Team, join date and counting level suggestions; `since` is the history window start.
    First activity within JOINED_MARGIN days of `since` is too close to the window start to infer joining."""
    squads = squads or {}
    zone = local_tz(tz)
    by_email: dict[str, str] = {}
    for name, emails in staff.items():
        for e in emails:
            by_email[e.lower()] = name
    mine: dict[str, list[dict]] = {name: [] for name in staff}
    for r in data["revisions"]:
        owner = by_email.get((r.get("assigned") or "").lower())
        if owner:
            mine[owner].append(r)
    known_by = {p.name: p for p in known}
    area_of = {n: _main_area(rows) for n, rows in mine.items()}
    team_by_area: dict[str, str] = {}
    for n, p in known_by.items():
        if n in staff and p.team and area_of[n]:
            team_by_area.setdefault(area_of[n], p.team)
    people: list[dict] = []
    for name, emails in staff.items():
        k = known_by.get(name)
        area = area_of[name]
        if k and k.team:
            team = k.team
        elif squads.get(name):
            team = squads[name].strip().upper()
        elif area in team_by_area:
            team = team_by_area[area]
        else:
            team = area.replace("/", "\\").split("\\")[-1].upper() if area else ""
        if team and area:
            team_by_area.setdefault(area, team)
        joined = k.joined if k and k.joined else None
        if joined is None and mine[name]:
            first = min(parse_ts(r["changed"]) for r in mine[name]).astimezone(zone).date()
            if since is None or (first - since).days > JOINED_MARGIN:
                joined = first
        merged = list(k.emails) if k else []
        merged += [e for e in emails if e not in merged]
        people.append(
            {
                "name": name,
                "emails": merged,
                "team": team,
                "joined": joined,
                "items": len({r["id"] for r in mine[name]}),
                "area": area,
            }
        )
    states: dict[str, str] = {}
    for r in data["revisions"]:
        states.setdefault(r["state"], r["category"])
    names = sorted(states)
    waiting = [
        s
        for s in names
        if states[s] in ("InProgress", "Resolved") and any(w in s.lower() for w in WAITING_WORDS)
    ]
    hold = [s for s in names if any(w in s.lower() for w in HOLD_WORDS)]
    test = [
        s
        for s in names
        if states[s] in ("InProgress", "Resolved") and any(w in s.lower() for w in TEST_WORDS)
    ]
    closures = {
        (r["id"], r["reason"]) for r in data["revisions"] if r["category"] in ("Completed", "Removed")
    }
    return {
        "people": people,
        "teams": count_stats(data, _team_emails((p["team"], p["emails"]) for p in people)),
        "reasons": Counter(reason for _, reason in closures),
        "states": [{"name": s, "category": states[s]} for s in names],
        "waiting": waiting,
        "hold": hold,
        "test": test,
    }


def machine_timezone() -> str | None:
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
        raise ValueError(f"file not found: {path}")
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
            prompt = f"{prompt} (not valid, try again)"


def _match_calendar(vac, staff, cfg, ask):
    """Return ({staff name: squad}, [unmatched calendar names]); new answers go to cfg.name_aliases."""
    squads, unmatched = {}, []
    for cal in vac.days_off:
        target = resolve_name(cal, list(staff), cfg.name_aliases)
        if target is None:
            answer = ask(f"Which person in staff.txt is '{cal}'? (blank if none)", "").strip()
            target = match_name(answer, staff) if answer else None
            if target is not None:
                cfg.name_aliases[cal] = target
        if target is None:
            unmatched.append(cal)
        elif vac.squad.get(cal):
            squads[target] = cfg.squad_teams.get(vac.squad[cal], vac.squad[cal])
    return squads, unmatched


def run_init(args, client: Client | None, ask: Callable[[str, str], str], out) -> int:
    d = args.dir
    today = date.today()
    path = os.path.join(d, CONFIG_FILE)
    had_config = os.path.exists(path)
    warnings: list[str] = []
    cfg = load_config(d, warnings.append) if had_config else Config()
    org = args.org or cfg.organization or getattr(client, "org", None)
    project = args.project or cfg.project or getattr(client, "project", None)
    if client is None:
        if not (org and project):
            dorg, dproject = az_defaults()
            org, project = org or dorg, project or dproject
        if not (org and project):
            raise AdoError(
                "the organisation and project are not set",
                "pass --org and --project, or run az devops configure --defaults "
                "organization=URL project=NAME",
            )
        client = Client(org, project)
    staff = merge_staff(parse_staff(_read(args.staff), warnings))
    vac = read_vacations(_exists(args.vacations), cfg.half_day_codes)
    squads, unmatched = _match_calendar(vac, staff, cfg, ask)
    tz = args.timezone or (cfg.timezone if had_config else machine_timezone())
    if not tz:
        tz = "UTC"
        warnings.append(
            'could not detect the time zone; using UTC. Set "timezone" in config.json '
            "(for example Europe/Zurich)"
        )
    local_tz(tz)
    team_path = os.path.join(d, TEAM_FILE)
    known = read_team(team_path) if os.path.exists(team_path) else []
    emails = sorted({e for es in staff.values() for e in es})

    def warn(line: str) -> None:
        print(f"warning: {line}", file=out)

    data = fetch(client, args.query, emails, today, cfg.history_days, warn, known=cached_ids(d))
    seen = {r["assigned"] for r in data["revisions"] if r["assigned"]}
    warnings += [
        f"{e} is never assigned on the board; check the sign-in email" for e in emails if e not in seen
    ]
    sug = suggest(data, staff, known, squads, tz, date.fromordinal(today.toordinal() - cfg.history_days))
    known_by = {p.name: p for p in known}
    people = []
    for s in sug["people"]:
        k = known_by.get(s["name"])
        if k:
            k.emails, k.left = s["emails"], None
            people.append(k)
            continue
        team = (ask(f"Team? ({s['name']})", s["team"]).strip() or s["team"]).upper()
        default = s["joined"].isoformat() if s["joined"] else ""
        joined = _ask_valid(
            ask,
            f"Joined the team on? ({s['name']}) YYYY-MM-DD",
            default,
            lambda t: date.fromisoformat(t) if t else None,
        )
        people.append(Person(s["name"], s["emails"], team, joined, None, 1.0))
    for k in known:
        if k.name not in staff:
            k.left = k.left or today
            people.append(k)
    stats = count_stats(data, _team_emails((p.team, p.emails) for p in people if not p.left))
    for team, st in stats.items():
        entry = cfg.teams.setdefault(team_key(cfg.teams, team), {})
        if not entry.get("count"):
            prompt = (
                f"Count stories or tasks? ({team}: last {cfg.history_days} days, "
                f"{st['tasks']} tasks, {st['stories']} stories done)"
            )
            entry["count"] = _ask_valid(ask, prompt, st["count"], _level)
    cfg.organization, cfg.project, cfg.query, cfg.timezone = org or "", project or "", args.query, tz
    cfg.staff, cfg.vacations = os.path.abspath(args.staff), os.path.abspath(args.vacations)
    if not cfg.waiting_states:
        cfg.waiting_states = sug["waiting"]
    if not cfg.hold_states:
        cfg.hold_states = sug["hold"]
    if not cfg.test_states:
        cfg.test_states = sug["test"]
    save_cache(d, data, warn)
    cache = cache_path(d)
    write_team(team_path, people)
    save_config(d, cfg)
    _summary(out, people, stats, cfg, sug, unmatched, warnings + vac.warnings, [path, team_path, cache])
    return 0


def _level(text: str) -> str:
    if text not in ("stories", "tasks"):
        raise ValueError("answer stories or tasks")
    return text


def _count_text(count) -> str:
    return ", ".join(count) if isinstance(count, list) else count


def _summary(out, people, stats, cfg, sug, unmatched, warnings, paths) -> None:
    def w(line: str = "") -> None:
        print(line, file=out)

    w("People:")
    for p in people:
        left = f" | left {p.left}" if p.left else ""
        w(f"  {p.name} | team {p.team or '(none)'} | joined {p.joined or 'unknown'}{left}")
    w("Counting level:")
    for team, st in stats.items():
        count_label = _count_text(cfg.teams[team_key(cfg.teams, team)]["count"])
        w(f"  {team} counts {count_label} (done in the window: {st['tasks']} tasks, {st['stories']} stories)")
    w("States (waiting states in the config are marked *):")
    for s in sug["states"]:
        mark = "*" if s["name"] in cfg.waiting_states else " "
        w(f"  {mark}{s['name']}: {s['category']}")
    w(f"Proposed waiting states: {', '.join(sug['waiting']) or 'none'}")
    w(f"Proposed hold states: {', '.join(sug['hold']) or 'none'}")
    w(f"Proposed test states: {', '.join(sug['test']) or 'none'}")
    w(f"Not-delivered reasons (config): {', '.join(cfg.not_delivered_reasons)}")
    cut = {r.lower() for r in cfg.not_delivered_reasons}
    reasons = [
        f"{'*' if (r or '').lower() in cut else ''}{r or '(no reason)'} ({n})"
        for r, n in sorted(sug["reasons"].items(), key=lambda kv: kv[0] or "")
    ]
    w(f"Closure reasons on the board (* = not delivered): {', '.join(reasons) or 'none'}")
    if unmatched:
        w(f"Calendar names not matched to staff.txt: {', '.join(unmatched)}")
    for line in warnings:
        w(f"warning: {line}")
    w("Written:")
    for p in paths:
        w(f"  {p}")
