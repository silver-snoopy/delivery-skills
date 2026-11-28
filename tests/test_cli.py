import base64
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ledger import cli, render
from ledger.ado import CACHE_FILE, AdoError
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
            code = cli.main(
                ["--dir", "x", "init", "--query", "q", "--staff", "s", "--vacations", "v", "--yes"], out=out
            )
        self.assertEqual((code, out.getvalue()), (1, "error: bad\n"))

    def test_report_rejects_unended_sprint_without_creating_report_files(self):
        with tempfile.TemporaryDirectory() as directory:
            vacation = os.path.join(directory, "vacations.xlsx")
            build_workbook(vacation, calendar_rows(), ["G2:AJ2", "AK2:BO2"])
            save_config(directory, Config(vacations=vacation, teams={"ALFA": {"count": "stories"}}))
            write_team(
                os.path.join(directory, "team.csv"),
                [Person("Ari Pine", ["ari@vendor.example"], "ALFA", None, None)],
            )
            b = Board().sprint("Next", "2026-11-02", "2026-11-13")
            b.item(1).at("2026-11-02 09:00", iteration="Proj\\Next", assigned="ari@vendor.example")
            os.makedirs(os.path.join(directory, "cache"))
            with open(os.path.join(directory, CACHE_FILE), "w") as f:
                json.dump(b.cache(), f)
            out = io.StringIO()
            code = cli.main(
                ["--dir", directory, "report", "--offline", "--today", "2026-11-06", "--sprint", "Next"],
                out=out,
            )
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
            save_config(
                directory,
                Config(
                    vacations=str(vacation), hold_states=["On Hold"], teams={"ALFA": {"count": "stories"}}
                ),
            )
            write_team(
                str(root / "team.csv"), [Person("Ari Pine", ["ari@vendor.example"], "ALFA", None, None)]
            )
            board = (
                Board()
                .sprint("Closed", "2026-10-26", "2026-11-06")
                .sprint("Current", "2026-11-09", "2026-11-20")
            )
            board.item(1, title="Upload validation").at(
                "2026-10-26 09:00", state="Active", iteration="Proj\\Closed", assigned="ari@vendor.example"
            ).at("2026-11-06 15:00", state="Closed")
            current = board.item(2, title="Running retry work").at(
                "2026-11-09 09:00", iteration="Proj\\Current", assigned="ari@vendor.example"
            )
            (root / "cache").mkdir()
            notes_path = root / "reports" / "closed" / "notes.md"
            pages, results = {}, []

            def capture_manager(*args):
                pages["manager"] = render_manager(*args)
                team = args[1][0]
                results.append(
                    tuple(
                        team[k]
                        for k in (
                            "planned",
                            "delivered_planned",
                            "added",
                            "removed",
                            "cycle",
                            "waiting_share",
                        )
                    )
                )
                return pages["manager"]

            def capture_dm(*args):
                pages["dm"] = render_dm(*args)
                pages["item"] = next(row for row in args[1][0]["actions"]["rows"] if row["id"] == 2)
                return pages["dm"]

            render_manager, render_dm = render.render_manager, render.render_dm

            def report(today, closed="Closed"):
                (root / CACHE_FILE).write_text(json.dumps(board.cache()), encoding="utf-8")
                out = io.StringIO()
                with (
                    mock.patch.object(render, "render_manager", side_effect=capture_manager),
                    mock.patch.object(render, "render_dm", side_effect=capture_dm),
                ):
                    code = cli.main(["--dir", directory, "report", "--offline", "--today", today], out=out)
                self.assertEqual(code, 0, out.getvalue())
                private_content = [
                    "Ari Pine",
                    "ari@vendor.example",
                    "d available",
                    "<th>Person</th>",
                    "<th>Signal</th>",
                    "Handled",
                    "Private support context.",
                    "review pending",
                    "Next",
                ]
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
            self.assertIn("1 <small>of 1</small>", pages["manager"])
            # This is an invented DM edit, never an answer on a real DM's behalf.
            answer = "chased: review pipe | and path C:\\draft"
            encoded_answer = answer.replace("\\", "\\\\").replace("|", "\\|")
            edited = (
                notes_path.read_text(encoding="utf-8")
                .replace("## Summary\n", "## Summary\n- Delivered upload validation.\n")
                .replace("## Exec note\n", "## Exec note\nRelease ready.\n")
                .replace("## Note\n", "## Note\nPrivate support context.\n")
                .replace("## Status\n", "## Status\nAMBER - review pending\n")
                .replace(" |  |  |", " | " + encoded_answer + " |  |", 1)
            )
            notes_path.write_text(edited, encoding="utf-8")
            self.assertEqual(saved_row("Actions")["answer"], answer)
            out = report("2026-11-13")
            self.assertNotIn("ALFA action:", out)
            self.assertIn('class="chip grey">Handled</span>', pages["dm"])
            first_baseline = saved_row("Actions")["answered"]
            manager_card = re.search(r"<article.*?</article>", pages["manager"], re.S).group()

            current.at("2026-11-13 10:00", state="On Hold", category="InProgress")
            out = report("2026-11-16")
            self.assertIn("On Hold ↑", pages["dm"])
            self.assertIn("ALFA action: Ari Pine #2 On Hold ↑ -", out)
            self.assertEqual(saved_row("Actions")["answered"], first_baseline)
            self.assertEqual(saved_row("Actions")["answer"], answer)

            renewed = answer + "; checked again"
            notes_path.write_text(
                notes_path.read_text(encoding="utf-8").replace(
                    " | " + first_baseline + " |", "; checked again | " + first_baseline + " |"
                ),
                encoding="utf-8",
            )
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
            for section, content in (
                ("Summary", "- Delivered upload validation."),
                ("Exec note", "Release ready."),
                ("Note", "Private support context."),
                ("Status", "AMBER - review pending"),
            ):
                self.assertEqual(saved[section].strip(), content)
            self.assertTrue(all(result == baseline_metrics for result in results))
            self.assertEqual(
                re.search(r"<article.*?</article>", pages["manager"], re.S).group(), manager_card
            )

            # Establish a closed-sprint carry-over answer during the gap before Next.
            # At Next's start, neither severity nor signal kinds change: only scope does.
            board.sprint("Next", "2026-11-23", "2026-12-04")
            current.at("2026-11-20 10:00", state="Active", iteration="Proj\\Next")
            report("2026-11-21", closed="Current")
            notes_path = root / "reports" / "current" / "notes.md"
            inherited = saved_row("Actions")
            self.assertEqual(inherited["answer"], renewed)
            next_answer = "review planned"
            notes_path.write_text(
                notes_path.read_text(encoding="utf-8").replace(renewed.replace("|", "\\|"), next_answer),
                encoding="utf-8",
            )
            self.assertEqual(saved_row("Actions")["answer"], next_answer)
            report("2026-11-21", closed="Current")
            self.assertTrue(pages["item"]["handled"])
            self.assertFalse(pages["item"]["worse"])
            self.assertIn('class="chip grey">Handled</span>', pages["dm"])
            self.assertEqual(pages["item"]["kinds"], ["carry"])
            self.assertEqual(pages["item"]["level"], "amber")
            self.assertEqual(pages["item"]["sprint_path"], "Proj\\Current")
            next_baseline = saved_row("Actions")["answered"]
            gap_metrics = results[-1]
            gap_manager = re.search(r"<article.*?</article>", pages["manager"], re.S).group()

            report("2026-11-23", closed="Current")
            self.assertTrue(pages["item"]["open"])
            self.assertFalse(pages["item"]["handled"])
            self.assertTrue(pages["item"]["worse"])
            self.assertEqual(pages["item"]["sprint_path"], "Proj\\Next")
            self.assertEqual(pages["item"]["kinds"], ["carry"])
            self.assertEqual(pages["item"]["level"], "amber")
            self.assertIn("Carry-over ↑", pages["dm"])
            item_row = next(
                row
                for row in re.findall(r"<tr>.*?</tr>", pages["dm"], re.S)
                if "#2 Running retry work" in row
            )
            self.assertNotIn("Handled", item_row)
            self.assertEqual(saved_row("Actions")["answer"], next_answer)
            self.assertEqual(saved_row("Actions")["answered"], next_baseline)
            self.assertEqual(results[-1], gap_metrics)
            self.assertEqual(re.search(r"<article.*?</article>", pages["manager"], re.S).group(), gap_manager)

    def test_po_satisfaction_history_survives_offline_reruns_and_checks_name_leaks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vacation = root / "vacations.xlsx"
            rows = calendar_rows()
            rows[4] = {4: "Ari Pine", 3: "Alfa"}
            del rows[5]
            build_workbook(str(vacation), rows, ["G2:AJ2", "AK2:BO2"])
            cfg = Config(
                vacations=str(vacation),
                manager_metrics=["planned", "delivered", "po_satisfaction"],
                teams={"ALFA": {"count": "stories"}},
            )
            save_config(directory, cfg)
            write_team(
                str(root / "team.csv"), [Person("Ari Pine", ["ari@vendor.example"], "ALFA", None, None)]
            )
            board = (
                Board()
                .sprint("Sprint 28", "2026-10-13", "2026-10-24")
                .sprint("Sprint 29", "2026-10-27", "2026-11-07")
            )
            board.item(1, title="Old work").at(
                "2026-10-13 09:00", state="Active", iteration="Proj\\Sprint 28", assigned="ari@vendor.example"
            ).at("2026-10-20 15:00", state="Closed")
            board.item(2, title="New work").at(
                "2026-10-27 09:00", state="Active", iteration="Proj\\Sprint 29", assigned="ari@vendor.example"
            ).at("2026-11-04 15:00", state="Closed")
            (root / "cache").mkdir()
            (root / CACHE_FILE).write_text(json.dumps(board.cache()), encoding="utf-8")

            # Prior sprint folder with a PO rating of 3
            s28_dir = root / "reports" / "sprint-28"
            s28_dir.mkdir(parents=True)
            (s28_dir / "notes.md").write_text(
                "<!-- ledger notes: ALFA=Proj\\Sprint 28 -->\n# ALFA\n## PO satisfaction\n3/5 - Okay\n",
                encoding="utf-8",
            )

            # Current sprint folder with a PO rating of 5 and a comment mentioning Ari Pine
            s29_dir = root / "reports" / "sprint-29"
            s29_dir.mkdir(parents=True)
            (s29_dir / "notes.md").write_text(
                "<!-- ledger notes: ALFA=Proj\\Sprint 29 -->\n# ALFA\n## Summary\nShipped.\n"
                "## PO satisfaction\n5/5 - Ari Pine handled release well\n",
                encoding="utf-8",
            )

            # Two consecutive --offline runs must preserve po_history, arrow, trend, and name-leak warning
            for _run in range(2):
                out = io.StringIO()
                code = cli.main(["--dir", directory, "report", "--offline", "--today", "2026-11-13"], out=out)
                self.assertEqual(code, 0, out.getvalue())
                self.assertIn("warning: manager file mentions Ari Pine", out.getvalue())
                manager = (s29_dir / "manager_2026-11-13.html").read_text(encoding="utf-8")
                self.assertIn("5 <small>/ 5</small>", manager)
                self.assertIn('<span class="chip green">Very satisfied</span>', manager)
                self.assertIn('<span class="d up">▲ from 3 vs Sprint 28</span>', manager)
                self.assertIn("<td>PO satisfaction</td><td>3/5</td><td>5/5</td>", manager)
                self.assertIn("PO feedback: Ari Pine handled release well", manager)

            # Disabling po_satisfaction suppresses the rendered comment and therefore the name-leak warning
            cfg.manager_metrics = ["planned", "delivered"]
            save_config(directory, cfg)
            out = io.StringIO()
            code = cli.main(["--dir", directory, "report", "--offline", "--today", "2026-11-13"], out=out)
            self.assertEqual(code, 0, out.getvalue())
            self.assertNotIn("warning: manager file mentions Ari Pine", out.getvalue())
            manager = (s29_dir / "manager_2026-11-13.html").read_text(encoding="utf-8")
            self.assertNotIn("PO satisfaction", manager)
            self.assertNotIn("PO feedback:", manager)

    def test_non_tty_accepts_suggestions_and_says_so(self):
        out = io.StringIO()
        seen = {}

        def fake(args, client, ask, o):
            seen["answer"] = ask("Team?", "ALFA")
            return 0

        with mock.patch("ledger.setup.run_init", side_effect=fake), mock.patch("sys.stdin", io.StringIO("")):
            code = cli.main(["init", "--query", "q", "--staff", "s", "--vacations", "v"], out=out)
        self.assertEqual((code, seen["answer"]), (0, "ALFA"))
        self.assertIn("suggestions were accepted", out.getvalue())

    def _theme_workspace(self, root: Path, people=None, **cfg_kwargs):
        root.mkdir(parents=True, exist_ok=True)
        vacation = root / "vacations.xlsx"
        rows = calendar_rows()
        rows[4] = {4: "Ari Pine", 3: "Alfa"}
        del rows[5]
        build_workbook(str(vacation), rows, ["G2:AJ2", "AK2:BO2"])
        cfg = Config(vacations=str(vacation), teams={"ALFA": {"count": "stories"}}, **cfg_kwargs)
        save_config(str(root), cfg)
        write_team(
            str(root / "team.csv"),
            people or [Person("Ari Pine", ["ari@vendor.example"], "ALFA", None, None)],
        )
        board = Board().sprint("Sprint 29", "2026-10-27", "2026-11-07")
        board.item(1, title="Upload validation").at(
            "2026-10-27 09:00", state="Active", iteration="Proj\\Sprint 29", assigned="ari@vendor.example"
        ).at("2026-11-04 15:00", state="Closed")
        (root / "cache").mkdir(exist_ok=True)
        (root / CACHE_FILE).write_text(json.dumps(board.cache()), encoding="utf-8")
        return cfg

    def _make_theme_pack(self, pack: Path, css=":root{--accent:#224466}", manifest=None, logo_bytes=None):
        pack.mkdir(parents=True, exist_ok=True)
        if css is not None:
            (pack / "theme.css").write_text(css, encoding="utf-8")
        if logo_bytes is not None:
            (pack / "logo.png").write_bytes(logo_bytes)
        if manifest is not None:
            (pack / "theme.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_theme_target_routing_for_manager_dm_and_person(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            pack = work / "brand"
            self._make_theme_pack(
                pack,
                css=":root{--accent:#224466}",
                logo_bytes=b"\x89PNG\r\n\x1a\nfake",
                manifest={
                    "logo": "logo.png",
                    "logo_alt": "Acme",
                    "header_note": "Exec Review",
                    "footer_note": "Brand Footer",
                },
            )
            cfg = self._theme_workspace(work, theme="brand")

            # Default theme_reports=["manager"]: manager gets theme, dm and person do not
            out = io.StringIO()
            code = cli.main(["--dir", str(work), "report", "--offline", "--today", "2026-11-13"], out=out)
            self.assertEqual(code, 0, out.getvalue())
            mgr = (work / "reports" / "sprint-29" / "manager_2026-11-13.html").read_text(encoding="utf-8")
            dm = (work / "reports" / "sprint-29" / "dm_2026-11-13.html").read_text(encoding="utf-8")
            self.assertIn("--accent:#224466", mgr)
            self.assertIn("Content-Security-Policy", mgr)
            self.assertIn("Exec Review", mgr)
            self.assertIn("Brand Footer", mgr)
            self.assertIn('class="brand-logo"', mgr)
            self.assertNotIn("--accent:#224466", dm)
            self.assertNotIn("Content-Security-Policy", dm)
            self.assertNotIn("brand-logo", dm)

            out = io.StringIO()
            code = cli.main(
                ["--dir", str(work), "person", "ari@vendor.example", "--offline", "--today", "2026-11-13"],
                out=out,
            )
            self.assertEqual(code, 0, out.getvalue())
            person_html = (work / "people" / "person_Ari_Pine_2026-11-13.html").read_text(encoding="utf-8")
            self.assertNotIn("--accent:#224466", person_html)
            self.assertNotIn("Content-Security-Policy", person_html)

            # Enable all three targets: manager, dm, and person all receive theme; .private stays first
            cfg.theme_reports = ["manager", "dm", "person"]
            save_config(str(work), cfg)
            out = io.StringIO()
            code = cli.main(["--dir", str(work), "report", "--offline", "--today", "2026-11-13"], out=out)
            self.assertEqual(code, 0, out.getvalue())
            dm = (work / "reports" / "sprint-29" / "dm_2026-11-13.html").read_text(encoding="utf-8")
            self.assertIn("--accent:#224466", dm)
            self.assertIn("Content-Security-Policy", dm)
            self.assertIn('class="brand-logo"', dm)
            self.assertIn("Brand Footer", dm)
            self.assertLess(dm.index('class="private"'), dm.index('class="brand-header"'))
            self.assertLess(dm.index('class="private"'), dm.index('class="brand-head"'))

            out = io.StringIO()
            code = cli.main(
                ["--dir", str(work), "person", "ari@vendor.example", "--offline", "--today", "2026-11-13"],
                out=out,
            )
            self.assertEqual(code, 0, out.getvalue())
            person_html = (work / "people" / "person_Ari_Pine_2026-11-13.html").read_text(encoding="utf-8")
            self.assertIn("--accent:#224466", person_html)
            self.assertIn("Content-Security-Policy", person_html)
            self.assertIn('class="brand-logo"', person_html)
            self.assertIn("Exec Review", person_html)
            self.assertIn("Brand Footer", person_html)
            self.assertLess(person_html.index('class="private"'), person_html.index('class="brand-header"'))
            self.assertLess(person_html.index('class="private"'), person_html.index('class="brand-head"'))

    def test_cli_theme_override_none_excluded_targets_and_cwd_vs_dir_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            work.mkdir()
            cwd = root / "cwd"
            cwd.mkdir()
            (work / "cfg.css").write_text(":root{--accent:#111111}", encoding="utf-8")
            (cwd / "cli.css").write_text(":root{--accent:#999999}", encoding="utf-8")
            cfg = self._theme_workspace(work, theme="cfg.css")

            # Config relative path resolves against --dir even when CWD differs
            with mock.patch("os.getcwd", return_value=str(cwd)):
                out = io.StringIO()
                code = cli.main(["--dir", str(work), "report", "--offline", "--today", "2026-11-13"], out=out)
                self.assertEqual(code, 0, out.getvalue())
                mgr = (work / "reports" / "sprint-29" / "manager_2026-11-13.html").read_text(encoding="utf-8")
                self.assertIn("--accent:#111111", mgr)

                # CLI --theme relative path resolves against CWD and overrides config.json
                out = io.StringIO()
                code = cli.main(
                    [
                        "--dir",
                        str(work),
                        "report",
                        "--offline",
                        "--today",
                        "2026-11-13",
                        "--theme",
                        "cli.css",
                    ],
                    out=out,
                )
                self.assertEqual(code, 0, out.getvalue())
                mgr = (work / "reports" / "sprint-29" / "manager_2026-11-13.html").read_text(encoding="utf-8")
                self.assertIn("--accent:#999999", mgr)
                self.assertNotIn("--accent:#111111", mgr)

            # Person skips filesystem access when excluded; passing --theme warns and skips access
            cfg.theme = "missing-configured.css"
            save_config(str(work), cfg)
            out = io.StringIO()
            code = cli.main(
                ["--dir", str(work), "person", "ari@vendor.example", "--offline", "--today", "2026-11-13"],
                out=out,
            )
            self.assertEqual(code, 0, out.getvalue())
            self.assertNotIn("warning: theme_reports", out.getvalue())

            out = io.StringIO()
            code = cli.main(
                [
                    "--dir",
                    str(work),
                    "person",
                    "ari@vendor.example",
                    "--offline",
                    "--today",
                    "2026-11-13",
                    "--theme",
                    "also-missing.css",
                ],
                out=out,
            )
            self.assertEqual(code, 0, out.getvalue())
            self.assertIn(
                "warning: theme_reports excludes person; --theme has no effect on this command",
                out.getvalue(),
            )

            # --theme none disables even if configured path is missing
            out = io.StringIO()
            code = cli.main(
                ["--dir", str(work), "report", "--offline", "--today", "2026-11-13", "--theme", "NONE"],
                out=out,
            )
            self.assertEqual(code, 0, out.getvalue())
            mgr = (work / "reports" / "sprint-29" / "manager_2026-11-13.html").read_text(encoding="utf-8")
            self.assertNotIn("Content-Security-Policy", mgr)

            # report with theme_reports=[] skips filesystem access and warns on explicit --theme
            cfg.theme_reports = []
            save_config(str(work), cfg)
            out = io.StringIO()
            code = cli.main(
                [
                    "--dir",
                    str(work),
                    "report",
                    "--offline",
                    "--today",
                    "2026-11-13",
                    "--theme",
                    "missing.css",
                ],
                out=out,
            )
            self.assertEqual(code, 0, out.getvalue())
            self.assertIn(
                "warning: theme_reports excludes manager and dm; --theme has no effect on this command",
                out.getvalue(),
            )

    def test_theme_failure_boundary_writes_nothing_for_report_and_person(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            bad_css = work / "bad.css"
            self._theme_workspace(work, theme="missing.css", theme_reports=["manager", "person"])

            # Missing theme on report: exits 1, writes neither notes.md nor HTML
            out = io.StringIO()
            code = cli.main(["--dir", str(work), "report", "--offline", "--today", "2026-11-13"], out=out)
            self.assertEqual(code, 1)
            self.assertIn("error: theme path not found", out.getvalue())
            self.assertFalse((work / "reports").exists())

            # Missing theme on person: exits 1, writes no HTML
            out = io.StringIO()
            code = cli.main(
                ["--dir", str(work), "person", "ari@vendor.example", "--offline", "--today", "2026-11-13"],
                out=out,
            )
            self.assertEqual(code, 1)
            self.assertIn("error: theme path not found", out.getvalue())
            self.assertFalse((work / "people").exists())

            # Malformed CSS (</style> breakout) on report and person: exits 1, writes nothing
            bad_css.write_text("body{color:red}</style><script>alert(1)</script>", encoding="utf-8")
            for cmd in (
                ["--dir", str(work), "report", "--offline", "--today", "2026-11-13", "--theme", str(bad_css)],
                [
                    "--dir",
                    str(work),
                    "person",
                    "ari@vendor.example",
                    "--offline",
                    "--today",
                    "2026-11-13",
                    "--theme",
                    str(bad_css),
                ],
            ):
                with self.subTest(cmd=cmd[2]):
                    out = io.StringIO()
                    code = cli.main(cmd, out=out)
                    self.assertEqual(code, 1)
                    self.assertIn("error: theme CSS must not contain '</style'", out.getvalue())
            self.assertFalse((work / "reports").exists())
            self.assertFalse((work / "people").exists())

    def test_manager_name_scan_with_theme_catches_visible_leaks_and_ignores_css_and_logo_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            pack = work / "brand"
            # Base64 of these bytes literally contains "+Ari+" (word-bounded "Ari" in raw HTML src)
            logo_bytes = base64.b64decode("AA+Ari+A")
            self.assertIn("+Ari+", base64.b64encode(logo_bytes).decode("ascii"))
            people = [
                Person("Ari Pine", ["ari@vendor.example"], "ALFA", None, None),
                Person("Ari", ["ari.short@vendor.example"], "ALFA", None, None),
            ]
            self._theme_workspace(work, people=people, theme="brand")

            # Clean manifest + CSS comment mentioning Ari Pine + base64 logo containing +Ari+: zero name warnings
            self._make_theme_pack(
                pack,
                css="/* Ari Pine and Ari */\n:root{--accent:#224466}",
                logo_bytes=logo_bytes,
                manifest={
                    "logo": "logo.png",
                    "logo_alt": "Acme",
                    "header_note": "Review",
                    "footer_note": "Ops",
                },
            )
            out = io.StringIO()
            code = cli.main(["--dir", str(work), "report", "--offline", "--today", "2026-11-13"], out=out)
            self.assertEqual(code, 0, out.getvalue())
            self.assertNotIn("warning: manager file mentions", out.getvalue())

            # Manifest fields containing roster name trigger warnings
            for field in ("header_note", "footer_note", "logo_alt"):
                with self.subTest(field=field):
                    manifest = {"logo": "logo.png", "logo_alt": "Acme", "header_note": "", "footer_note": ""}
                    manifest[field] = "Owner: Ari Pine"
                    self._make_theme_pack(
                        pack, css=":root{--accent:#224466}", logo_bytes=logo_bytes, manifest=manifest
                    )
                    out = io.StringIO()
                    code = cli.main(
                        ["--dir", str(work), "report", "--offline", "--today", "2026-11-13"], out=out
                    )
                    self.assertEqual(code, 0, out.getvalue())
                    self.assertIn("warning: manager file mentions Ari Pine", out.getvalue())

            # Literal "data: Ari Pine" and escaped "<style>Ari Pine</style>" in notes still trigger warnings
            self._make_theme_pack(
                pack,
                css=":root{--accent:#224466}",
                logo_bytes=logo_bytes,
                manifest={"logo": "logo.png", "logo_alt": "Acme"},
            )
            notes_path = work / "reports" / "sprint-29" / "notes.md"
            for payload in ("data: Ari Pine", "<style>Ari Pine</style>"):
                with self.subTest(payload=payload):
                    notes_path.write_text(
                        f"<!-- ledger notes: ALFA=Proj\\Sprint 29 -->\n# ALFA\n## Summary\n{payload}\n",
                        encoding="utf-8",
                    )
                    out = io.StringIO()
                    code = cli.main(
                        ["--dir", str(work), "report", "--offline", "--today", "2026-11-13"], out=out
                    )
                    self.assertEqual(code, 0, out.getvalue())
                    self.assertIn("warning: manager file mentions Ari Pine", out.getvalue())

            # Empty or whitespace CLI --theme fails cleanly instead of silently disabling
            for blank in ("", "   "):
                with self.subTest(blank=blank):
                    out = io.StringIO()
                    code = cli.main(
                        [
                            "--dir",
                            str(work),
                            "report",
                            "--offline",
                            "--today",
                            "2026-11-13",
                            "--theme",
                            blank,
                        ],
                        out=out,
                    )
                    self.assertEqual(code, 1)
                    self.assertIn("error: theme path must not be empty", out.getvalue())


if __name__ == "__main__":
    unittest.main()
