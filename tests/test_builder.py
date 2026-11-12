import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ledger.config import load_config, load_roster, validate
from ledger.home import Home
from ledger.model import Snapshot
from ledger.testing.builder import PortfolioBuilder

SPRINTS = [("S36", "2026-10-12", "2026-10-23"),
           ("S37", "2026-10-26", "2026-11-06")]


def make():
    b = PortfolioBuilder(tz="Europe/Zurich")
    b.team("Claims", area="Demo\\Claims", sprints=SPRINTS)
    return b


class BuilderTests(unittest.TestCase):
    def test_revisions_carry_forward(self):
        b = make()
        anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        t = b.ticket(1, team="Claims", title="T")
        t.on("2026-10-26 09:00", state="New", iteration="S37",
             assignee=anna, by=b.other("qa"))
        t.on("2026-10-27 10:00", state="Active")
        snap = b.snapshot(cutoff="2026-11-07 08:00")
        r1, r2 = snap.tickets[0].revisions
        self.assertEqual((r1.rev, r2.rev), (1, 2))
        self.assertEqual(r2.iteration, "Demo\\Claims\\S37")
        self.assertEqual(r2.assignee, "p:anna")
        self.assertEqual(r2.by, "p:anna")
        self.assertEqual(r1.by, b.other("qa"))
        self.assertEqual(r1.changed,
                         datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc))
        self.assertEqual(r1.changed.utcoffset().total_seconds(), 0)
        self.assertEqual(r1.area, "Demo\\Claims")

    def test_sprint_bounds_and_period_start(self):
        s36, s37 = make().snapshot(cutoff="2026-11-07 08:00").sprints["Claims"]
        self.assertEqual(s37.start,
                         datetime(2026, 10, 25, 23, 0, tzinfo=timezone.utc))
        self.assertEqual(s37.period_start, s36.end)
        self.assertEqual(s36.period_start, s36.start)
        self.assertEqual(s37.id, "demo-claims-s37")

    def test_edit_changes_hash_and_length(self):
        b = make()
        t = b.ticket(1, team="Claims", title="T")
        t.on("2026-10-26 09:00", state="New")
        t.edit("2026-10-28 10:00", description="longer text",
               acceptance="Given x")
        tk = b.snapshot(cutoff="2026-11-07 08:00").tickets[0]
        r1, r2 = tk.revisions
        self.assertEqual((r1.desc_len, r1.ac_len), (0, 0))
        self.assertEqual((r2.desc_len, r2.ac_len), (11, 7))
        self.assertNotEqual(r1.desc_hash, r2.desc_hash)
        self.assertEqual(tk.description, "longer text")
        self.assertEqual(len(r1.desc_hash), 64)

    def test_vendor_ticket_appears_in_squad_without_identities(self):
        b = make()
        anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        b.ticket(1, team="Claims", title="Secret", description="d").on(
            "2026-10-26 09:00", state="Active", assignee=anna)
        b.squad_ticket(50001, team="Claims").on("2026-10-27 09:00",
                                                state="Active")
        snap = b.snapshot(cutoff="2026-11-07 08:00")
        by_id = {t.id: t for t in snap.squad}
        self.assertEqual(set(by_id), {1, 50001})
        r = by_id[1].revisions[0]
        self.assertIsNone(r.by)
        self.assertIsNone(r.assignee)
        self.assertEqual((r.desc_hash, r.desc_len), ("", 0))
        self.assertEqual(by_id[1].title, "")
        self.assertEqual(by_id[1].description, "")
        self.assertEqual(r.state, "Active")

    def test_all_fields_carry_forward(self):
        b = make()
        anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        t = b.ticket(1, team="Claims", description="d", acceptance="a")
        t.on("2026-10-26 09:00", state="Active", iteration="S37",
             assignee=anna, by=b.other("qa"), reason="Because",
             column="Doing", column_done=True, type="Bug", project="Other",
             parent=7, tags=["x", "y"], area="Demo\\Claims\\Sub")
        t.on("2026-10-27 09:00", state="In QA")
        r1, r2 = b.snapshot(cutoff="2026-11-07 08:00").tickets[0].revisions
        self.assertEqual(r2.state, "In QA")
        for f in ("reason", "column", "column_done", "iteration", "area",
                  "assignee", "type", "project", "parent", "tags",
                  "desc_hash", "desc_len", "ac_hash", "ac_len"):
            self.assertEqual(getattr(r2, f), getattr(r1, f), f)
        self.assertEqual((r2.reason, r2.column, r2.column_done, r2.type,
                          r2.project, r2.parent, r2.tags, r2.area),
                         ("Because", "Doing", True, "Bug", "Other", 7,
                          ("x", "y"), "Demo\\Claims\\Sub"))

    def test_unassign_defaults_and_tags(self):
        b = make()
        anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        t = b.ticket(1, team="Claims")
        t.on("2026-10-26 09:00", state="New")
        t.on("2026-10-26 10:00", iteration="S37", assignee=anna)
        t.on("2026-10-26 11:00", assignee=None)
        t.on("2026-10-26 12:00", iteration=None, tags=["a", "b"])
        r1, r2, r3, r4 = b.snapshot(
            cutoff="2026-11-07 08:00").tickets[0].revisions
        self.assertEqual(r1.iteration, "Demo\\Claims")
        self.assertIsNone(r1.assignee)
        self.assertEqual(r1.by, b.other("sm"))
        self.assertEqual(r2.by, "p:anna")
        self.assertIsNone(r3.assignee)
        self.assertEqual(r3.by, b.other("sm"))
        self.assertEqual(r4.iteration, "Demo\\Claims")
        self.assertEqual(r4.tags, ("a", "b"))
        self.assertIsInstance(r4.tags, tuple)

    def test_dst_conversions(self):
        b = make()
        s36, s37 = b.snapshot(cutoff="2026-11-07 08:00").sprints["Claims"]
        utc = timezone.utc
        self.assertEqual(s36.start, datetime(2026, 10, 11, 22, 0, tzinfo=utc))
        self.assertEqual(s36.end, datetime(2026, 10, 23, 22, 0, tzinfo=utc))
        self.assertEqual(s37.end, datetime(2026, 11, 6, 23, 0, tzinfo=utc))
        t = b.ticket(1, team="Claims").on("2026-10-12 09:00", state="New")
        self.assertEqual(t.revisions[0].changed,
                         datetime(2026, 10, 12, 7, 0, tzinfo=utc))

    def test_squad_copy_keeps_revisions(self):
        b = make()
        anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        b.ticket(1, team="Claims", description="d", acceptance="ac").on(
            "2026-10-26 09:00", state="New", assignee=anna).on(
            "2026-10-27 09:00", state="Active")
        snap = b.snapshot(cutoff="2026-11-07 08:00")
        mine, copy = snap.tickets[0].revisions, snap.squad[0].revisions
        self.assertEqual([r.rev for r in copy], [1, 2])
        self.assertEqual([r.changed for r in copy],
                         [r.changed for r in mine])
        for r in copy:
            self.assertEqual((r.ac_hash, r.ac_len), ("", 0))
            self.assertEqual((r.desc_hash, r.desc_len), ("", 0))
        self.assertEqual(mine[0].ac_len, 2)

    def test_time_order_enforced(self):
        t = make().ticket(1, team="Claims")
        t.on("2026-10-27 09:00", state="New")
        with self.assertRaises(ValueError):
            t.on("2026-10-26 09:00", state="Active")

    def test_identities(self):
        b = make()
        self.assertTrue(b.other("qa").startswith("o:"))
        self.assertEqual(len(b.other("qa")), 34)
        self.assertEqual(b.other("qa"), b.other("qa"))
        self.assertTrue(b.service("bot").startswith("s:"))

    def test_write_then_load(self):
        b = make()
        b.team("Pricing", area="Demo\\Pricing",
               sprints=[("P1", "2026-10-26", "2026-11-06")])
        b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        b.absence("anna", "2026-11-02", "2026-11-03")
        b.ticket(1, team="Claims").on("2026-10-26 09:00", state="New",
                                      iteration="S37")
        b.ticket(2, team="Pricing").on("2026-10-26 09:00", state="New")
        with tempfile.TemporaryDirectory() as d:
            home = Home(Path(d))
            b.write(home)
            cfg, roster = load_config(home), load_roster(home)
            validate(cfg, roster)
            self.assertEqual(len(cfg.teams), 2)
            self.assertEqual(roster.person("anna").absences[0].start.day, 2)
            snap = Snapshot.load(home.team_sprint_dir("Claims",
                                                      "demo-claims-s37")
                                 / "snapshot")
            self.assertEqual([t.id for t in snap.tickets], [1])
            self.assertEqual(list(snap.sprints), ["Claims"])
            self.assertEqual(snap.meta["account"], "builder")
            self.assertTrue(home.team_sprint_dir(
                "Pricing", "demo-pricing-p1").joinpath("snapshot").is_dir())

    def test_builder_options(self):
        b = PortfolioBuilder(tz="Europe/Zurich",
                             not_delivered_reasons=["Duplicate"],
                             unit_level="tasks")
        b.team("Claims", area="Demo\\Claims", sprints=SPRINTS)
        cfg = b.config()
        self.assertEqual(cfg.not_delivered_reasons, frozenset(["Duplicate"]))
        self.assertEqual(cfg.unit_level, "tasks")
        self.assertEqual(cfg.team("Claims").phases[2].wait_label, "QA")


if __name__ == "__main__":
    unittest.main()
