"""Risk candidates (rule-based detectors) and the DM's risk register."""
import contextlib
import copy
import hashlib
import io
import json
import re
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ledger.cli import main
from ledger.config import DEFAULT_THRESHOLDS, load_config, load_roster
from ledger.home import Home
from ledger.names_check import draft_texts, names_check
from ledger.report import build_report, exec_projection
from ledger.testing.portfolio import (AS_OF, CLAIMS, PLATFORM, PRICING,
                                      run_demo)

TH = dict(DEFAULT_THRESHOLDS)
TEAM = "Claims Intake"
FORBIDDEN = ("hands-on", "delivering", "safe to send", "starvation",
             "sole engineer")


class _Cfg:
    thresholds = TH


def ticket(**kw):
    t = {"outcome": "delivered", "sprints_carried": 0, "returns": [],
         "days_waiting": 0.0, "sprints_waiting": 0,
         "held_by_vendor_at_end": True, "outside_teams": False,
         "workers": {"p1": 1.0}, "parent_title": "",
         "received": {"received_at": None, "no_criteria": False,
                      "text_changes_after": 0, "added_mid_sprint": False,
                      "assigned_late": False}}
    t.update(kw)
    return t


def metrics(tickets=None, people=None, rules=None, received=None,
            capacity=None):
    rec = {"received": 0, "no_criteria": 0, "text_changed": 0,
           "added_mid_sprint": 0, "assigned_late": 0}
    rec.update(received or {})
    return {"team": TEAM,
            "sprint": {"id": "s1", "name": "Sprint 9",
                       "start": "2026-10-26", "finish": "2026-11-06"},
            "figures": {"received": rec,
                        "capacity": capacity or {"available": 10.0,
                                                 "nominal": 10.0}},
            "status": {"label": None, "rules": rules or [],
                       "override": None},
            "people": people or {}, "tickets": tickets or {}}


def report_of(m):
    return {"variant": "dm", "risks": [],
            "groups": [{"teams": [{"name": TEAM, "metrics": m}]}]}


def fired(m, detector):
    from ledger.risks import detect
    return [c for c in detect(report_of(m), _Cfg()) if c["detector"] == detector]


def returns(n):
    return [{"rev": i, "at": "2026-10-2%dT09:00:00+00:00" % i,
             "reopen": False} for i in range(n)]


class DetectorRuleTests(unittest.TestCase):
    """Each detector on a fixture built for it, at and just below threshold."""

    def test_chronic_carryover(self):
        n = int(TH["carry_flag_sprints"])
        hit = fired(metrics({"7": ticket(outcome="carried_in_progress",
                                         sprints_carried=n)}),
                    "chronic_carryover")
        self.assertEqual([c["tickets"] for c in hit], [["7"]])
        self.assertEqual(hit[0]["kind"], "Issue")
        self.assertIn("#7", hit[0]["ask"])
        self.assertEqual(fired(metrics({"7": ticket(
            outcome="carried_in_progress", sprints_carried=n - 1)}),
            "chronic_carryover"), [])
        # needs a vendor holder at the end, and not outside the teams
        self.assertEqual(fired(metrics({"7": ticket(
            outcome="carried_in_progress", sprints_carried=n,
            held_by_vendor_at_end=False)}), "chronic_carryover"), [])
        self.assertEqual(fired(metrics({"7": ticket(
            outcome="carried_in_progress", sprints_carried=n,
            outside_teams=True)}), "chronic_carryover"), [])

    def test_repeated_returns(self):
        n = int(TH["returns_flag"])
        rec = dict(ticket()["received"], text_changes_after=2)
        hit = fired(metrics({"8": ticket(returns=returns(n), received=rec)}),
                    "repeated_returns")
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["kind"], "Issue")
        self.assertIn("was edited 2 times", hit[0]["text"])
        plain = fired(metrics({"8": ticket(returns=returns(n))}),
                      "repeated_returns")
        self.assertIn("was not edited", plain[0]["text"])
        self.assertEqual(fired(metrics({"8": ticket(returns=returns(n - 1))}),
                               "repeated_returns"), [])

    def test_long_wait(self):
        d = TH["long_wait_days"]
        s = int(TH["long_wait_sprint_ends"])
        aw = "awaiting_acceptance"
        self.assertEqual(len(fired(metrics({"9": ticket(
            outcome=aw, days_waiting=d + 0.5)}), "long_wait")), 1)
        self.assertEqual(len(fired(metrics({"9": ticket(
            outcome=aw, sprints_waiting=s)}), "long_wait")), 1)
        self.assertEqual(fired(metrics({"9": ticket(
            outcome=aw, days_waiting=d, sprints_waiting=s - 1)}),
            "long_wait"), [])
        self.assertEqual(fired(metrics({"9": ticket(
            outcome="delivered", days_waiting=d + 3)}), "long_wait"), [])

    def test_unready_work(self):
        mn = int(TH["unready_min"])
        nc = dict(ticket()["received"], no_criteria=True,
                  received_at="2026-10-27T08:00:00+00:00")
        ts = {str(i): ticket(received=nc) for i in range(mn)}
        hit = fired(metrics(ts, received={"received": mn, "no_criteria": mn}),
                    "unready_work")
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["kind"], "Risk")
        self.assertEqual(hit[0]["tickets"], sorted(ts))
        # below the minimum count
        self.assertEqual(fired(metrics(ts, received={
            "received": mn, "no_criteria": mn - 1}), "unready_work"), [])
        # below the share: mn of a large intake
        big = int(mn / TH["unready_share"]) + 2
        self.assertEqual(fired(metrics(ts, received={
            "received": big, "no_criteria": mn}), "unready_work"), [])
        # no intake: silent
        self.assertEqual(fired(metrics({}, received={
            "received": 0, "no_criteria": 0}), "unready_work"), [])

    def test_midsprint_additions(self):
        mn = int(TH["midsprint_min"])
        self.assertEqual(len(fired(metrics(received={
            "added_mid_sprint": mn}), "midsprint_additions")), 1)
        self.assertEqual(fired(metrics(received={
            "added_mid_sprint": mn - 1}), "midsprint_additions"), [])

    def test_one_vendor_contributor(self):
        f = "Billing export"
        hit = fired(metrics({
            "1": ticket(parent_title=f, workers={"p1": 2.0}),
            "2": ticket(parent_title=f, workers={"p1": 1.0}),
            "4": ticket(parent_title=f, workers={"p1": 1.0}),
            "3": ticket(parent_title=f, workers={})}),
            "one_vendor_contributor")
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["tickets"], ["1", "2", "4"])
        self.assertEqual(hit[0]["people"], ["p1"])
        self.assertIn(f, hit[0]["title"] + hit[0]["text"])
        # one ticket with workers is below the minimum of two
        self.assertEqual(fired(metrics({
            "1": ticket(parent_title=f, workers={"p1": 2.0}),
            "3": ticket(parent_title=f, workers={})}),
            "one_vendor_contributor"), [])
        # two contributors
        self.assertEqual(fired(metrics({
            "1": ticket(parent_title=f, workers={"p1": 2.0}),
            "2": ticket(parent_title=f, workers={"p2": 1.0})}),
            "one_vendor_contributor"), [])
        # no parent: silent
        self.assertEqual(fired(metrics({
            "1": ticket(workers={"p1": 2.0}),
            "2": ticket(workers={"p1": 1.0})}),
            "one_vendor_contributor"), [])

    def test_fewer_delivered(self):
        self.assertEqual(len(fired(metrics(
            rules=["Fewer delivered than usual"]), "fewer_delivered")), 1)
        self.assertEqual(fired(metrics(rules=["Returns"]),
                               "fewer_delivered"), [])

    def test_nothing_in_progress(self):
        d = TH["nothing_in_progress_days"]
        hit = fired(metrics(people={
            "p1": {"days_nothing_in_progress": d},
            "p2": {"days_nothing_in_progress": d + 1},
            "p3": {"days_nothing_in_progress": None}}),
            "nothing_in_progress")
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["people"], ["p1", "p2"])
        self.assertIn("2 ", hit[0]["text"])
        self.assertEqual(fired(metrics(people={
            "p1": {"days_nothing_in_progress": d - 0.5}}),
            "nothing_in_progress"), [])

    def test_low_capacity(self):
        share = TH["capacity_share"]
        self.assertEqual(len(fired(metrics(capacity={
            "available": 10 * share - 0.5, "nominal": 10.0}),
            "low_capacity")), 1)
        self.assertEqual(fired(metrics(capacity={
            "available": 10 * share, "nominal": 10.0}), "low_capacity"), [])
        self.assertEqual(fired(metrics(capacity={
            "available": 0.0, "nominal": 0.0}), "low_capacity"), [])

    def test_missing_data_is_silent(self):
        m = metrics()
        m["figures"] = {}
        m.pop("status")
        m["tickets"] = {"1": {"outcome": "carried_in_progress"}}
        from ledger.risks import detect
        self.assertEqual(detect(report_of(m), _Cfg()), [])


def rec(**kw):
    r = dict(ticket()["received"])
    r.update(kw)
    return r


class FixRoundTests(unittest.TestCase):
    """Text states only what the data shows; stable team-level ids."""

    def test_returns_text_change_sentence(self):
        n = int(TH["returns_flag"])
        none = fired(metrics({"8": ticket(returns=returns(n), received=rec(
            text_changes_after=None))}), "repeated_returns")[0]["text"]
        zero = fired(metrics({"8": ticket(returns=returns(n), received=rec(
            text_changes_after=0))}), "repeated_returns")[0]["text"]
        one = fired(metrics({"8": ticket(returns=returns(n), received=rec(
            text_changes_after=1))}), "repeated_returns")[0]["text"]
        many = fired(metrics({"8": ticket(returns=returns(n), received=rec(
            text_changes_after=3))}), "repeated_returns")[0]["text"]
        self.assertNotIn("edited", none)
        self.assertNotIn("change", none)
        self.assertIn("was not edited after", zero)
        self.assertIn("was edited 1 time after", one)
        self.assertIn("was edited 3 times after", many)
        for t in (none, zero, one, many):
            self.assertNotIn("requirement", t.lower())

    def test_long_wait_uses_only_present_figures(self):
        aw = "awaiting_acceptance"
        s = int(TH["long_wait_sprint_ends"])
        t = fired(metrics({"9": ticket(outcome=aw, days_waiting=None,
                                       sprints_waiting=s)}),
                  "long_wait")[0]["text"]
        self.assertNotIn("working day", t)
        self.assertIn("%d sprint ends" % s, t)
        t = fired(metrics({"9": ticket(outcome=aw, days_waiting=7.5,
                                       sprints_waiting=None)}),
                  "long_wait")[0]["text"]
        self.assertNotIn("sprint end", t)
        self.assertIn("7.5 working days", t)

    def test_one_contributor_threshold_and_wording(self):
        self.assertEqual(TH["one_contributor_min_tickets"], 3)
        f = "Billing export"

        def ts(n):
            return {str(i): ticket(parent_title=f, workers={"p1": 1.0})
                    for i in range(1, n + 1)}
        self.assertEqual(fired(metrics(ts(2)), "one_vendor_contributor"), [])
        c = fired(metrics(ts(3)), "one_vendor_contributor")[0]
        self.assertEqual(c["kind"], "Risk")
        self.assertEqual(c["title"], "One recorded vendor contributor on "
                                     "'Billing export' in Sprint 9")
        self.assertEqual(c["text"], "In Sprint 9, the 3 tickets under "
                         "'Billing export' that had recorded vendor work "
                         "were all held by the same engineer.")
        self.assertEqual(c["ask"], "Consider whether a second person should "
                         "take part in this feature next sprint.")
        blob = (c["title"] + c["text"] + c["ask"]).lower()
        for w in ("only contributor", "sole", "single point"):
            self.assertNotIn(w, blob)

    def test_team_level_ids_stable_across_sprints(self):
        for det, flag, key in (("unready_work", "no_criteria", "no_criteria"),
                               ("midsprint_additions", "added_mid_sprint",
                                "added_mid_sprint")):
            ids = []
            for tids in (("1", "2", "3"), ("7", "8", "9", "10")):
                m = metrics({t: ticket(received=rec(**{flag: True}))
                             for t in tids},
                            received={"received": len(tids),
                                      key: len(tids)})
                c = fired(m, det)[0]
                self.assertEqual(c["tickets"], list(tids))
                ids.append(c["id"])
            self.assertEqual(ids[0], ids[1], det)
            want = hashlib.sha256(("%s|%s|-" % (det, TEAM))
                                  .encode("utf-8")).hexdigest()[:12]
            self.assertEqual(ids[0], want)

    def test_singular_counts(self):
        m = metrics({"1": ticket(outcome="carried_in_progress",
                                 sprints_carried=1, returns=returns(1)),
                     "2": ticket(outcome="awaiting_acceptance",
                                 days_waiting=1.0, sprints_waiting=1)},
                    people={"p1": {"days_nothing_in_progress": 1}},
                    received={"received": 1, "no_criteria": 1,
                              "added_mid_sprint": 1})
        th = dict(TH, carry_flag_sprints=1, returns_flag=1, long_wait_days=0.5,
                  long_wait_sprint_ends=1, unready_min=1, midsprint_min=1,
                  nothing_in_progress_days=1)

        class C:
            thresholds = th
        from ledger.risks import detect
        cands = detect(report_of(m), C())
        self.assertEqual(len({c["detector"] for c in cands}), 6)
        blob = " ".join(c["title"] + " " + c["text"] for c in cands)
        for bad in ("1 sprints", "1 times", "1 tickets", "1 sprint ends",
                    "1 working days", "1 engineers", "1 days"):
            self.assertNotIn(bad, blob)
        self.assertIn("1 ticket was added", blob)
        self.assertIn("1 sprint end", blob)

    def test_plural_helper(self):
        from ledger.risks import plural
        self.assertEqual(plural(1, "ticket"), "1 ticket")
        self.assertEqual(plural(2, "ticket"), "2 tickets")
        self.assertEqual(plural(1, "sprint end"), "1 sprint end")
        self.assertEqual(plural(1.5, "working day"), "1.5 working days")
        self.assertEqual(plural(1.0, "working day"), "1 working day")


class RegisterShapeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Home(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def attach(self):
        from ledger.risks import attach
        attach(self.home, _Cfg(), report_of(metrics()))

    def test_top_level_list_is_refused(self):
        from ledger.errors import LedgerError
        from ledger.risks import load
        self.home.write_json([], "risks.json")
        with self.assertRaises(LedgerError) as cm:
            self.attach()
        self.assertIn("risks.json", cm.exception.message)
        with self.assertRaises(LedgerError):
            load(self.home)

    def test_entry_without_id_is_refused(self):
        from ledger.errors import LedgerError
        self.home.write_json({"risks": [{"status": "open"}]}, "risks.json")
        with self.assertRaises(LedgerError) as cm:
            self.attach()
        self.assertIn("risks.json", cm.exception.message)


class DemoRiskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name) / "demo"
        run_demo(Home(cls.root))
        cls.home = Home(cls.root)
        cls.cfg, cls.roster = load_config(cls.home), load_roster(cls.home)
        cls.as_of = date.fromisoformat(AS_OF)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        p = self.home.path("risks.json")
        if p.exists():
            p.unlink()

    def detect(self, home=None):
        from ledger.risks import detect
        home = home or self.home
        return detect(build_report(home, self.cfg, self.roster, self.as_of),
                      self.cfg)

    def cli(self, *args, home=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--home", str((home or self.home).root), "--json"]
                        + list(args))
        self.assertEqual(code, 0)
        return json.loads(out.getvalue())

    def find(self, cands, detector, team):
        hits = [c for c in cands if c["detector"] == detector
                and c["team"] == team]
        self.assertTrue(hits, "%s did not fire for %s" % (detector, team))
        return hits

    def test_each_detector_fires_on_demo(self):
        c = self.detect()
        self.assertIn(["40655"], [x["tickets"] for x in
                                  self.find(c, "chronic_carryover", PRICING)])
        self.assertIn(["40655"], [x["tickets"] for x in
                                  self.find(c, "repeated_returns", PRICING)])
        self.assertIn(["40671"], [x["tickets"] for x in
                                  self.find(c, "long_wait", PRICING)])
        self.find(c, "fewer_delivered", PRICING)
        self.find(c, "load_above_usual", CLAIMS)
        self.find(c, "nothing_in_progress", PRICING)
        # "Data quality checks" has 2 tickets: below one_contributor_min_tickets
        self.assertFalse([x for x in c
                          if x["detector"] == "one_vendor_contributor"])
        self.assertFalse([x for x in c if x["team"] == CLAIMS
                          and x["detector"] == "chronic_carryover"])

    def test_candidate_text_has_no_names(self):
        terms = set()
        for p in self.roster.people:
            terms.add(p.display_name)
            terms.add(p.id)
            terms.update(p.aliases)
            terms.update(x for x in re.split(r"\W+", p.display_name)
                         if len(x) >= 4)
        from ledger.risks import detect
        synthetic = detect(report_of(metrics(
            {"1": ticket(parent_title="F", workers={"p1": 1.0}),
             "2": ticket(parent_title="F", workers={"p1": 1.0}),
             "5": ticket(parent_title="F", workers={"p1": 1.0}),
             "3": ticket(outcome="carried_in_progress", sprints_carried=9,
                         returns=returns(4)),
             "4": ticket(outcome="awaiting_acceptance", days_waiting=9)},
            people={"p1": {"days_nothing_in_progress": 9}},
            rules=["Fewer delivered than usual"],
            received={"received": 3, "no_criteria": 3,
                      "added_mid_sprint": 3},
            capacity={"available": 1.0, "nominal": 10.0})), _Cfg())
        self.assertEqual(len({c["detector"] for c in synthetic}), 9)
        for c in self.detect() + synthetic:
            blob = " ".join((c["title"], c["text"], c["ask"]))
            for t in terms | {"p1"}:
                self.assertIsNone(
                    re.search(r"(?<!\w)%s(?!\w)" % re.escape(t), blob, re.I),
                    "%r in %r" % (t, blob))
            for w in FORBIDDEN:
                self.assertNotIn(w, blob.lower())

    def test_ids_stable_across_runs(self):
        a, b = self.detect(), self.detect()
        self.assertEqual([x["id"] for x in a], [x["id"] for x in b])
        self.assertEqual(len({x["id"] for x in a}), len(a))
        c = self.find(a, "chronic_carryover", PRICING)[0]
        want = hashlib.sha256(("chronic_carryover|%s|40655" % PRICING)
                              .encode("utf-8")).hexdigest()[:12]
        self.assertEqual(c["id"], want)

    def test_accept_edit_close_cycle(self):
        res = self.cli("risks", "--as-of", AS_OF, "--candidates")
        cands = res["candidates"]
        self.assertTrue(cands)
        self.assertEqual(res["open"], [])
        cid = [c for c in cands if c["detector"] == "long_wait"][0]["id"]
        self.cli("risks", "--as-of", AS_OF, "--accept", cid,
                 "--text", "Edited text.", "--ask", "Edited ask.")
        reg = self.home.read_json("risks.json")
        entry = [r for r in reg["risks"] if r["id"] == cid][0]
        self.assertEqual((entry["status"], entry["text"], entry["ask"],
                          entry["edited"], entry["opened"], entry["closed"]),
                         ("open", "Edited text.", "Edited ask.", True,
                          AS_OF, None))
        self.assertNotIn("people", entry)
        # no longer offered as a candidate; listed as open
        res = self.cli("risks", "--as-of", AS_OF, "--candidates")
        self.assertNotIn(cid, [c["id"] for c in res["candidates"]])
        self.assertIn(cid, [r["id"] for r in res["open"]])
        # edit survives into the next report
        rep = build_report(self.home, self.cfg, self.roster, self.as_of)
        r = [x for x in rep["risks"] if x["id"] == cid][0]
        self.assertEqual(r["text"], "Edited text.")
        self.assertTrue(r["evidence_ok"])
        # drop another: never offered again
        other = [c["id"] for c in cands if c["id"] != cid][0]
        self.cli("risks", "--as-of", AS_OF, "--drop", other)
        res = self.cli("risks", "--as-of", AS_OF, "--candidates")
        self.assertNotIn(other, [c["id"] for c in res["candidates"]])
        self.assertIn(other, self.home.read_json("risks.json")["dropped"])
        # close: not in the next report
        self.cli("risks", "--as-of", AS_OF, "--close", cid)
        entry = [r for r in self.home.read_json("risks.json")["risks"]
                 if r["id"] == cid][0]
        self.assertEqual(entry["status"], "closed")
        self.assertIsNotNone(entry["closed"])
        rep = build_report(self.home, self.cfg, self.roster, self.as_of)
        self.assertNotIn(cid, [x["id"] for x in rep["risks"]])
        listed = self.cli("risks", "--list")
        self.assertIn(cid, [r["id"] for r in listed["risks"]])

    def test_register_unchanged_and_lean(self):
        for c in self.detect()[:3]:
            self.cli("risks", "--as-of", AS_OF, "--accept", c["id"])
        build_report(self.home, self.cfg, self.roster, self.as_of)
        before = self.home.path("risks.json").read_bytes()
        build_report(self.home, self.cfg, self.roster, self.as_of)
        self.assertEqual(self.home.path("risks.json").read_bytes(), before)
        for r in self.home.read_json("risks.json")["risks"]:
            self.assertNotIn("figures", r)
            self.assertNotIn("people", r)

    def test_unknown_id_is_refused(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--home", str(self.root), "risks", "--as-of", AS_OF,
                         "--accept", "000000000000"])
        self.assertEqual(code, 2)

    def test_open_risk_persists_and_flags_stale_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            home = Home(Path(d) / "copy")
            shutil.copytree(str(self.root), str(home.root))
            cid = self.find(self.detect(home), "chronic_carryover",
                            PRICING)[0]["id"]
            self.cli("risks", "--as-of", AS_OF, "--accept", cid, home=home)
            # the ticket is delivered in the data the next report reads
            for f in home.path("teams").glob("*/sprints/*/metrics.json"):
                m = json.loads(f.read_text(encoding="utf-8"))
                t = m["tickets"].get("40655")
                if m["team"] == PRICING and t:
                    t["outcome"] = "delivered"
                    f.write_text(json.dumps(m), encoding="utf-8")
            res = self.cli("report", "--as-of", AS_OF, home=home)
            self.assertEqual(res["risks_stale"], [cid])
            self.assertIn(cid, res["summary"])
            rep = build_report(home, self.cfg, self.roster, self.as_of)
            r = [x for x in rep["risks"] if x["id"] == cid][0]
            self.assertFalse(r["evidence_ok"])
            entry = [x for x in home.read_json("risks.json")["risks"]
                     if x["id"] == cid][0]
            self.assertEqual((entry["status"], entry["evidence_ok"]),
                             ("open", False))
            dm = Path(res["files"]["delivery_manager_html"]).read_text(
                encoding="utf-8")
            self.assertIn("The board no longer shows the evidence", dm)
            ex = Path(res["files"]["executive_html"]).read_text(
                encoding="utf-8")
            self.assertNotIn("The board no longer shows the evidence", ex)

    def test_accepted_risks_appear_in_report(self):
        cands = self.detect()
        chosen = [self.find(cands, "chronic_carryover", PRICING)[0],
                  self.find(cands, "long_wait", PRICING)[0],
                  self.find(cands, "nothing_in_progress", PRICING)[0]]
        for c in chosen:
            self.cli("risks", "--as-of", AS_OF, "--accept", c["id"])
        rep = build_report(self.home, self.cfg, self.roster, self.as_of)
        self.assertEqual({r["id"] for r in rep["risks"]},
                         {c["id"] for c in chosen})
        self.assertEqual(rep["risks"][0]["kind"], "Issue")
        self.assertTrue(all("people" in r for r in rep["risks"]))
        ex = exec_projection(rep)
        self.assertTrue(ex["risks"])
        self.assertFalse(any("people" in r for r in ex["risks"]))
        res = self.cli("report", "--as-of", AS_OF)
        self.assertEqual(res["risks_stale"], [])
        md = Path(res["files"]["executive_md"]).read_text(encoding="utf-8")
        dm = Path(res["files"]["delivery_manager_html"]).read_text(
            encoding="utf-8")
        for c in chosen:
            self.assertIn("Risks and asks", md)
            self.assertIn(c["ask"].split("#")[0][:20], md)
        self.assertIn("Risks and asks", dm)
        self.assertNotIn("The board no longer shows the evidence", dm)
        self.assertEqual(names_check(draft_texts(self.home, self.as_of),
                                     self.roster, []), [])


if __name__ == "__main__":
    unittest.main()
