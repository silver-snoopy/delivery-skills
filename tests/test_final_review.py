"""Final-review regressions use invented histories and real offline CLI reports."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ledger import cli, render
from ledger.ado import CACHE_FILE
from ledger.config import Config, Person, parse_notes, save_config, write_team
from tests.fixture import Board
from tests.test_xlsx import build_workbook, calendar_rows

ROOT = Path(__file__).resolve().parent.parent


class FinalReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        vacation = self.root / "leave.xlsx"
        calendar = calendar_rows()
        calendar[4] = {4: "Nia Birch", 3: "Alfa"}
        del calendar[5]
        build_workbook(str(vacation), calendar, ["G2:AJ2", "AK2:BO2"])
        save_config(
            str(self.root),
            Config(vacations=str(vacation), hold_states=["On Hold"], teams={"ALFA": {"count": "stories"}}),
        )
        write_team(
            str(self.root / "team.csv"), [Person("Nia Birch", ["nia@vendor.example"], "ALFA", None, None)]
        )
        self.board = (
            Board().sprint("Maple", "2026-10-26", "2026-11-06").sprint("Cedar", "2026-11-09", "2026-11-20")
        )
        self.board.item(101, title="Validate manifest").at(
            "2026-10-01 09:00", state="Active", iteration="Proj\\Maple", assigned="nia@vendor.example"
        ).at("2026-10-30 15:00", state="Closed")
        (self.root / "cache").mkdir()
        self.pages = {}
        self.folder = "maple"

    def item(self, item=202, carry=False, active=False, title="Retry manifest"):
        return self.board.item(item, title=title).at(
            "2026-10-26 09:00" if carry else "2026-11-09 09:00",
            state="Active" if active else "New",
            iteration="Proj\\Maple" if carry else "Proj\\Cedar",
            assigned="nia@vendor.example",
        )

    def report(self, today="2026-11-13"):
        (self.root / CACHE_FILE).write_text(json.dumps(self.board.cache()), encoding="utf-8")
        manager, dm = render.render_manager, render.render_dm

        def capture_manager(*args):
            self.team = args[1][0]
            self.pages["manager"] = manager(*args)
            return self.pages["manager"]

        def capture_dm(*args):
            self.pages["dm"] = dm(*args)
            return self.pages["dm"]

        out = io.StringIO()
        with (
            patch.object(render, "render_manager", side_effect=capture_manager),
            patch.object(render, "render_dm", side_effect=capture_dm),
        ):
            code = cli.main(["--dir", str(self.root), "report", "--offline", "--today", today], out=out)
        self.assertEqual(code, 0, out.getvalue())
        return out.getvalue()

    @property
    def notes_path(self):
        return self.root / "reports" / self.folder / "notes.md"

    def saved(self, item=202):
        sec = parse_notes(self.notes_path.read_text(encoding="utf-8")).teams["ALFA"]
        return next(
            (heading, row) for heading in ("Actions", "Resolved") for row in sec[heading] if row["id"] == item
        )

    def answer(self, text, item=202):
        # Change only the invented DM-owned cell; parser/writer under test stays real.
        lines = self.notes_path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.startswith(f"| Nia Birch | #{item} "):
                cells = line.split(" | ")
                cells[3] = text
                lines[i] = " | ".join(cells)
                break
        else:
            self.fail("Missing action to answer")
        self.notes_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def next_sprint(self, *items):
        self.board.sprint("Willow", "2026-11-23", "2026-12-04")
        for item in items:
            item.at("2026-11-20 18:00", iteration="Proj\\Willow")
        self.folder = "cedar"

    def action(self, item=202):
        return next(row for row in self.team["actions"]["rows"] if row["id"] == item)

    def test_c1_copied_now_answer_never_reaches_manager_reason(self):
        current = self.item()
        self.report()
        self.answer("Nia says the supplier is blocked")
        self.report()
        self.next_sprint(current)
        self.report("2026-11-24")
        self.assertNotIn("Nia says", self.pages["manager"])
        self.assertEqual(self.team["carry"][0]["reason"], "")
        self.assertIn("Nia says", self.saved()[1]["answer"])

    def test_c1_now_answer_cannot_become_reason_in_reused_folder(self):
        self.item()
        self.report()
        self.answer("agreed: private running context")
        self.report()
        self.board.sprints[1]["end"] = "2026-11-12"
        # Reuse the current notes file in place, as the marker-mismatch path allows.
        (self.root / "reports" / "maple").rename(self.root / "reports" / "cedar")
        self.folder = "cedar"
        self.report()
        self.assertNotIn("private running context", self.pages["manager"])

    def test_c1_carry_reason_uses_existing_name_warning(self):
        self.item(carry=True)
        self.report()
        self.answer("Nia Birch will confirm")
        out = self.report()
        self.assertEqual(self.team["carry"][0]["reason"], "Nia Birch will confirm")
        self.assertIn("Nia Birch will confirm", self.pages["manager"])
        self.assertIn("warning: manager file mentions Nia Birch", out)

    def test_i1_resolved_open_answer_survives_new_folder(self):
        carried = self.item(carry=True, active=True)
        carried.at("2026-11-07 09:00", iteration="Proj\\Cedar").at("2026-11-12 09:00")
        self.report()
        self.answer("split after planning")
        self.report()
        self.assertEqual(self.saved()[0], "Actions")
        self.next_sprint(carried)
        self.report("2026-11-24")
        self.assertEqual(self.saved()[1]["answer"], "split after planning")
        self.assertTrue(self.action()["worse"])
        self.assertEqual(self.team["carry"][0]["reason"], "")

    def test_i1_actions_open_carry_answer_survives_new_folder(self):
        carried = self.item(carry=True, active=True).at("2026-11-07 09:00", iteration="Proj\\Cedar")
        self.report()
        self.answer("awaiting product decision")
        self.report()
        self.assertEqual(self.saved()[0], "Actions")
        self.next_sprint(carried)
        self.report("2026-11-24")
        self.assertEqual(self.saved()[1]["answer"], "awaiting product decision")
        self.assertTrue(self.action()["worse"])

    def test_minor_answered_carry_preserves_problem(self):
        self.item(carry=True, active=True).at("2026-11-07 09:00", iteration="Proj\\Cedar").at(
            "2026-11-12 09:00"
        )
        self.report()
        problem = self.saved()[1]["problem"]
        self.answer("split after planning")
        self.report()
        self.assertEqual(self.saved()[0], "Actions")
        self.assertEqual(self.saved()[1]["problem"], problem)

    def test_i2_copied_carry_prints_action_and_carry_question(self):
        carried = self.item(carry=True, active=True).at("2026-11-07 09:00", iteration="Proj\\Cedar")
        self.report()
        self.answer("continue after planning")
        self.report()
        self.next_sprint(carried)
        out = self.report("2026-11-24")
        self.assertIn("ALFA carry-over without note: #202 Retry manifest", out)
        self.assertIn("ALFA action: Nia Birch #202 Carry-over ↑ -", out)

    def test_i2_answered_new_signal_prints_up_arrow_action(self):
        current = self.item()
        self.report()
        self.answer("supplier review booked")
        self.report()
        current.at("2026-11-13 10:00", state="On Hold", category="InProgress")
        out = self.report("2026-11-16")
        self.assertTrue(self.action()["worse"])
        self.assertIn("ALFA action: Nia Birch #202 On Hold ↑ -", out)
        self.assertEqual(self.saved()[1]["answer"], "supplier review booked")

    def test_i3_padding_copied_answer_never_renews_reason(self):
        carried = self.item(carry=True, active=True).at("2026-11-07 09:00", iteration="Proj\\Cedar")
        self.report()
        self.answer("continue after planning")
        self.report()
        self.next_sprint(carried)
        self.report("2026-11-24")
        baseline = self.saved()[1]["answered"]
        for padded in ("continue after planning    ", "  continue\t after   planning    "):
            with self.subTest(padded=padded):
                self.answer(padded)
                self.report("2026-11-24")
                self.assertEqual(self.team["carry"][0]["reason"], "")
                self.assertTrue(self.action()["worse"])
                self.assertEqual(self.saved()[1]["answered"], baseline)
                self.assertEqual(self.saved()[1]["answer"], padded)
                state = json.loads((self.notes_path.parent / ".notes-state.json").read_text())
                self.assertEqual(state["teams"]["ALFA"]["i:202"]["answer"], "continue after planning")

    def test_i3_whitespace_collapses_for_reason_without_changing_dm_text(self):
        self.item(carry=True)
        self.report()
        answer = "  dependency\t review   pending   "
        self.answer(answer)
        self.report()
        self.assertEqual(self.team["carry"][0]["reason"], "dependency review pending")
        self.assertEqual(self.saved()[1]["answer"], answer)

    def test_m1_held_item_does_not_worsen_for_no_movement(self):
        self.item(active=True).at("2026-11-10 09:00", state="On Hold", category="InProgress")
        self.report("2026-11-12")
        self.answer("supplier fix scheduled")
        self.report("2026-11-12")
        baseline = self.saved()[1]["answered"]
        out = self.report("2026-11-18")
        self.assertNotIn("stale", self.action()["kinds"])
        self.assertTrue(self.action()["handled"])
        self.assertNotIn("ALFA action: Nia Birch #202", out)
        self.assertEqual(self.saved()[1]["answered"], baseline)

    def test_m1_closed_hold_also_has_no_movement_signal(self):
        self.item(active=True).at("2026-11-10 09:00", state="On Hold", category="InProgress")
        self.folder = "cedar"
        self.report("2026-11-21")
        self.assertNotIn("stale", self.action()["kinds"])

    def test_e2e5_missing_state_import_keeps_text_and_requests_review(self):
        self.item(carry=True, active=True).at("2026-11-07 09:00", iteration="Proj\\Cedar")
        self.report()
        self.answer("agreed: supplier checkpoint booked")
        self.report()
        baseline = self.saved()[1]["answered"]
        (self.notes_path.parent / ".notes-state.json").unlink()
        self.report()
        self.assertTrue(self.action()["worse"])
        self.assertEqual(self.saved()[1]["answer"], "agreed: supplier checkpoint booked")
        self.assertEqual(self.saved()[1]["answered"], baseline)
        self.assertNotIn("supplier checkpoint", self.pages["manager"])

    def test_e2e7_answer_observed_after_activation_survives_return(self):
        self.check_answer_after_problem_disappears("Active")

    def test_e2e8_answer_observed_after_closure_survives_reopening(self):
        self.check_answer_after_problem_disappears("Closed")

    def check_answer_after_problem_disappears(self, state):
        current = self.item()
        self.report()
        self.answer("supplier checkpoint booked")
        current.at("2026-11-12 10:00", state=state)
        self.report()
        self.assertEqual(self.saved()[0], "Resolved")
        baseline = self.saved()[1]["answered"]
        self.assertTrue(baseline.startswith("2026-11-13 "))
        current.at("2026-11-13 10:00", state="New", reason="Reopened")
        self.report("2026-11-18")
        self.assertEqual(self.saved()[0], "Actions")
        self.assertEqual(self.saved()[1]["answer"], "supplier checkpoint booked")
        self.assertEqual(self.saved()[1]["answered"], baseline)
        self.assertTrue(self.action()["worse"])

    def test_minor_missing_title_is_empty_in_action_and_notes(self):
        current = self.item()
        for row in current.rows:
            row["title"] = None
        self.report()
        self.assertEqual(self.action()["title"], "")
        self.assertNotIn("#202 None", self.pages["dm"])
        self.assertNotIn("#202 None", self.notes_path.read_text(encoding="utf-8"))

    def test_c1_same_closed_sprint_copy_supplies_no_manager_reason(self):
        self.item(carry=True)
        self.report()
        self.answer("defer until supplier delivery")
        self.report()
        (self.root / "reports" / "maple").rename(self.root / "reports" / "archive")
        self.report()
        self.assertEqual(self.team["carry"][0]["reason"], "")
        self.assertNotIn("defer until supplier delivery", self.pages["manager"])
        self.assertEqual(self.saved()[1]["answer"], "defer until supplier delivery")

    def test_c1_unchanged_now_answer_cannot_gain_carry_provenance(self):
        current = self.item()
        self.report()
        self.answer("private checkpoint")
        self.report()
        # Corrected board history makes the existing item a closed-sprint carry.
        # The DM has not changed the answer in response to that carry-over.
        current.rows[0]["changed"] = "2026-10-26T08:00:00Z"
        current.rows[0]["iteration"] = "Proj\\Maple"
        current.at("2026-11-07 10:00", iteration="Proj\\Cedar")
        self.report()
        self.assertEqual(self.team["carry"][0]["reason"], "")
        self.assertNotIn("private checkpoint", self.pages["manager"])
        self.answer("checkpoint renewed for carry-over")
        self.report()
        self.assertEqual(self.team["carry"][0]["reason"], "checkpoint renewed for carry-over")

    def test_c1_missing_state_does_not_infer_local_reason_from_visible_baseline(self):
        self.item(carry=True)
        self.report()
        self.answer("private provenance uncertain")
        self.report()
        (self.notes_path.parent / ".notes-state.json").unlink()
        self.report()
        self.assertEqual(self.team["carry"][0]["reason"], "")
        self.assertNotIn("provenance uncertain", self.pages["manager"])
        self.assertEqual(self.saved()[1]["answer"], "private provenance uncertain")

    def test_minor_predecessor_warning_identifies_its_source_file(self):
        self.item(carry=True)
        self.report()
        archive = self.root / "Archive"
        (self.root / "reports" / "maple").rename(archive)
        path = archive / "notes.md"
        path.write_text(
            path.read_text(encoding="utf-8") + "## Unrecognised\nkeep this line\n", encoding="utf-8"
        )
        out = self.report()
        self.assertIn(f"warning: {path}: notes.md line", out)
        self.assertIn("keep this line", self.notes_path.read_text(encoding="utf-8"))

    def test_e2e1_old_copied_carry_prints_stuck_arrow(self):
        # A short completed item makes the carried item older than the squad P85.
        self.board.items[0].rows[0]["changed"] = "2026-10-26T08:00:00Z"
        carried = self.item(carry=True, active=True).at("2026-11-07 09:00", iteration="Proj\\Cedar")
        self.report()
        self.answer("split at supplier boundary")
        self.report()
        self.next_sprint(carried)
        out = self.report("2026-11-24")
        self.assertEqual(self.saved()[1]["answer"], "split at supplier boundary")
        self.assertEqual(self.team["carry"][0]["reason"], "")
        self.assertIn("ALFA action: Nia Birch #202 Stuck ↑ -", out)
        self.assertIn("ALFA carry-over without note: #202 Retry manifest", out)
