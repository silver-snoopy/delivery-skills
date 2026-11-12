import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ledger.model import Phase, Revision, Snapshot, Sprint, Ticket


def utc(y, m, d, h=0):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


def rev(n, **kw):
    base = dict(rev=n, changed=utc(2026, 1, n), by="Ann", state="Active",
                reason="", column=None, column_done=None, iteration="S1",
                area="P\\Claims", assignee=None, type="User Story",
                project="P", parent=None, tags=(), desc_hash="h",
                desc_len=3, ac_hash="a", ac_len=2)
    base.update(kw)
    return Revision(**base)


class ModelTests(unittest.TestCase):
    def test_phase_values(self):
        self.assertEqual([p.value for p in (Phase.REMOVED, Phase.TODO,
                         Phase.DOING, Phase.HANDOFF, Phase.DONE)],
                         [-1, 0, 1, 2, 3])

    def test_revision_json_round_trip(self):
        r = rev(1, tags=("a", "b"), column="QA", column_done=True, parent=7)
        d = r.to_json()
        self.assertIsInstance(d["tags"], list)
        self.assertIsInstance(d["changed"], str)
        json.dumps(d)
        back = Revision.from_json(d)
        self.assertEqual(back, r)
        self.assertEqual(back.tags, ("a", "b"))
        self.assertEqual(back.changed.utcoffset().total_seconds(), 0)

    def snapshot(self):
        t1 = Ticket(1, [rev(1), rev(2, by=None, assignee="Bo")],
                    title="T", description="D", acceptance="A",
                    parent_title="P")
        t2 = Ticket(2, [rev(1)])
        sp = Sprint("Claims", "s1", "Sprint 1", "P\\S1", utc(2026, 1, 1),
                    utc(2026, 1, 15), utc(2026, 1, 1))
        return Snapshot([t1], [t2], {"Claims": [sp]},
                        {"cutoff": "2026-01-20T00:00:00+00:00",
                         "account": "x", "auth_mode": "az",
                         "coverage": {"missing": [3], "unreadable": []},
                         "tool_version": "1"})

    def test_snapshot_round_trip(self):
        s = self.snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "snap"
            s.save(d)
            self.assertEqual(Snapshot.load(d), s)
            self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)
            for n in ("tickets.jsonl", "squad.jsonl", "sprints.json",
                      "meta.json"):
                self.assertEqual(stat.S_IMODE(os.stat(d / n).st_mode), 0o600)
            self.assertEqual(
                len((d / "tickets.jsonl").read_text().splitlines()), 1)
            self.assertEqual(
                [f for f in os.listdir(d) if f.endswith(".tmp")], [])

    def test_save_overwrites_existing(self):
        s = self.snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            s.save(Path(tmp))
            s.tickets = []
            s.save(Path(tmp))
            self.assertEqual(Snapshot.load(Path(tmp)).tickets, [])


if __name__ == "__main__":
    unittest.main()
