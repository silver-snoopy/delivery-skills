import io
import json
import os
import re
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from ledger import cli, render
from ledger.ado import AdoError, CACHE_FILE
from ledger.config import Config, Person, parse_notes, save_config, write_team
from tests.fixture import Board
from tests.test_xlsx import build_workbook, calendar_rows


class CliTest(unittest.TestCase):
    def test_cli_error_lines(self):
        out = io.StringIO()
        with mock.patch("ledger.setup.run_init", side_effect=AdoError("no token", "az login")):
            code = cli.main(["init", "--query", "q", "--staff", "s", "--vacations", "v", "--yes"], out=out)
        self.assertEqual(code, 1)
        self.assertIn("error: no token", out.getvalue())
        self.assertIn("next: az login", out.getvalue())

    def test_value_error_has_no_next_line(self):
        out = io.StringIO()
        with mock.patch("ledger.setup.run_init", side_effect=ValueError("bad")):
            code = cli.main(["--dir", "x", "init", "--query", "q", "--staff", "s", "--vacations", "v", "--yes"], out=out)
        self.assertEqual((code, out.getvalue()), (1, "error: bad\n"))

    def test_report_rejects_unended_sprint_without_creating_report_files(self):
        with tempfile.TemporaryDirectory() as directory:
            vacation = os.path.join(directory, "vacations.xlsx")
            build_workbook(vacation, calendar_rows(), ["G2:AJ2", "AK2:BO2"])
            save_config(directory, Config(vacations=vacation, teams={"ALFA": {"count": "stories"}}))
            write_team(os.path.join(directory, "team.csv"),
                       [Person("Ari Pine", ["ari@vendor.example"], "ALFA", None, None)])
            b = Board().sprint("Next", "2026-11-02", "2026-11-13")
            b.item(1).at("2026-11-02 09:00", iteration="Proj\\Next", assigned="ari@vendor.example")
            os.makedirs(os.path.join(directory, "cache"))
            with open(os.path.join(directory, CACHE_FILE), "w") as f:
                json.dump(b.cache(), f)
            out = io.StringIO()
            code = cli.main(["--dir", directory, "report", "--offline", "--today", "2026-11-06",
                             "--sprint", "Next"], out=out)
            self.assertEqual(code, 1)
            self.assertIn("error: sprint 'Next' has not ended before 2026-11-06", out.getvalue())
            self.assertNotIn("Written:", out.getvalue())
            self.assertFalse(os.path.exists(os.path.join(directory, "Next")))

    def test_offline_action_workflow_preserves_answers_and_closed_results(self):
        # Losing saved answers, hiding new signals, or leaking Now rows to manager_
        # must fail through the real CLI, cache, notes and renderer boundaries.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vacation = root / "vacations.xlsx"
            rows = calendar_rows()
            rows[4] = {4: "Ari Pine", 3: "Alfa"}
            del rows[5]
            build_workbook(str(vacation), rows, ["G2:AJ2", "AK2:BO2"])
            save_config(directory, Config(vacations=str(vacation), hold_states=["On Hold"],
                                          teams={"ALFA": {"count": "stories"}}))
            write_team(str(root / "team.csv"),
                       [Person("Ari Pine", ["ari@vendor.example"], "ALFA", None, None)])
            board = (Board().sprint("Closed", "2026-10-26", "2026-11-06")
                     .sprint("Current", "2026-11-09", "2026-11-20"))
            board.item(1, title="Upload validation").at(
                "2026-10-26 09:00", state="Active", iteration="Proj\\Closed",
                assigned="ari@vendor.example").at("2026-11-06 15:00", state="Closed")
            current = board.item(2, title="Running retry work").at(
                "2026-11-09 09:00", iteration="Proj\\Current", assigned="ari@vendor.example")
            (root / "cache").mkdir()
            notes_path = root / "reports" / "closed" / "notes.md"
            pages, results = {}, []

            def capture_manager(*args):
                pages["manager"] = render_manager(*args)
                team = args[1][0]
                results.append(tuple(team[k] for k in (
                    "planned", "delivered_planned", "added", "removed", "cycle", "waiting_share")))
                return pages["manager"]

            def capture_dm(*args):
                pages["dm"] = render_dm(*args)
                pages["item"] = next(row for row in args[1][0]["actions"]["rows"]
                                     if row["id"] == 2)
                return pages["dm"]

            render_manager, render_dm = render.render_manager, render.render_dm

            def report(today, closed="Closed"):
                (root / CACHE_FILE).write_text(json.dumps(board.cache()), encoding="utf-8")
                out = io.StringIO()
                with mock.patch.object(render, "render_manager", side_effect=capture_manager), \
                        mock.patch.object(render, "render_dm", side_effect=capture_dm):
                    code = cli.main(["--dir", directory, "report", "--offline", "--today", today],
                                    out=out)
                self.assertEqual(code, 0, out.getvalue())
                private_content = ["Ari Pine", "ari@vendor.example", "d available", "<th>Person</th>",
                                   "<th>Signal</th>", "Handled", "Private support context.",
                                   "review pending", "Next"]
                if closed == "Closed":
                    private_content += ["Running retry work", "Current", "chased:"]
                for private in private_content:
                    self.assertNotIn(private, pages["manager"])
                return out.getvalue()

            def saved_row(section):
                saved = parse_notes(notes_path.read_text(encoding="utf-8"))
                return next(row for row in saved.teams["ALFA"][section] if row["id"] == 2)

            out = report("2026-11-13")
            self.assertIn("ALFA action: Ari Pine #2 Not started - still Proposed after working day 4\n", out)
            self.assertEqual(saved_row("Actions")["problem"], "still Proposed after working day 4")
            self.assertIn("<td>still Proposed after working day 4</td>", pages["dm"])
            self.assertTrue((notes_path.parent / ".notes-state.json").is_file())
            self.assertIn("<!-- ledger notes: ALFA=Proj\\Closed -->", notes_path.read_text())
            baseline_metrics = results[-1]
            self.assertIn('1 <small>of 1</small>', pages["manager"])
            # This is an invented DM edit, never an answer on a real DM's behalf.
            answer = "chased: review pipe | and path C:\\draft"
            encoded_answer = answer.replace("\\", "\\\\").replace("|", "\\|")
            edited = notes_path.read_text(encoding="utf-8").replace(
                "## Summary\n", "## Summary\n- Delivered upload validation.\n").replace(
                "## Exec note\n", "## Exec note\nRelease ready.\n").replace(
                "## Note\n", "## Note\nPrivate support context.\n").replace(
                "## Status\n", "## Status\nAMBER - review pending\n").replace(
                " |  |  |", " | " + encoded_answer + " |  |", 1)
            notes_path.write_text(edited, encoding="utf-8")
            self.assertEqual(saved_row("Actions")["answer"], answer)
            out = report("2026-11-13")
            self.assertNotIn("ALFA action:", out)
            self.assertIn('class="chip grey">Handled</span>', pages["dm"])
            first_baseline = saved_row("Actions")["answered"]
            manager_card = re.search(r'<article.*?</article>', pages["manager"], re.S).group()

            current.at("2026-11-13 10:00", state="On Hold", category="InProgress")
            out = report("2026-11-16")
            self.assertIn("On Hold ↑", pages["dm"])
            self.assertIn("ALFA action: Ari Pine #2 On Hold ↑ -", out)
            self.assertEqual(saved_row("Actions")["answered"], first_baseline)
            self.assertEqual(saved_row("Actions")["answer"], answer)

            renewed = answer + "; checked again"
            notes_path.write_text(notes_path.read_text(encoding="utf-8").replace(
                " | " + first_baseline + " |",
                "; checked again | " + first_baseline + " |"), encoding="utf-8")
            self.assertEqual(saved_row("Actions")["answer"], renewed)
            report("2026-11-16")
            self.assertIn('class="chip grey">Handled</span>', pages["dm"])
            renewed_baseline = saved_row("Actions")["answered"]
            self.assertNotEqual(renewed_baseline, first_baseline)

            current.at("2026-11-16 10:00", state="Active")
            report("2026-11-17")
            self.assertEqual(saved_row("Resolved")["answer"], renewed)
            current.at("2026-11-17 10:00", state="On Hold", category="InProgress")
            report("2026-11-18")
            self.assertEqual(saved_row("Actions")["answer"], renewed)
            self.assertEqual(saved_row("Actions")["answered"], renewed_baseline)
            self.assertIn('class="chip grey">Handled</span>', pages["dm"])
            self.assertNotIn("On Hold ↑", pages["dm"])
            saved = parse_notes(notes_path.read_text(encoding="utf-8")).teams["ALFA"]
            for section, content in (("Summary", "- Delivered upload validation."),
                                     ("Exec note", "Release ready."),
                                     ("Note", "Private support context."),
                                     ("Status", "AMBER - review pending")):
                self.assertEqual(saved[section].strip(), content)
            self.assertTrue(all(result == baseline_metrics for result in results))
            self.assertEqual(re.search(r'<article.*?</article>', pages["manager"], re.S).group(),
                             manager_card)

            # Establish a closed-sprint carry-over answer during the gap before Next.
            # At Next's start, neither severity nor signal kinds change: only scope does.
            board.sprint("Next", "2026-11-23", "2026-12-04")
            current.at("2026-11-20 10:00", state="Active", iteration="Proj\\Next")
            report("2026-11-21", closed="Current")
            notes_path = root / "reports" / "current" / "notes.md"
            inherited = saved_row("Actions")
            self.assertEqual(inherited["answer"], renewed)
            next_answer = "unknown: review planned"
            notes_path.write_text(notes_path.read_text(encoding="utf-8").replace(
                renewed.replace("|", "\\|"), next_answer), encoding="utf-8")
            self.assertEqual(saved_row("Actions")["answer"], next_answer)
            report("2026-11-21", closed="Current")
            self.assertTrue(pages["item"]["handled"])
            self.assertFalse(pages["item"]["worse"])
            self.assertIn('class="chip grey">Handled</span>', pages["dm"])
            self.assertEqual(pages["item"]["kinds"], ["agreed"])
            self.assertEqual(pages["item"]["level"], "amber")
            self.assertEqual(pages["item"]["sprint_path"], "Proj\\Current")
            next_baseline = saved_row("Actions")["answered"]
            gap_metrics = results[-1]
            gap_manager = re.search(r'<article.*?</article>', pages["manager"], re.S).group()

            report("2026-11-23", closed="Current")
            self.assertTrue(pages["item"]["open"])
            self.assertFalse(pages["item"]["handled"])
            self.assertTrue(pages["item"]["worse"])
            self.assertEqual(pages["item"]["sprint_path"], "Proj\\Next")
            self.assertEqual(pages["item"]["kinds"], ["agreed"])
            self.assertEqual(pages["item"]["level"], "amber")
            self.assertIn("Carry-over ↑", pages["dm"])
            item_row = next(row for row in re.findall(r"<tr>.*?</tr>", pages["dm"], re.S)
                            if "#2 Running retry work" in row)
            self.assertNotIn("Handled", item_row)
            self.assertEqual(saved_row("Actions")["answer"], next_answer)
            self.assertEqual(saved_row("Actions")["answered"], next_baseline)
            self.assertEqual(results[-1], gap_metrics)
            self.assertEqual(re.search(r'<article.*?</article>', pages["manager"], re.S).group(),
                             gap_manager)

    def test_non_tty_accepts_suggestions_and_says_so(self):
        out = io.StringIO()
        seen = {}

        def fake(args, client, ask, o):
            seen["answer"] = ask("Team?", "ALFA")
            return 0
        with mock.patch("ledger.setup.run_init", side_effect=fake), \
                mock.patch("sys.stdin", io.StringIO("")):
            code = cli.main(["init", "--query", "q", "--staff", "s", "--vacations", "v"], out=out)
        self.assertEqual((code, seen["answer"]), (0, "ALFA"))
        self.assertIn("suggestions were accepted", out.getvalue())


if __name__ == "__main__":
    unittest.main()
