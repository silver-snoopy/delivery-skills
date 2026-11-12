import unittest
from datetime import timedelta

from ledger.model import Phase
from ledger.phases import PhaseMapper
from ledger.testing.builder import DEFAULT_PHASES, PortfolioBuilder
from ledger.timeline import build_timeline, find_bulk_edits

SPRINTS = [("S37", "2026-10-26", "2026-11-06")]
CUTOFF = "2026-11-07 08:00"


def make(phases=None, **kw):
    b = PortfolioBuilder(tz="Europe/Zurich", **kw)
    b.team("Claims", area="Demo\\Claims", sprints=SPRINTS, phases=phases)
    anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
    return b, anna


def timeline(b, ticket_id=1, bulk=frozenset()):
    cfg = b.config()
    mapper = PhaseMapper(cfg.team("Claims"), cfg)
    snap = b.snapshot(cutoff=CUTOFF)
    t = {x.id: x for x in snap.tickets}[ticket_id]
    return build_timeline(t, mapper, b.parse_time(CUTOFF), set(bulk))


class IntervalTests(unittest.TestCase):
    def test_intervals_split_on_holder_and_phase(self):
        b, anna = make()
        qa = b.other("qa")
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="New", assignee=anna).on(
            "2026-10-27 09:00", state="Active").on(
            "2026-10-28 09:00", state="In QA", assignee=qa).on(
            "2026-10-29 09:00", state="Closed")
        tl = timeline(b)
        self.assertEqual([i.phase for i in tl.intervals],
                         [Phase.TODO, Phase.DOING, Phase.HANDOFF, Phase.DONE])
        self.assertEqual([i.holder for i in tl.intervals],
                         [anna, anna, qa, qa])
        self.assertEqual(tl.intervals[2].wait_label, "QA")
        self.assertEqual(tl.intervals[-1].end, b.parse_time(CUTOFF))
        for a, c in zip(tl.intervals, tl.intervals[1:]):
            self.assertEqual(a.end, c.start)
        self.assertEqual(len(tl.events), 4)
        self.assertIsNone(tl.events[0].from_phase)
        self.assertIsNone(tl.events[0].holder_before)

    def test_holder_change_alone_splits_without_event(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Active", assignee=anna).on(
            "2026-10-27 09:00", assignee=None).on(
            "2026-10-28 09:00", iteration="S37")
        tl = timeline(b)
        self.assertEqual([i.holder for i in tl.intervals], [anna, None, None])
        self.assertEqual(tl.intervals[2].iteration, "Demo\\Claims\\S37")
        self.assertEqual(len(tl.events), 1)

    def test_unchanged_fields_do_not_split(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Active", assignee=anna).on(
            "2026-10-27 09:00", tags=["x"])
        self.assertEqual(len(timeline(b).intervals), 1)

    def test_reason_change_in_done_splits_on_not_delivered(self):
        b, anna = make(not_delivered_reasons=["Cut"])
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Closed", reason="Done",
            assignee=anna).on(
            "2026-10-27 09:00", reason="Cut").on(
            "2026-10-28 09:00", reason="Also cut but not configured")
        tl = timeline(b)
        self.assertEqual([i.not_delivered for i in tl.intervals],
                         [False, True, False])
        self.assertEqual(len(tl.events), 1)

    def test_same_timestamp_drops_zero_length_but_keeps_event(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="New", assignee=anna).on(
            "2026-10-27 09:00", state="Active").on(
            "2026-10-27 09:00", state="In QA")
        tl = timeline(b)
        self.assertEqual([i.phase for i in tl.intervals],
                         [Phase.TODO, Phase.HANDOFF])
        self.assertEqual([(e.from_phase, e.to_phase) for e in tl.events],
                         [(None, Phase.TODO), (Phase.TODO, Phase.DOING),
                          (Phase.DOING, Phase.HANDOFF)])

    def test_no_revisions(self):
        b, _ = make()
        b.ticket(1, team="Claims")
        tl = timeline(b)
        self.assertEqual((tl.intervals, tl.events), ([], []))

    def test_at_phase_at_clip_first(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="New", assignee=anna).on(
            "2026-10-27 09:00", state="Active").on(
            "2026-10-28 09:00", state="Closed")
        tl = timeline(b)
        t = b.parse_time
        self.assertIsNone(tl.phase_at(t("2026-10-26 08:59")))
        self.assertEqual(tl.phase_at(t("2026-10-26 09:00")), Phase.TODO)
        self.assertEqual(tl.phase_at(t("2026-10-27 09:00")), Phase.DOING)
        self.assertIs(tl.at(t("2026-10-27 12:00")), tl.intervals[1])
        self.assertEqual(tl.phase_at(t("2026-11-07 07:59")), Phase.DONE)
        self.assertIsNone(tl.phase_at(t(CUTOFF)))
        clipped = tl.clip(t("2026-10-26 12:00"), t("2026-10-27 12:00"))
        self.assertEqual([(c.start, c.end) for c in clipped],
                         [(t("2026-10-26 12:00"), t("2026-10-27 09:00")),
                          (t("2026-10-27 09:00"), t("2026-10-27 12:00"))])
        self.assertEqual(tl.intervals[0].start, t("2026-10-26 09:00"))
        self.assertEqual(tl.clip(t("2026-10-27 09:00"),
                                 t("2026-10-27 09:00")), [])
        self.assertIs(tl.first(lambda i: i.phase >= Phase.DOING),
                      tl.intervals[1])
        self.assertIsNone(tl.first(lambda i: i.phase == Phase.REMOVED))


class EventTests(unittest.TestCase):
    def test_column_only_change_is_a_phase_change(self):
        phases = [{"state": "Active", "column": "Dev", "phase": "doing"},
                  {"state": "Active", "column": "QA", "phase": "handoff",
                   "wait_label": "QA"}] + DEFAULT_PHASES
        b, anna = make(phases=phases)
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Active", column="Dev",
            assignee=anna).on(
            "2026-10-27 09:00", column="QA")
        tl = timeline(b)
        e = tl.events[-1]
        self.assertEqual((e.from_phase, e.to_phase),
                         (Phase.DOING, Phase.HANDOFF))
        self.assertTrue(e.to_handoff)
        self.assertEqual(tl.intervals[-1].state, "Active")
        self.assertEqual(tl.intervals[-1].wait_label, "QA")

    def test_return_from_qa(self):
        b, anna = make()
        qa = b.other("qa")
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Active", assignee=anna).on(
            "2026-10-27 09:00", state="In QA", assignee=qa).on(
            "2026-10-28 09:00", state="Active", assignee=anna, by=qa)
        e = timeline(b).events[-1]
        self.assertTrue(e.backward)
        self.assertTrue(e.is_return)
        self.assertFalse(e.reopen)
        self.assertFalse(e.admin)
        self.assertFalse(e.to_handoff)
        self.assertEqual(e.holder_before, qa)
        self.assertEqual(e.by, qa)
        self.assertEqual(e.rev, 3)

    def test_backward_from_doing_is_not_return(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Active", assignee=anna).on(
            "2026-10-27 09:00", state="New")
        e = timeline(b).events[-1]
        self.assertTrue(e.backward)
        self.assertFalse(e.is_return)

    def test_reopen(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Closed", assignee=anna).on(
            "2026-10-27 09:00", state="Active")
        e = timeline(b).events[-1]
        self.assertTrue(e.backward)
        self.assertTrue(e.is_return)
        self.assertTrue(e.reopen)

    def test_handoff_to_handoff_splits_without_event(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="In Review", assignee=anna).on(
            "2026-10-27 09:00", state="In QA")
        tl = timeline(b)
        self.assertEqual(len(tl.events), 1)
        self.assertTrue(tl.events[0].to_handoff)
        self.assertEqual([i.wait_label for i in tl.intervals],
                         ["review", "QA"])
        self.assertEqual(len(tl.intervals), 2)

    def test_service_account_change_is_admin_not_backward(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="In QA", assignee=anna).on(
            "2026-10-27 09:00", state="Active", by=b.service("bot"))
        e = timeline(b).events[-1]
        self.assertTrue(e.admin)
        self.assertFalse(e.backward)
        self.assertFalse(e.is_return)
        self.assertFalse(e.reopen)

    def test_type_or_project_change_is_admin(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="In QA", assignee=anna).on(
            "2026-10-27 09:00", state="Active", type="Bug")
        b.ticket(2, team="Claims").on(
            "2026-10-26 09:00", state="Closed", assignee=anna).on(
            "2026-10-27 09:00", state="Active", project="Other")
        b.ticket(3, team="Claims").on(
            "2026-10-26 09:00", state="Closed", assignee=anna).on(
            "2026-10-27 09:00", type="Bug").on(
            "2026-10-28 09:00", state="Active")
        for tid in (1, 2):
            e = timeline(b, tid).events[-1]
            self.assertTrue(e.admin, tid)
            self.assertFalse(e.backward, tid)
            self.assertFalse(e.is_return, tid)
        e = timeline(b, 3).events[-1]
        self.assertFalse(e.admin)
        self.assertTrue(e.reopen)

    def test_bulk_edit_marked_admin(self):
        for n, expect in ((10, True), (9, False)):
            b, anna = make()
            sm = b.other("sm")
            for i in range(1, n + 1):
                b.ticket(i, team="Claims").on(
                    "2026-10-26 09:00", state="Active", assignee=anna).on(
                    "2026-10-27 09:00", state="New", by=sm)
            # spread the moves 3 s apart: 10 tickets span 27 s
            snap = b.snapshot(cutoff=CUTOFF)
            tickets = snap.tickets
            for i, t in enumerate(tickets):
                r = t.revisions[1]
                r.changed = r.changed + timedelta(seconds=3 * i)
            bulk = find_bulk_edits(tickets, 10, 30)
            cfg = b.config()
            mapper = PhaseMapper(cfg.team("Claims"), cfg)
            for t in tickets:
                e = build_timeline(t, mapper, b.parse_time(CUTOFF),
                                   bulk).events[-1]
                self.assertEqual(e.admin, expect, (n, t.id))
                self.assertEqual(e.backward, not expect)

    def test_removed_is_not_backward(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Closed", assignee=anna).on(
            "2026-10-27 09:00", state="Removed").on(
            "2026-10-28 09:00", state="Active")
        removed, back = timeline(b).events[1:]
        for e in (removed, back):
            self.assertFalse(e.backward)
            self.assertFalse(e.is_return)
            self.assertFalse(e.reopen)
        self.assertEqual(removed.to_phase, Phase.REMOVED)
        self.assertEqual(back.from_phase, Phase.REMOVED)


class BulkTests(unittest.TestCase):
    def _tickets(self, offsets, by=None):
        b, anna = make()
        actor = by or b.other("sm")
        for i, _ in enumerate(offsets, start=1):
            b.ticket(i, team="Claims").on(
                "2026-10-26 09:00", state="Active", assignee=anna,
                by=actor).on("2026-10-27 09:00", state="New", by=actor)
        tickets = b.snapshot(cutoff=CUTOFF).tickets
        for t, off in zip(tickets, offsets):
            t.revisions[1].changed += timedelta(seconds=off)
        return b, tickets

    def test_window_boundary_inclusive_and_exclusive(self):
        _, ts = self._tickets([0, 10, 30])
        self.assertEqual(find_bulk_edits(ts, 3, 30),
                         {(1, 2), (2, 2), (3, 2)})
        _, ts = self._tickets([0, 10, 31])
        self.assertEqual(find_bulk_edits(ts, 3, 30), set())

    def test_first_revision_and_none_actor_ignored(self):
        # all rev-1 edits share a timestamp and actor but are never bulk
        _, ts = self._tickets([0, 3600, 7200])
        self.assertEqual(find_bulk_edits(ts, 3, 30), set())
        _, ts = self._tickets([0, 1, 2])
        for t in ts:
            t.revisions[1].by = None
        self.assertEqual(find_bulk_edits(ts, 3, 30), set())

    def test_distinct_tickets_counted(self):
        b, anna = make()
        sm = b.other("sm")
        t = b.ticket(1, team="Claims").on("2026-10-26 09:00", by=sm)
        for _ in range(5):
            t.on("2026-10-27 09:00", state="Active", by=sm)
        ts = b.snapshot(cutoff=CUTOFF).tickets
        self.assertEqual(find_bulk_edits(ts, 2, 30), set())


if __name__ == "__main__":
    unittest.main()
