import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from ledger.ado.client import AdoClient
from ledger.ado.collect import RUN_FILE, collect_team
from ledger.cli import main
from ledger.config import load_config, load_roster
from ledger.errors import LedgerError
from ledger.home import Home
from ledger.metrics import analyze_team_sprint
from ledger.model import Snapshot
from ledger.testing import fake_ado
from ledger.testing.builder import PortfolioBuilder
from ledger.testing.fake_ado import SQUAD_ACTOR, FakeAdo

SPRINTS = [
    ("S35", "2026-09-28", "2026-10-09"),
    ("S36", "2026-10-12", "2026-10-23"),
    ("S37", "2026-10-26", "2026-11-06"),
    ("S38", "2026-11-09", "2026-11-20"),
]
NOW = datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)
CUTOFF_TEXT = "2026-11-24 09:00"  # NOW in Europe/Zurich


def portfolio():
    b = PortfolioBuilder(tz="Europe/Zurich")
    b.team("Claims", area="Demo\\Claims", sprints=SPRINTS)
    b.team("Data", area="Demo\\Data", sprints=SPRINTS)
    anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
    bence = b.person("bence", "Bence T.", team="Claims", start="2026-01-05")
    dora = b.person("dora", "Dora V.", team="Data", start="2026-01-05")
    qa = b.other("qa")
    # handoff to a non-roster QA person, carried over S37 -> S38, text edits
    b.ticket(1, team="Claims", title="Claim intake form",
             description="Intake v1", acceptance="Saves a claim").on(
        "2026-10-20 09:00", iteration="S37").on(
        "2026-10-26 09:00", state="Active", assignee=anna).edit(
        "2026-10-27 10:00", description="Intake v2 with more detail").on(
        "2026-11-02 09:00", state="In QA", assignee=qa).on(
        "2026-11-09 09:00", state="Active", assignee=anna,
        iteration="S38").on(
        "2026-11-12 09:00", state="Closed")
    b.ticket(2, team="Claims", title="Policy lookup").on(
        "2026-09-28 09:00", iteration="S35", state="Active",
        assignee=bence).on("2026-10-01 09:00", state="Closed")
    # squad-only ticket, closed
    b.squad_ticket(3, team="Claims").on(
        "2026-11-09 09:00", iteration="S38", state="Active").on(
        "2026-11-13 09:00", state="Closed")
    # untouched for months, waiting in QA
    b.ticket(4, team="Claims", title="Old export").on(
        "2026-05-04 09:00", state="Active", assignee=anna).on(
        "2026-05-20 09:00", state="In QA", assignee=qa)
    # in progress at the cut-off
    b.ticket(5, team="Claims", title="Search", parent=50).on(
        "2026-11-10 09:00", iteration="S38", state="Active",
        assignee=bence)
    b.ticket(50, team="Claims", title="Search epic", type="Feature").on(
        "2026-09-01 09:00")
    b.ticket(20, team="Data", title="Pipeline").on(
        "2026-11-10 09:00", iteration="S38", state="Active",
        assignee=dora).on("2026-11-16 09:00", state="Closed")
    return b


def make_home(b, fake, alias_override=None):
    home = Home(Path(tempfile.mkdtemp()))
    home.write_json(b._config_json(), "config.json")
    roster = b._roster_json()
    patch = fake.roster_patch()
    for p in roster["people"]:
        p.update(patch[p["id"]])
        if alias_override and p["id"] in alias_override:
            p["aliases"] = alias_override[p["id"]]
    home.write_json(roster, "roster.json")
    return home, load_config(home), load_roster(home)


def client_for(fake):
    return AdoClient(fake, fake.org_url, fake.project)


def snapshot_dir(home, team="Claims", sprint="demo-claims-s38"):
    return home.team_sprint_dir(team, sprint) / "snapshot"


def all_text(home):
    out = []
    for p in home.root.rglob("*"):
        if p.is_file():
            out.append(p.read_bytes().decode("utf-8", errors="replace"))
    return "\n".join(out)


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.b = portfolio()
        self.fake = FakeAdo(self.b)
        self.home, self.cfg, self.roster = make_home(self.b, self.fake)

    def collect(self, now=NOW, refresh=False, team="Claims", fake=None):
        return collect_team(self.home, self.cfg, self.roster,
                            client_for(fake or self.fake), team, now,
                            refresh=refresh)

    def load(self):
        return Snapshot.load(snapshot_dir(self.home))

    def test_collect_round_trip_equals_builder_snapshot(self):
        r = self.collect()
        self.assertEqual(r["sprint"]["name"], "S38")
        self.assertFalse(r["resumed"])
        got = self.load()
        want = self.b.snapshot(cutoff=CUTOFF_TEXT, team="Claims")
        self.assertEqual(sorted(t.id for t in got.tickets), [1, 2, 4, 5])
        self.assertEqual(sorted(t.id for t in got.squad), [1, 2, 3, 4, 5])
        self.assertEqual(got.sprints, want.sprints)
        for sprint in got.sprints["Claims"][1:]:
            m_got, _ = analyze_team_sprint(got, self.cfg, self.roster,
                                           "Claims", sprint)
            m_want, _ = analyze_team_sprint(want, self.cfg, self.roster,
                                            "Claims", sprint)
            self.assertEqual(m_got["figures"], m_want["figures"],
                             sprint.name)
        t1 = next(t for t in got.tickets if t.id == 1)
        self.assertEqual(t1.title, "Claim intake form")
        self.assertEqual(t1.description, "Intake v2 with more detail")
        self.assertEqual(len({r.desc_hash for r in t1.revisions}), 2)
        t5 = next(t for t in got.tickets if t.id == 5)
        self.assertEqual(t5.parent_title, "Search epic")
        self.assertFalse(snapshot_dir(self.home).joinpath(RUN_FILE).exists())
        self.assertEqual(r["vendor_tickets"], 4)
        self.assertEqual(r["squad_tickets"], 5)

    def test_ticket_untouched_for_months_is_collected(self):
        self.collect()
        t4 = next(t for t in self.load().tickets if t.id == 4)
        self.assertEqual(t4.revisions[-1].state, "In QA")
        self.assertEqual(len(t4.revisions), 2)

    def test_refuses_unfinished_sprint(self):
        early = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
        with self.assertRaises(LedgerError) as ctx:
            self.collect(now=early)
        self.assertIn("no sprint of Claims has ended yet", ctx.exception.message)
        self.assertIn("2026-10-10", ctx.exception.hint)

    def _page_two_request_index(self, tid):
        probe = FakeAdo(self.b, page_cap=2)
        home, cfg, roster = make_home(self.b, probe)
        collect_team(home, cfg, roster, client_for(probe), "Claims", NOW)
        for i, (method, url, _) in enumerate(probe.requests):
            if ("/workItems/%d/revisions" % tid in url
                    and "$skip=2&" in url):
                return i
        self.fail("no page-2 request seen")

    def test_interrupted_then_resumed(self):
        idx = self._page_two_request_index(1)
        fake = FakeAdo(self.b, page_cap=2)
        fake.drop_after(idx)  # request idx (page 2 of ticket 1) fails
        with self.assertRaises(LedgerError):
            self.collect(fake=fake)
        run = json.loads(snapshot_dir(self.home).joinpath(RUN_FILE)
                         .read_text())
        self.assertNotIn(1, run["done"])
        fake.reset_failures()
        later = datetime(2026, 12, 15, 8, 0, tzinfo=timezone.utc)
        r = self.collect(now=later, fake=fake)
        self.assertTrue(r["resumed"])
        snap = self.load()
        self.assertEqual(snap.meta["cutoff"], NOW.isoformat())
        ids = [t.id for t in snap.tickets]
        self.assertEqual(len(ids), len(set(ids)))
        squad_ids = [t.id for t in snap.squad]
        self.assertEqual(len(squad_ids), len(set(squad_ids)))
        t1 = next(t for t in snap.tickets if t.id == 1)
        self.assertEqual([x.rev for x in t1.revisions], [1, 2, 3, 4, 5, 6])
        self.assertEqual(sorted(ids), [1, 2, 4, 5])

    def test_resume_with_different_account_refused(self):
        idx = self._page_two_request_index(1)
        fake = FakeAdo(self.b, page_cap=2)
        fake.drop_after(idx)
        with self.assertRaises(LedgerError):
            self.collect(fake=fake)
        other = FakeAdo(self.b, page_cap=2, account=self.b.other("someone"))
        with self.assertRaises(LedgerError) as ctx:
            self.collect(fake=other)
        self.assertIn("account", ctx.exception.message)
        self.assertIn("--refresh", ctx.exception.hint)
        r = self.collect(fake=other, refresh=True)
        self.assertFalse(r["resumed"])

    def test_revisions_after_cutoff_dropped(self):
        self.b._tickets[0].on("2026-11-25 09:00", state="Active")
        self.b.ticket(60, team="Claims").on(
            "2026-11-26 09:00", state="Active",
            assignee="p:anna")
        self.collect()
        snap = self.load()
        t1 = next(t for t in snap.tickets if t.id == 1)
        self.assertEqual(len(t1.revisions), 6)
        self.assertTrue(all(r.changed <= NOW for r in t1.revisions))
        self.assertNotIn(60, [t.id for t in snap.tickets])
        self.assertEqual(snap.meta["cutoff"], NOW.isoformat())

    def test_sprint_without_dates_skipped_with_warning(self):
        self.fake.dateless_iteration = True
        r = self.collect()
        self.assertTrue(any("Future" in w for w in r["warnings"]))
        names = [s.name for s in self.load().sprints["Claims"]]
        self.assertEqual(names, ["S35", "S36", "S37", "S38"])
        self.assertTrue(any("Future" in w
                            for w in self.load().meta["warnings"]))

    def test_missing_and_unreadable_recorded_in_coverage(self):
        self.home.write_json({"Claims": [99]}, "inventory.json")
        self.fake.unreadable.add(5)
        r = self.collect()
        cov = self.load().meta["coverage"]
        self.assertEqual(cov, {"missing": [99], "unreadable": [5],
                               "malformed": []})
        self.assertEqual((r["missing"], r["unreadable"]), (1, 1))
        self.assertNotIn(5, [t.id for t in self.load().tickets])

    def _non_roster_names(self):
        actors = {SQUAD_ACTOR, self.b.other("sm")}
        for t in self.b._tickets:
            for r in t.revisions:
                for a in (r.by, r.assignee):
                    if a and not a.startswith("p:"):
                        actors.add(a)
        names = set()
        for a in actors:
            ident = self.fake.identity(a)
            names |= {ident["displayName"], ident["uniqueName"]}
        return names

    def test_no_non_roster_identity_on_disk(self):
        names = self._non_roster_names()
        self.assertGreaterEqual(len(names), 6)
        # interrupted run: run file and partial files are on disk
        idx = self._page_two_request_index(4)
        fake = FakeAdo(self.b, page_cap=2)
        fake.drop_after(idx)
        with self.assertRaises(LedgerError):
            self.collect(fake=fake)
        self.assertTrue(snapshot_dir(self.home)
                        .joinpath("tickets.partial.jsonl").exists())
        text = all_text(self.home)
        for n in names:
            self.assertNotIn(n, text)
        fake.reset_failures()
        self.collect(fake=fake)
        text = all_text(self.home)
        for n in names:
            self.assertNotIn(n, text)

    def test_issued_sprint_snapshot_never_rewritten(self):
        self.collect()
        d = snapshot_dir(self.home)
        self.home.write_json({"edition": 1, "as_of": "2026-11-24",
                              "issued_at": "2026-11-24T10:00:00Z"},
                             *d.parent.relative_to(self.home.root).parts,
                             "issued.json")
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in d.iterdir()}
        later = datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)
        r = self.collect(now=later)
        self.assertEqual(r["skipped"], "issued")
        self.assertIn("edition 001", r["summary"])
        self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns)
                          for p in d.iterdir()}, before)

    def _issue_marker(self):
        d = snapshot_dir(self.home)
        self.home.write_json({"edition": 1, "as_of": "2026-11-24",
                              "issued_at": "2026-11-24T10:00:00Z"},
                             *d.parent.relative_to(self.home.root).parts,
                             "issued.json")
        return d

    def test_stale_run_in_issued_sprint_is_not_resumed(self):
        self.collect()
        d = self._issue_marker()
        before = {p: p.read_bytes() for p in d.iterdir()}
        stale = {"cutoff": NOW.isoformat(),
                 "account": {"id": client_for(self.fake).connection()["id"],
                             "name": "x"},
                 "auth_mode": "test", "partitions": {}, "queries_done": [],
                 "done": [], "coverage": {"missing": [], "unreadable": []}}
        self.home.write_json(stale, *d.relative_to(self.home.root).parts,
                             RUN_FILE)
        (d / "tickets.partial.jsonl").write_text("")
        with_run = {p: p.read_bytes() for p in d.iterdir()}
        r = self.collect()
        self.assertEqual(r["skipped"], "issued")
        self.assertTrue(any(RUN_FILE in w and "--refresh" in w
                            for w in r["warnings"]), r["warnings"])
        self.assertEqual({p: p.read_bytes() for p in d.iterdir()}, with_run)
        r = self.collect(refresh=True)
        self.assertEqual(r["skipped"], "issued")
        self.assertEqual({p: p.read_bytes() for p in d.iterdir()}, before)

    def test_issued_skip_names_reopen(self):
        self.collect()
        self._issue_marker()
        self.assertIn("ledger reopen", self.collect()["summary"])

    def test_reopened_latest_sprint_is_collected_again(self):
        from ledger.reopen import reopen
        self.collect()
        self._issue_marker()
        reopen(self.home, "Claims", "demo-claims-s38", "Wrong alias.")
        later = datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)
        r = self.collect(now=later, refresh=True)
        self.assertNotIn("skipped", r)
        self.assertEqual(self.load().meta["cutoff"], later.isoformat())
        sup = snapshot_dir(self.home).parent / "superseded" / "001"
        self.assertEqual(Snapshot.load(sup / "snapshot").meta["cutoff"],
                         NOW.isoformat())

    def test_reopened_older_sprint_cannot_be_collected(self):
        older = self.home.team_sprint_dir("Claims", "demo-claims-s37")
        self.home.write_json([{"edition": 1, "as_of": "2026-11-10",
                               "reopened_at": "2026-11-20T10:00:00Z",
                               "note": "x"}],
                             *older.relative_to(self.home.root).parts,
                             "reopened.json")
        r = self.collect()
        self.assertTrue(any("S37" in w and "not the latest" in w
                            and "analyze" in w for w in r["warnings"]),
                        r["warnings"])

    def test_inventory_updated(self):
        self.home.write_json({"Data": [20]}, "inventory.json")
        self.collect()
        inv = self.home.read_json("inventory.json")
        self.assertEqual(inv["Claims"], [4, 5])
        self.assertEqual(inv["Data"], [20])

    def test_only_get_and_wiql_post_issued(self):
        self.collect()
        self.assertTrue(self.fake.requests)
        for method, url, _ in self.fake.requests:
            if method == "GET":
                continue
            self.assertEqual(method, "POST")
            self.assertTrue(url.split("?")[0].endswith("/_apis/wit/wiql"),
                            url)

    def test_renamed_person_and_apostrophe_alias_found(self):
        ident = {"displayName": "Anna O'Neil", "uniqueName":
                 "anna.o'neil@vendor.example", "id": "id-anna",
                 "descriptor": "aad.x"}
        fake = FakeAdo(self.b, identities={"p:anna": ident})
        home, cfg, roster = make_home(self.b, fake, alias_override={
            "anna": ["anna.k@vendor.example", "anna.o'neil@vendor.example"]})
        collect_team(home, cfg, roster, client_for(fake), "Claims", NOW)
        snap = Snapshot.load(snapshot_dir(home))
        self.assertIn(4, [t.id for t in snap.tickets])
        self.assertIn(1, [t.id for t in snap.tickets])
        queries = [body["query"] for m, _, body in fake.requests if body]
        self.assertTrue(any("anna.k@vendor.example" in q for q in queries))

    def test_refresh_restarts_with_new_cutoff(self):
        idx = self._page_two_request_index(1)
        fake = FakeAdo(self.b, page_cap=2)
        fake.drop_after(idx)
        with self.assertRaises(LedgerError):
            self.collect(fake=fake)
        fake.reset_failures()
        later = datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)
        r = self.collect(now=later, refresh=True, fake=fake)
        self.assertFalse(r["resumed"])
        self.assertEqual(self.load().meta["cutoff"], later.isoformat())

    def test_cli_all_with_test_transport(self):
        fake_ado.REGISTRY["collect-test"] = self.fake
        self.addCleanup(fake_ado.REGISTRY.pop, "collect-test", None)
        with mock.patch.dict(os.environ,
                             {"LEDGER_TEST_TRANSPORT": "collect-test"}):
            code, out, err = run_main([
                "--home", str(self.home.root), "--json", "collect", "--all",
                "--as-of", "2026-11-24T08:00:00Z"])
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual([t["team"] for t in result["teams"]],
                         ["Claims", "Data"])
        meta = self.load().meta
        self.assertEqual(meta["auth_mode"], "test")
        self.assertEqual(meta["cutoff"], NOW.isoformat())
        data = Snapshot.load(snapshot_dir(self.home, "Data",
                                          "demo-data-s38"))
        self.assertEqual([t.id for t in data.tickets], [20])

    def test_cli_unknown_test_transport_refused(self):
        with mock.patch.dict(os.environ, {"LEDGER_TEST_TRANSPORT": "nope"}):
            code, out, err = run_main([
                "--home", str(self.home.root), "collect", "--team", "Claims",
                "--as-of", "2026-11-24T08:00:00Z"])
        self.assertEqual(code, 2)
        self.assertIn("nope", err)



IN_S37 = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)   # S36 ended
AFTER_S37 = datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)  # S37 ended


class FixRoundOneTests(unittest.TestCase):
    def setUp(self):
        self.b = portfolio()
        self.fake = FakeAdo(self.b)
        self.home, self.cfg, self.roster = make_home(self.b, self.fake)

    def collect(self, now=NOW, refresh=False, fake=None):
        return collect_team(self.home, self.cfg, self.roster,
                            client_for(fake or self.fake), "Claims", now,
                            refresh=refresh)

    def interrupt_at_first_fetch(self, now):
        probe = FakeAdo(self.b)
        home, cfg, roster = make_home(self.b, probe)
        collect_team(home, cfg, roster, client_for(probe), "Claims", now)
        idx = next(i for i, (_, url, _) in enumerate(probe.requests)
                   if "/revisions" in url)
        fake = FakeAdo(self.b)
        fake.drop_after(idx + 1)  # first ticket done, second one fails
        with self.assertRaises(LedgerError):
            self.collect(now=now, fake=fake)
        fake.reset_failures()
        return fake

    def test_resume_across_sprint_boundary_warns(self):
        fake = self.interrupt_at_first_fetch(IN_S37)
        r = self.collect(now=AFTER_S37, fake=fake)
        self.assertTrue(r["resumed"])
        self.assertEqual(r["sprint"]["name"], "S36")
        stale = [w for w in r["warnings"] if "S37" in w]
        self.assertEqual(len(stale), 1)
        self.assertIn("S36", stale[0])
        self.assertIn("2026-11-02 09:00", stale[0])
        self.assertIn("collect", stale[0])
        self.assertIn("2026-11-02 09:00", r["summary"].splitlines()[0])
        snap = Snapshot.load(snapshot_dir(self.home, sprint="demo-claims-s36"))
        self.assertEqual(snap.meta["cutoff"], IN_S37.isoformat())
        self.assertIn(stale[0], snap.meta["warnings"])
        r2 = self.collect(now=AFTER_S37, fake=fake)
        self.assertFalse(r2["resumed"])
        self.assertEqual(r2["sprint"]["name"], "S37")
        self.assertFalse(any("S36" in w for w in r2["warnings"]))
        self.assertTrue(snapshot_dir(self.home, sprint="demo-claims-s37")
                        .joinpath("meta.json").exists())

    def test_summary_always_has_local_cutoff(self):
        r = self.collect()
        self.assertIn("2026-11-24 09:00", r["summary"].splitlines()[0])

    def test_appended_ticket_fsynced_before_marked_done(self):
        sdir = snapshot_dir(self.home)
        checks = []
        real_fsync = os.fsync

        def spy(fd):
            real_fsync(fd)
            run = json.loads(sdir.joinpath(RUN_FILE).read_text())
            appended = set()
            for name in ("tickets.partial.jsonl", "squad.partial.jsonl"):
                path = sdir / name
                if path.exists():
                    appended |= {json.loads(l)["id"] for l in
                                 path.read_text().splitlines() if l}
            checks.append(appended - set(run["done"]))

        with mock.patch("ledger.ado.collect.os.fsync", side_effect=spy):
            r = self.collect()
        self.assertEqual(len(checks),
                         r["vendor_tickets"] + r["squad_tickets"])
        self.assertTrue(all(checks), checks)

    def test_several_unfinished_runs_refused(self):
        fake = self.interrupt_at_first_fetch(NOW)
        src = snapshot_dir(self.home) / RUN_FILE
        other = snapshot_dir(self.home, sprint="demo-claims-s37")
        other.mkdir(parents=True)
        (other / RUN_FILE).write_text(src.read_text())
        with self.assertRaises(LedgerError) as ctx:
            self.collect(fake=fake)
        self.assertIn("demo-claims-s37", ctx.exception.hint)
        self.assertIn("demo-claims-s38", ctx.exception.hint)
        self.assertIn("--refresh", ctx.exception.hint)
        self.assertFalse(self.collect(fake=fake, refresh=True)["resumed"])

    def test_no_finished_state_warns(self):
        raw = self.b._config_json()
        raw["teams"][0]["phases"] = list(raw["teams"][0]["phases"]) + [
            {"state": "Closed", "phase": "doing", "column": "Odd"},
            {"state": "Removed", "phase": "doing", "column": "Odd"}]
        self.home.write_json(raw, "config.json")
        self.cfg = load_config(self.home)
        r = self.collect()
        w = [x for x in r["warnings"] if "finished state" in x]
        self.assertEqual(len(w), 1)
        self.assertIn("Claims", w[0])
        meta = Snapshot.load(snapshot_dir(self.home)).meta
        self.assertIn(w[0], meta["warnings"])

    def test_interrupted_during_discovery_then_resumed(self):
        full = FakeAdo(self.b)
        home, cfg, roster = make_home(self.b, full)
        collect_team(home, cfg, roster, client_for(full), "Claims", NOW)
        want = Snapshot.load(snapshot_dir(home))
        first_post = next(i for i, (m, _, _) in enumerate(full.requests)
                          if m == "POST")
        query1 = full.requests[first_post][2]["query"]
        fake = FakeAdo(self.b)
        fake.drop_after(first_post + 1)  # first WIQL page ok, probe fails
        with self.assertRaises(LedgerError):
            self.collect(fake=fake)
        run = json.loads(snapshot_dir(self.home).joinpath(RUN_FILE)
                         .read_text())
        self.assertEqual(run["queries_done"], [])
        fake.reset_failures()
        before = len(fake.requests)
        self.collect(fake=fake)
        rerun = [b for m, _, b in fake.requests[before:]
                 if m == "POST" and b["query"] == query1]
        self.assertTrue(rerun)
        got = Snapshot.load(snapshot_dir(self.home))
        self.assertEqual(sorted(t.id for t in got.tickets),
                         sorted(t.id for t in want.tickets))
        self.assertEqual(sorted(t.id for t in got.squad),
                         sorted(t.id for t in want.squad))

if __name__ == "__main__":
    unittest.main()
