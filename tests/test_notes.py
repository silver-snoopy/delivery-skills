"""Invented notes fixtures; never read or open report HTML."""

import json
import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

from ledger.config import carry_reasons, load_report_notes, parse_notes, reconcile_notes, write_report_notes

OLD = {"path": "Project/One", "name": "One", "start": "2026-10-19", "end": "2026-10-30"}
NEW = {"path": "Project/Two", "name": "Two", "start": "2026-11-02", "end": "2026-11-13"}
RUN = "Project/Three"
TODAY = date(2026, 11, 16)
HEADER = "| Person | Work item | Problem | Answer | Answered |\n|---|---|---|---|---|\n"


def row(item=81, person="Ada Example", **kw):
    value = dict(
        id=item,
        person=person,
        title="Invented task",
        sprint="Now",
        sprint_path=RUN,
        level="amber",
        badge="Waiting",
        kinds=["old", "waiting"],
        problem="Waiting for a slot",
        ask="Ask for a slot",
        age=12,
        open=True,
        carry=item is not None,
    )
    value.update(kw)
    return value


def actions(*rows):
    return {"ALFA": {"people": [], "rows": list(rows), "current": None}}


def markdown(
    answer="",
    answered="",
    item="#81 Invented task",
    person="Ada Example",
    problem="Waiting for a slot",
    team="ALFA",
    marker=NEW["path"],
):
    return (
        f"<!-- ledger notes: {team}={marker} -->\n# {team}\n## Summary\n- Delivered a fixture.\n"
        "## Exec note\nExternal text\n## Note\nPrivate text\n## Status\n\n## Actions\n"
        + HEADER
        + f"| {person} | {item} | {problem} | {answer} | {answered} |\n"
        + "## Resolved\n"
        + HEADER
    )


class NotesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.folder = os.path.join(self.root, "report")
        self.selected = {"ALFA": NEW}
        self.warnings = []

    def load(self, folder=None, selected=None):
        return load_report_notes(
            self.root,
            folder or self.folder,
            self.selected if selected is None else selected,
            [OLD, NEW],
            self.warnings.append,
        )

    def save_text(self, text, folder=None):
        folder = folder or self.folder
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "notes.md"), "w", encoding="utf-8") as f:
            f.write(text)

    def read(self, name="notes.md", folder=None):
        with open(os.path.join(folder or self.folder, name), encoding="utf-8") as f:
            return f.read()

    def cycle(self, notes, rows=None, today=TODAY):
        result = reconcile_notes(
            notes, actions(*(rows if rows is not None else [row()])), self.selected, today
        )
        write_report_notes(self.folder, notes)
        return result["ALFA"]["rows"]

    def test_lossless_sections_cells_and_raw_lines_twice(self):
        text = "\ufeff" + markdown(r"chased: A\|B at C:\\temp\\file")
        text = text.replace("- Delivered a fixture.", "  - Delivered a fixture.  \n\nPlain bullet")
        text = text.replace("External text", "First line  \nsecond | line\\tail")
        text = text.replace("## Actions", "## Unknown heading\nkeep | this\\line\n## Actions")
        text = text.replace("## Resolved", "| malformed | preserve |\n## Resolved")
        text += "# UNLISTED\n## Note\nkeep this unknown team\n"
        self.save_text(text)
        n = self.load()
        self.assertEqual(n.summary["ALFA"], "Delivered a fixture.\nPlain bullet")
        self.assertEqual(n.exec_note["ALFA"], "First line  \nsecond | line\\tail")
        self.assertTrue(any("UNLISTED" in w for w in self.warnings))
        for _ in range(2):
            result = self.cycle(n)
            self.assertEqual(result[0]["answer"], "chased: A|B at C:\\temp\\file")
            n = self.load()
        out = self.read()
        for raw in (
            "  - Delivered a fixture.  ",
            "keep | this\\line",
            "| malformed | preserve |",
            "keep this unknown team",
        ):
            self.assertEqual(out.count(raw), 1)
        offsets = [
            out.index("## " + s)
            for s in ("Summary", "Exec note", "PO satisfaction", "Note", "Status", "Actions", "Resolved")
        ]
        self.assertEqual(offsets, sorted(offsets))
        self.assertTrue(n.warnings)

    def test_new_notes_have_fixed_sections_and_blank_status(self):
        n = self.load()
        self.cycle(n)
        self.assertFalse(n.status)
        self.assertTrue(self.read().startswith("<!-- ledger notes: ALFA=Project/Two -->"))
        self.assertEqual(self.read().count("## "), 7)
        self.assertEqual(self.load().teams["ALFA"]["Actions"][0]["answer"], "")

    def test_baseline_lifecycle_and_tool_owned_edits(self):
        self.save_text(markdown("chased: promised Friday"))
        n = self.load()
        first = self.cycle(n)[0]
        self.assertTrue(first["handled"])
        self.assertEqual(first["answered"], "2026-11-16 amber Chase, Waiting")
        text = self.read().replace("2026-11-16 amber Chase, Waiting", "2099-01-01 red On Hold")
        text = text.replace("Waiting for a slot", "DM edited tool cell")
        self.save_text(text)
        second = self.cycle(self.load(), [row(age=90)], date(2026, 11, 17))[0]
        self.assertEqual(second["answered"], first["answered"])
        self.assertTrue(second["handled"])
        self.save_text(self.read().replace("chased: promised Friday", "chased: revised promise"))
        third = self.cycle(self.load(), [row(level="red", kinds=["held"])], date(2026, 11, 18))[0]
        self.assertEqual(third["answered"], "2026-11-18 red On Hold")
        self.assertTrue(third["handled"])
        self.save_text(self.read().replace("chased: revised promise", ""))
        fourth = self.cycle(self.load())[0]
        self.assertFalse(fourth["handled"])
        self.assertEqual(fourth["answered"], "")

    def test_all_worse_rules_and_secondary_kind_age_non_rules(self):
        for change, expected in [
            ({}, False),
            ({"kinds": ["waiting"]}, False),
            ({"age": 400}, False),
            ({"level": "red"}, True),
            ({"kinds": ["old", "waiting", "held"]}, True),
            ({"sprint_path": "Project/Four"}, True),
            ({"sprint_path": "Project/Four", "open": False}, False),
        ]:
            with self.subTest(change=change):
                self.save_text(markdown("chased: promised Friday"))
                state = os.path.join(self.folder, ".notes-state.json")
                if os.path.exists(state):
                    os.unlink(state)
                self.cycle(self.load())
                out = self.cycle(self.load(), [row(**change)])[0]
                self.assertEqual(out["worse"], expected)
                self.assertEqual(out["handled"], not expected)

    def test_resolved_grey_green_restore_and_reassignment(self):
        self.save_text(markdown("split next sprint"))
        self.cycle(self.load())
        for level in ("green", "grey"):
            out = self.cycle(self.load(), [row(level=level, kinds=[], badge="OK")])[0]
            self.assertTrue(out["handled"])
            n = self.load()
            self.assertFalse(n.teams["ALFA"]["Actions"])
            self.assertEqual(len(n.teams["ALFA"]["Resolved"]), 1)
            self.assertEqual(carry_reasons(n, "ALFA").get(81, ""), "split next sprint")
        self.cycle(self.load(), [])
        out = self.cycle(self.load(), [row(person="Ben Example")])[0]
        self.assertEqual(out["answer"], "split next sprint")
        self.assertTrue(out["handled"])
        self.assertFalse(self.load().teams["ALFA"]["Resolved"])

    def test_carry_reasons_and_team_isolation(self):
        for answer, expected in [
            ("split in refinement", "split in refinement"),
            ("  blocked   on\tAPI  ", "blocked on API"),
        ]:
            with self.subTest(answer=answer):
                n = parse_notes(markdown(answer) + markdown("other team reason", team="BETA"))
                selected = {"ALFA": NEW, "BETA": NEW}
                result = reconcile_notes(
                    n, dict(actions(row()), BETA=actions(row())["ALFA"]), selected, TODAY
                )
                self.assertEqual(carry_reasons(n, "ALFA").get(81, ""), expected)
                self.assertEqual(carry_reasons(n, "BETA")[81], "other team reason")
                self.assertTrue(result["ALFA"]["rows"][0]["handled"])

    def test_person_identity_includes_badge(self):
        n = self.load()
        rows = [
            row(None, badge="No work", kinds=["none"], problem="No work, 9 days available"),
            row(None, badge="Too much WIP", kinds=["wip"], problem="Too much WIP: finish one"),
        ]
        self.cycle(n, rows)
        self.save_text(
            self.read().replace(
                "| No work: No work, 9 days available |  |",
                "| No work: No work, 9 days available | lent to spike |",
            )
        )
        out = self.cycle(self.load(), rows)
        self.assertEqual([r["answer"] for r in out], ["lent to spike", ""])
        self.assertEqual([r["handled"] for r in out], [True, False])

    def test_valid_import_and_invalid_metadata_preserve_answer(self):
        for meta, handled in [
            ("2026-11-10 amber Chase, Waiting", True),
            ("2026-13-40 amber Waiting", False),
            ("nonsense", False),
        ]:
            with self.subTest(meta=meta):
                n = parse_notes(markdown("chased: promise", meta))
                out = self.cycle(n, [row(sprint_path=NEW["path"], carry=False)])[0]
                self.assertEqual(out["handled"], handled)
                self.assertEqual(out["answer"], "chased: promise")
                if not handled:
                    self.assertTrue(n.warnings)

    def test_predecessor_per_team_and_no_future_import(self):
        for name, sprint, answer in [("older", OLD, "old answer"), ("newer", NEW, "future answer")]:
            self.save_text(markdown(answer, marker=sprint["path"]), os.path.join(self.root, name))
        self.selected = {"ALFA": OLD}
        n = self.load()
        out = self.cycle(n, [row(sprint_path=OLD["path"])])[0]
        self.assertEqual(out["answer"], "old answer")
        self.assertEqual(n.summary["ALFA"], "Delivered a fixture.")

    def test_changed_sprint_only_open_action_answers_no_renewal_until_edit(self):
        old_folder = os.path.join(self.root, "old")
        self.save_text(markdown("split", marker=OLD["path"]), old_folder)
        old = load_report_notes(self.root, old_folder, {"ALFA": OLD}, [OLD, NEW], self.warnings.append)
        reconcile_notes(old, actions(row(sprint_path=OLD["path"])), {"ALFA": OLD}, date(2026, 11, 2))
        write_report_notes(old_folder, old)
        n = self.load()
        out = self.cycle(n)[0]
        self.assertEqual(out["answer"], "split")
        self.assertTrue(out["worse"])
        self.assertFalse(out["handled"])
        self.assertFalse(n.summary.get("ALFA"))
        self.assertNotIn(81, carry_reasons(n, "ALFA"))
        out = self.cycle(self.load())[0]
        self.assertFalse(out["handled"])
        self.assertNotIn(81, carry_reasons(self.load(), "ALFA"))
        self.save_text(self.read().replace("split", "renewed split"))
        n = self.load()
        self.assertTrue(self.cycle(n)[0]["handled"])
        self.assertEqual(carry_reasons(n, "ALFA").get(81, ""), "renewed split")

    def test_changed_sprint_copies_open_resolved_but_drops_closed_and_empty_rows(self):
        source = parse_notes(markdown("closed answer", marker=OLD["path"]))
        reconcile_notes(source, actions(row()), {"ALFA": OLD}, TODAY)
        source.teams["ALFA"]["Actions"].extend(
            [
                dict(
                    id=None,
                    person="Ada Example",
                    badge="No work",
                    work="–",
                    problem="No work",
                    answer="person answer",
                    answered="2026-11-16 red No work",
                ),
                dict(
                    id=82, person="Ada Example", work="#82 Empty", problem="Waiting", answer="", answered=""
                ),
            ]
        )
        source.teams["ALFA"]["Resolved"].append(
            dict(
                id=83,
                person="Ada Example",
                work="#83 Resolved",
                problem="Waiting",
                answer="resolved answer",
                answered="2026-11-16 amber Waiting",
            )
        )
        write_report_notes(os.path.join(self.root, "old"), source)
        n = self.load()
        self.cycle(
            n,
            [
                row(open=False),
                row(None, badge="No work", kinds=["none"], problem="No work"),
                row(82),
                row(83),
            ],
        )
        for text in ("closed answer",):
            self.assertNotIn(text, self.read())
        saved = self.load().teams["ALFA"]["Actions"]
        self.assertEqual([r["answer"] for r in saved if r["id"] is None], ["person answer"])
        self.assertEqual(next(r["answer"] for r in saved if r["id"] == 83), "resolved answer")
        self.assertTrue(all(not r["answer"] for r in saved if r["id"] in (81, 82)))

    def test_mixed_calendars_missing_sprint_and_unknown_marker(self):
        self.save_text(
            markdown("agreed: old", marker=OLD["path"])
            + markdown("beta answer", team="BETA", marker=NEW["path"]),
            os.path.join(self.root, "prior"),
        )
        self.selected = {"ALFA": NEW, "BETA": NEW, "GAMMA": None}
        n = self.load()
        self.assertFalse(n.summary.get("ALFA"))
        self.assertEqual(n.summary["BETA"], "Delivered a fixture.")
        # Existing missing-sprint sections and unknown teams survive.
        self.save_text(markdown(team="GAMMA") + markdown(team="UNKNOWN", marker="bad/path"))
        n = self.load()
        reconcile_notes(n, {}, self.selected, TODAY)
        write_report_notes(self.folder, n)
        self.assertIn("# GAMMA\n", self.read())
        self.assertIn("# UNKNOWN\n", self.read())
        self.assertTrue(self.warnings)

    def test_malformed_state_and_interrupted_pair_recover_without_answer_loss(self):
        self.save_text(markdown("chased: first"))
        self.cycle(self.load())
        self.save_text(self.read().replace("chased: first", "chased: second"))
        original = os.replace

        def fail_state(src, dst):
            if dst.endswith(".notes-state.json"):
                raise OSError("invented interruption")
            return original(src, dst)

        with patch("ledger.config.os.replace", side_effect=fail_state), self.assertRaises(OSError):
            self.cycle(self.load(), [row(level="red")], date(2026, 11, 17))
        out = self.cycle(self.load(), [row(level="red")], date(2026, 11, 17))[0]
        self.assertEqual(out["answer"], "chased: second")
        self.assertEqual(out["answered"], "2026-11-17 red Chase, Waiting")
        with open(os.path.join(self.folder, ".notes-state.json"), "w") as f:
            f.write("{bad")
        self.assertEqual(self.cycle(self.load(), [row(level="red")])[0]["answer"], "chased: second")
        self.assertTrue(self.warnings)

    def test_same_closed_sprint_copies_all_sections_state_and_resolved(self):
        prior = os.path.join(self.root, "prior")
        self.save_text(markdown("chased: baseline"), prior)
        n = load_report_notes(self.root, prior, self.selected, [OLD, NEW], self.warnings.append)
        reconcile_notes(n, actions(row(carry=False)), self.selected, date(2026, 11, 15))
        reconcile_notes(n, actions(), self.selected, TODAY)
        write_report_notes(prior, n)
        n = self.load()
        self.assertEqual(n.summary["ALFA"], "Delivered a fixture.")
        self.assertEqual(n.note["ALFA"], "Private text")
        self.assertEqual(n.exec_note["ALFA"], "External text")
        out = self.cycle(n, [row(carry=False)])[0]
        self.assertTrue(out["handled"])
        self.assertEqual(out["answered"], "2026-11-15 amber Chase, Waiting")

    def test_predecessors_choose_per_team_with_tie_break_and_skip_unknown_paths(self):
        self.save_text(
            markdown("earlier alpha", marker=OLD["path"]) + markdown("current beta", team="BETA"),
            os.path.join(self.root, "a"),
        )
        self.save_text(
            markdown("current alpha") + markdown("earlier beta", team="BETA", marker=OLD["path"]),
            os.path.join(self.root, "b"),
        )
        self.save_text(markdown("tie winner"), os.path.join(self.root, "z"))
        self.save_text(markdown("unresolvable", marker="unknown/path"), os.path.join(self.root, "zz"))
        self.selected = {"ALFA": NEW, "BETA": NEW}
        n = self.load()
        self.assertEqual(n.teams["ALFA"]["Actions"][0]["answer"], "tie winner")
        self.assertEqual(n.teams["BETA"]["Actions"][0]["answer"], "current beta")
        self.assertTrue(any("unknown/path" in w and "skipped" in w for w in self.warnings))

    def test_clearing_a_disappeared_row_records_clear_before_same_answer_reentry(self):
        self.save_text(markdown("agreed: split"))
        self.cycle(self.load())
        self.cycle(self.load(), [])
        self.save_text(self.read().replace("agreed: split", ""))
        self.cycle(self.load(), [])
        state = json.loads(self.read(".notes-state.json"))
        self.assertEqual(state["teams"]["ALFA"]["i:81"]["answer"], "")
        self.cycle(self.load(), [row(level="red")])
        self.save_text(
            self.read().replace("| Waiting for a slot |  |", "| Waiting for a slot | agreed: split |")
        )
        out = self.cycle(self.load(), [row(level="red")], date(2026, 11, 17))[0]
        self.assertTrue(out["handled"])
        self.assertEqual(out["answered"], "2026-11-17 red Chase, Waiting")

    def test_edit_or_import_answer_when_row_is_absent_keeps_scoped_reason(self):
        self.save_text(markdown("split"))
        self.cycle(self.load(), [row(kinds=["carry"])])
        self.cycle(self.load(), [])
        self.save_text(self.read().replace("split", "revised reason"))
        n = self.load()
        self.cycle(n, [])
        self.assertEqual(carry_reasons(n, "ALFA").get(81, ""), "revised reason")
        # Loss of the sidecar imports non-locally, so no manager reason is inferred.
        os.unlink(os.path.join(self.folder, ".notes-state.json"))
        n = self.load()
        self.cycle(n, [])
        self.assertNotIn(81, carry_reasons(n, "ALFA"))

    def test_unknown_team_is_preserved_but_cannot_contribute_reason(self):
        self.save_text(markdown("unknown team reason", team="UNLISTED"))
        n = load_report_notes(self.root, self.folder, {"UNLISTED": NEW}, [OLD, NEW], self.warnings.append)
        reconcile_notes(n, {"UNLISTED": actions(row())["ALFA"]}, {"UNLISTED": NEW}, TODAY)
        write_report_notes(self.folder, n)
        n = self.load()
        self.assertFalse(n.items)
        self.assertFalse(carry_reasons(n, "UNLISTED"))
        self.assertIn("unknown team reason", self.read())

    def test_person_answer_survives_tool_owned_problem_edits(self):
        rows = [row(None, badge="No work", kinds=["none"], problem="No work")]
        self.cycle(self.load(), rows)
        self.save_text(self.read().replace("| No work: No work |  |", "| No work: No work | lent to spike |"))
        self.cycle(self.load(), rows)
        self.save_text(self.read().replace("No work |", "Edited problem |"))
        self.assertTrue(self.cycle(self.load(), rows)[0]["handled"])

    def test_second_reconcile_keeps_structured_badge_with_unrecognizable_prose(self):
        for answer, answered in [("", ""), ("keep this answer", "invalid metadata")]:
            with self.subTest(answer=answer):
                n = parse_notes(markdown(answer, answered, item="–", problem="No work: previous wording"))
                original = actions(row(None, badge="No work", kinds=["none"], problem="Awaiting allocation"))
                reconcile_notes(n, original, self.selected, TODAY)
                saved = n.teams["ALFA"]["Actions"][0]
                self.assertEqual(saved["answered"], "")
                refreshed = actions(row(None, badge="No work", kinds=["none"], problem="Awaiting allocation"))
                out = reconcile_notes(n, refreshed, self.selected, TODAY)
                self.assertEqual(saved["badge"], "No work")
                self.assertEqual(out["ALFA"]["rows"][0]["answer"], answer)
                self.assertFalse(n.teams["ALFA"]["Resolved"])
                self.assertEqual(set(n.state["ALFA"]), {'["Ada Example", "No work"]'})

    def test_person_binding_uses_badge_metadata_before_misleading_problem(self):
        for badge, kind, misleading in [
            ("No work", "none", "Too much WIP: 4 items in progress"),
            ("Too much WIP", "wip", "No work: no open item"),
        ]:
            with self.subTest(badge=badge):
                n = parse_notes(
                    markdown("same answer", "2026-11-16 amber " + badge, item="–", problem=misleading)
                )
                out = self.cycle(
                    n,
                    [
                        row(
                            None,
                            badge=badge,
                            kinds=[kind],
                            problem="Reworded by the tool",
                            sprint_path=NEW["path"],
                        )
                    ],
                )[0]
                self.assertEqual(out["answer"], "same answer")
                self.assertTrue(out["handled"])
                self.assertFalse(n.teams["ALFA"]["Resolved"])

    def test_pending_person_badges_survive_reorder_deleted_item_and_missing_sidecar(self):
        for change in ("reorder", "delete item", "missing sidecar"):
            with self.subTest(change=change):
                folder = os.path.join(self.root, change)
                self.folder = folder
                rows = [
                    row(),
                    row(
                        None,
                        badge="Nothing delivered",
                        kinds=["nothing"],
                        problem="delivered nothing, 8 available days",
                    ),
                    row(
                        None,
                        badge="Too much WIP",
                        kinds=["wip"],
                        problem="4 items in progress: #81, #82, #83, #84",
                    ),
                ]
                self.cycle(parse_notes(""), rows)
                lines = self.read().splitlines()
                item_at = next(i for i, line in enumerate(lines) if "| #81 " in line)
                person_at = [i for i, line in enumerate(lines) if "| – |" in line]
                if change == "reorder":
                    a, b = person_at
                    lines[a], lines[b] = lines[b], lines[a]
                elif change == "delete item":
                    del lines[item_at]
                else:
                    os.unlink(os.path.join(folder, ".notes-state.json"))
                wip_at = next(i for i, line in enumerate(lines) if "4 items in progress" in line)
                lines[wip_at] = lines[wip_at].replace(" |  |  |", " | wip answer |  |")
                self.save_text("\n".join(lines) + "\n")
                out = self.cycle(self.load(), rows)
                self.assertEqual(
                    {r["badge"]: r["answer"] for r in out if r["id"] is None},
                    {"Nothing delivered": "", "Too much WIP": "wip answer"},
                )
                self.assertEqual([r["handled"] for r in out], [False, False, True])
                state = json.loads(self.read(".notes-state.json"))["teams"]["ALFA"]
                self.assertEqual(
                    set(state),
                    {"i:81", '["Ada Example", "Nothing delivered"]', '["Ada Example", "Too much WIP"]'},
                )
                self.assertTrue(all("position" not in m and "section" not in m for m in state.values()))
                self.assertFalse(self.load().teams["ALFA"]["Resolved"])

    def test_person_prefix_is_written_only_in_notes_actions_and_resolved(self):
        rows = [row(None, badge="No work", kinds=["none"], problem="no open item, 4 available days left")]
        out = self.cycle(self.load(), rows)
        self.assertEqual(out[0]["problem"], "no open item, 4 available days left")
        self.assertIn("| No work: no open item, 4 available days left |", self.read())
        self.save_text(self.read().replace(" |  |  |", " | lent to spike |  |"))
        self.cycle(self.load(), rows)
        self.cycle(self.load(), [])
        saved = self.load().teams["ALFA"]["Resolved"][0]
        self.assertEqual(saved["problem"], "No work: no open item, 4 available days left")
        self.assertNotIn("No work: No work:", self.read())
        self.assertEqual(saved["answer"], "lent to spike")

    def test_person_prefix_wins_over_prose_without_item_state_binding(self):
        n = parse_notes(
            markdown("person answer", item="–", problem="Too much WIP: delivered nothing, no open item")
        )
        n.state["ALFA"] = {
            "i:81": dict(
                person="Ada Example", badge="Waiting", answer="person answer", section="Actions", position=0
            )
        }
        out = self.cycle(
            n,
            [
                row(
                    None,
                    badge="Too much WIP",
                    kinds=["wip"],
                    problem="4 items in progress: #81, #82, #83, #84",
                )
            ],
        )[0]
        self.assertEqual(out["answer"], "person answer")
        self.assertEqual(set(n.state["ALFA"]), {"i:81", '["Ada Example", "Too much WIP"]'})

    def test_old_position_hints_are_removed_on_load(self):
        self.save_text(markdown("legacy answer", item="–", problem="No work: no open item"))
        with open(os.path.join(self.folder, ".notes-state.json"), "w") as f:
            json.dump(
                {
                    "version": 1,
                    "teams": {
                        "ALFA": {
                            "i:81": dict(
                                person="Ada Example",
                                badge="Waiting",
                                answer="legacy answer",
                                section="Actions",
                                position=0,
                            )
                        }
                    },
                },
                f,
            )
        n = self.load()
        self.cycle(n, [row(None, badge="No work", kinds=["none"], problem="no open item")])
        state = json.loads(self.read(".notes-state.json"))["teams"]["ALFA"]
        self.assertTrue(all("position" not in m and "section" not in m for m in state.values()))
        self.assertEqual(set(state), {"i:81", '["Ada Example", "No work"]'})

    def test_reordered_person_rows_bind_by_badge_in_answered_metadata(self):
        rows = [
            row(None, badge="No work", kinds=["none"], problem="No work", answer="first answer"),
            row(None, badge="Too much WIP", kinds=["wip"], problem="Too much WIP", answer="second answer"),
        ]
        n = self.load()
        n.teams["ALFA"]["Actions"] = [
            dict(r, work="–", answered="2026-11-16 amber " + r["badge"]) for r in rows
        ]
        self.cycle(n, rows)
        lines = self.read().splitlines()
        positions = [
            i for i, line in enumerate(lines) if "| first answer |" in line or "| second answer |" in line
        ]
        lines[positions[0]], lines[positions[1]] = lines[positions[1]], lines[positions[0]]
        self.save_text("\n".join(lines) + "\n")
        out = self.cycle(self.load(), rows)
        self.assertEqual([r["answer"] for r in out], ["first answer", "second answer"])

    def test_old_person_wording_answers_bind_to_new_wording_without_state(self):
        cases = [
            (
                "No work",
                ["none"],
                "Ada Example had 8 available days but no item assigned during the sprint.",
                "no item assigned during the sprint, 8 available days",
            ),
            (
                "Nothing delivered",
                ["nothing"],
                "Ada Example had 8 available days and delivered nothing.",
                "delivered nothing, 8 available days",
            ),
            (
                "Too much WIP",
                ["wip"],
                "Ada Example has 4 items in progress: #81, #82, #83, #84.",
                "4 items in progress: #81, #82, #83, #84",
            ),
        ]
        for badge, kinds, old, new in cases:
            with self.subTest(badge=badge):
                n = parse_notes(markdown("legacy answer", item="–", problem=old))
                out = self.cycle(n, [row(None, badge=badge, kinds=kinds, problem=new)])[0]
                self.assertEqual(out["answer"], "legacy answer")
                self.assertTrue(out["handled"])
                self.assertEqual(n.teams["ALFA"]["Actions"][0]["problem"], new)

    def test_malformed_state_shapes_do_not_crash_or_lose_text(self):
        self.save_text(markdown("chased: promise", "2026-11-10 amber Waiting"))
        for state in (
            [],
            {"version": 1, "teams": []},
            {
                "version": 1,
                "teams": {
                    "ALFA": {
                        "i:81": {
                            "answer": "chased: promise",
                            "date": "2026-11-10",
                            "level": "amber",
                            "kinds": 5,
                        }
                    }
                },
            },
        ):
            with self.subTest(state=state):
                with open(os.path.join(self.folder, ".notes-state.json"), "w") as f:
                    json.dump(state, f)
                self.assertEqual(self.cycle(self.load())[0]["answer"], "chased: promise")
        self.assertTrue(self.warnings)

    def test_task_one_person_wording_without_state(self):
        cases = [
            ("No work", ["none"], "Ada Example had 8 available days but no item assigned during the sprint."),
            ("Nothing delivered", ["nothing"], "Ada Example had 8 available days and delivered nothing."),
            ("Too much WIP", ["wip"], "Ada Example has 4 items in progress: #81, #82, #83, #84."),
        ]
        for badge, kinds, problem in cases:
            with self.subTest(badge=badge):
                n = parse_notes(markdown("lent to spike", item="–", problem=problem))
                result = reconcile_notes(
                    n, actions(row(None, badge=badge, kinds=kinds, problem=problem)), self.selected, TODAY
                )
                self.assertEqual(result["ALFA"]["rows"][0]["answer"], "lent to spike")
                self.assertTrue(result["ALFA"]["rows"][0]["handled"])

    def test_current_unresolvable_marker_warns_preserves_text_and_does_not_grant_reason(self):
        self.save_text(markdown("imported reason", marker="unknown/path"))
        n = self.load()
        self.assertTrue(any("unknown/path" in w for w in self.warnings))
        self.assertEqual(n.note["ALFA"], "Private text")
        self.cycle(n)
        self.assertNotIn(81, carry_reasons(n, "ALFA"))

    def test_unknown_and_malformed_content_roundtrip_without_reconciliation(self):
        text = "# ALFA\n## Note\ntext  \n\nnext\n## Mystery\nline | raw\\x\n## Actions\n" + HEADER
        text += "| malformed |\n| Ada Example | invalid reference | raw | keep | data |\n"
        n = parse_notes(text)
        write_report_notes(self.folder, n)
        first = self.read()
        write_report_notes(self.folder, parse_notes(first))
        self.assertEqual(first, self.read())
        self.assertEqual(n.note["ALFA"], "text  \n\nnext")
        self.assertIn("| Ada Example | invalid reference | raw | keep | data |", first)

    def test_clear_then_reenter_same_copied_answer_renews_reason(self):
        self.save_text(markdown("split", marker=OLD["path"]), os.path.join(self.root, "old"))
        n = self.load()
        self.assertTrue(self.cycle(n)[0]["worse"])
        self.save_text(self.read().replace("split", ""))
        self.assertFalse(self.cycle(self.load())[0]["handled"])
        self.save_text(self.read().replace("| Waiting for a slot |  |", "| Waiting for a slot | split |"))
        n = self.load()
        self.assertTrue(self.cycle(n)[0]["handled"])
        self.assertEqual(carry_reasons(n, "ALFA").get(81, ""), "split")

    def test_unmatched_prior_state_is_retained(self):
        self.save_text(markdown("answer"))
        self.cycle(self.load())
        n = self.load()
        n.state["ALFA"]["i:999"] = {"answer": "unmatched preserved"}
        self.cycle(n, [])
        self.assertEqual(self.load().state["ALFA"]["i:999"]["answer"], "unmatched preserved")

    def test_malformed_separator_is_preserved_and_padded_empty_answer_is_unanswered(self):
        text = markdown().replace("## Resolved", "|---|---|\n## Resolved")
        text = text.replace("| Waiting for a slot |  |", "| Waiting for a slot |        |")
        self.save_text(text)
        out = self.cycle(self.load())[0]
        with self.subTest("blank answer"):
            self.assertEqual(out["answer"], "")
            self.assertFalse(out["handled"])
        with self.subTest("malformed separator"):
            self.assertIn("|---|---|", self.read().splitlines())
            self.assertTrue(any("|---|---|" in warning for warning in self.warnings))

    def test_interrupted_write_keeps_baseline_when_retrying_on_a_later_day(self):
        self.save_text(markdown("chased: first"))
        self.cycle(self.load())
        self.save_text(self.read().replace("chased: first", "chased: revised"))
        original = os.replace

        def fail_notes(src, dst):
            if dst.endswith("notes.md"):
                raise OSError("invented notes interruption")
            return original(src, dst)

        with patch("ledger.config.os.replace", side_effect=fail_notes), self.assertRaises(OSError):
            self.cycle(self.load(), [row()], date(2026, 11, 17))
        out = self.cycle(self.load(), [row(level="red")], date(2026, 11, 18))[0]
        self.assertEqual(out["answered"], "2026-11-17 amber Chase, Waiting")
        self.assertTrue(out["worse"])
        self.assertFalse(out["handled"])

    def test_import_without_state_uses_marker_scope_conservatively(self):
        n = parse_notes(markdown("chased: old promise", "2026-11-10 amber Chase, Waiting"))
        out = self.cycle(n)[0]
        self.assertEqual(out["answered"], "2026-11-10 amber Chase, Waiting")
        self.assertTrue(out["worse"])
        self.assertFalse(out["handled"])

    def test_missing_closed_sprint_does_not_import_later_report_into_history(self):
        future_folder = os.path.join(self.root, "future")
        future = parse_notes(markdown("future answer"))
        reconcile_notes(future, actions(row()), self.selected, TODAY)
        write_report_notes(future_folder, future)
        self.save_text(
            markdown("historical beta", team="BETA", marker=OLD["path"]), os.path.join(self.root, "older")
        )
        historical = self.load(selected={"ALFA": None, "BETA": OLD})
        self.assertEqual(historical.summary["BETA"], "Delivered a fixture.")
        with self.subTest("sections"):
            for heading in (
                "Summary",
                "Exec note",
                "PO satisfaction",
                "Note",
                "Status",
                "Actions",
                "Resolved",
            ):
                self.assertFalse(historical.teams["ALFA"][heading])
        with self.subTest("state"):
            self.assertFalse(historical.state.get("ALFA"))
        with self.subTest("marker"):
            self.assertNotIn("ALFA", historical.markers)
        write_report_notes(self.folder, historical)
        self.assertNotIn("future answer", self.read())
        self.assertNotIn("ALFA=Project/Two", self.read())

    def test_answered_display_labels_roundtrip_all_distinct_signal_kinds(self):
        labels = {
            "old": "Chase",
            "waiting": "Waiting",
            "stale": "No movement",
            "none": "No work",
            "nothing": "Nothing delivered",
            "wip": "Too much WIP",
            "never_active": "Never active",
            "reopened": "Reopened",
            "shared": "Shared",
            "carry": "Carry-over",
            "held": "On Hold",
            "not_started": "Not started",
        }
        self.save_text(markdown("chased: promise"))
        out = self.cycle(self.load(), [row(kinds=sorted(labels))])[0]
        self.assertEqual(out["answered"], "2026-11-16 amber " + ", ".join(labels[k] for k in sorted(labels)))
        self.assertEqual(len(set(labels.values())), len(labels))
        os.unlink(os.path.join(self.folder, ".notes-state.json"))
        n = self.load()
        out = self.cycle(n, [row(kinds=sorted(labels), sprint_path=NEW["path"], carry=False)])[0]
        self.assertTrue(out["handled"])
        self.assertEqual(n.state["ALFA"]["i:81"]["kinds"], sorted(labels))
        self.assertEqual(out["kinds"], sorted(labels))
        self.assertEqual(self.load().state["ALFA"]["i:81"]["kinds"], sorted(labels))
        # The example's label order parses to the same complete token set.
        n = parse_notes(markdown("answer", "2026-11-23 amber Waiting, Chase"))
        out = self.cycle(n, [row(sprint_path=NEW["path"], carry=False)])[0]
        self.assertTrue(out["handled"])
        self.assertEqual(n.state["ALFA"]["i:81"]["kinds"], ["old", "waiting"])

    def test_changed_sprint_keeps_person_answer_when_same_badge_is_present(self):
        prior_folder = os.path.join(self.root, "old")
        old = parse_notes(markdown("lent to spike", item="–", problem="No work", marker=OLD["path"]))
        person_row = row(
            None, badge="No work", kinds=["none"], problem="No work", open=False, sprint_path=OLD["path"]
        )
        reconcile_notes(old, actions(person_row), {"ALFA": OLD}, date(2026, 11, 2))
        write_report_notes(prior_folder, old)
        rows = self.cycle(
            self.load(),
            [dict(person_row, sprint_path=RUN), row(None, badge="Too much WIP", kinds=["wip"], open=False)],
        )
        self.assertEqual(rows[1]["answer"], "")
        out = rows[0]
        self.assertEqual(out["answer"], "lent to spike")
        self.assertTrue(out["worse"])
        self.assertFalse(out["handled"])
        out = self.cycle(self.load(), [dict(person_row, sprint_path=RUN)])[0]
        self.assertEqual(out["answer"], "lent to spike")
        self.assertFalse(out["handled"])

    def test_reused_folder_changed_marker_keeps_all_dm_text_and_old_answers(self):
        for with_state in (False, True):
            with self.subTest(with_state=with_state):
                folder = os.path.join(self.root, "reuse-" + str(with_state))
                text = markdown("agreed: keep old agreement", marker=OLD["path"])
                text = text.replace("## Status\n\n", "## Status\nAMBER - keep reason\n")
                text = text.replace(
                    "## Resolved",
                    "| Ada Example | – | No work | keep person answer | 2026-11-02 red No work |\n"
                    "| Ada Example | #83 Old action | Waiting | keep unmatched answer | 2026-11-02 amber Waiting |\n"
                    "## Resolved",
                )
                text += "| Ada Example | #82 Resolved task | Gone | agreed: keep resolved | 2026-11-02 amber Carry-over |\n"
                self.save_text(text, folder)
                if with_state:
                    old = self.load(folder, {"ALFA": OLD})
                    reconcile_notes(old, actions(row()), {"ALFA": OLD}, date(2026, 11, 2))
                    write_report_notes(folder, old)
                n = self.load(folder)
                expected = {
                    "Summary": "- Delivered a fixture.\n",
                    "Exec note": "External text\n",
                    "Note": "Private text\n",
                    "Status": "AMBER - keep reason\n",
                }
                for heading, value in expected.items():
                    self.assertEqual(n.teams["ALFA"][heading], value)
                result = reconcile_notes(n, actions(row()), self.selected, TODAY)
                write_report_notes(folder, n)
                self.assertEqual(result["ALFA"]["rows"][0]["answer"], "agreed: keep old agreement")
                self.assertFalse(result["ALFA"]["rows"][0]["handled"])
                self.assertNotIn(81, carry_reasons(n, "ALFA"))
                self.assertNotIn(82, carry_reasons(n, "ALFA"))
                self.assertEqual(
                    {r["answer"] for r in n.teams["ALFA"]["Resolved"]},
                    {"agreed: keep resolved", "keep person answer", "keep unmatched answer"},
                )
                again = self.load(folder)
                reconcile_notes(again, actions(), self.selected, TODAY)
                write_report_notes(folder, again)
                for heading, value in expected.items():
                    self.assertEqual(again.teams["ALFA"][heading], value)
                self.assertEqual(
                    {r["answer"] for r in again.teams["ALFA"]["Resolved"]},
                    {
                        "agreed: keep old agreement",
                        "agreed: keep resolved",
                        "keep person answer",
                        "keep unmatched answer",
                    },
                )

    def test_single_backslash_before_plain_character_stays_single_in_markdown(self):
        self.save_text(markdown(r"chased: C:\temp"))
        self.cycle(self.load())
        self.assertIn(r"| chased: C:\temp |", self.read())
        first = self.read()
        self.cycle(self.load())
        self.assertEqual(self.read(), first)

    def test_serialization_failure_leaves_both_files_untouched(self):
        self.save_text(markdown("answer"))
        n = self.load()
        self.cycle(n)
        before = self.read(), self.read(".notes-state.json")
        with (
            patch("ledger.config.json.dumps", side_effect=TypeError("invented invalid state")),
            self.assertRaises(TypeError),
        ):
            write_report_notes(self.folder, n)
        self.assertEqual((self.read(), self.read(".notes-state.json")), before)

    def test_po_satisfaction_parsing_valid_and_invalid(self):
        valid = [
            ("4", (4, "")),
            ("4/5", (4, "")),
            (" 4 / 5 ", (4, "")),
            ("5 - Smooth release", (5, "Smooth release")),
            ("4/5 - Smooth release, minor UAT delay", (4, "Smooth release, minor UAT delay")),
            ("3 – Mixed feedback", (3, "Mixed feedback")),
            ("2 — Delays in QA", (2, "Delays in QA")),
            ("1 - ", (1, "")),
        ]
        for raw, expected in valid:
            with self.subTest(raw=raw):
                text = markdown().replace("## Note\n", f"## PO satisfaction\n{raw}\n## Note\n")
                n = parse_notes(text)
                self.assertEqual(n.po_satisfaction["ALFA"], expected)
                self.assertFalse(n.warnings)
        blank = parse_notes(markdown().replace("## Note\n", "## PO satisfaction\n   \n## Note\n"))
        self.assertNotIn("ALFA", blank.po_satisfaction)
        self.assertFalse(blank.warnings)
        for bad in ("0", "6", "3.5", "4/10", "good", "4/5 - line one\nline two"):
            with self.subTest(bad=bad):
                text = markdown().replace("## Note\n", f"## PO satisfaction\n{bad}\n## Note\n")
                n = parse_notes(text)
                self.assertNotIn("ALFA", n.po_satisfaction)
                self.assertTrue(any("invalid PO satisfaction" in w for w in n.warnings))
                self.assertEqual(n.teams["ALFA"]["PO satisfaction"].strip(), bad)
                write_report_notes(self.folder, n)
                self.assertIn(bad, self.read())

    def test_po_satisfaction_carry_forward_and_reused_folder_clearing(self):
        def po_md(value, marker=OLD["path"], team="ALFA"):
            return markdown(marker=marker, team=team).replace(
                "## Note\n", f"## PO satisfaction\n{value}\n## Note\n"
            )

        # New folder, unchanged sprint -> preserved.
        self.save_text(po_md("4/5 - Good", marker=NEW["path"]), os.path.join(self.root, "same"))
        n = self.load(os.path.join(self.root, "new_same"))
        self.assertEqual(n.po_satisfaction.get("ALFA"), (4, "Good"))

        # New folder, changed sprint -> cleared.
        self.save_text(po_md("5/5 - Great", marker=OLD["path"]), os.path.join(self.root, "only_old"))
        n = load_report_notes(
            os.path.join(self.root, "only_old"),
            os.path.join(self.root, "only_old", "sub"),
            {"ALFA": NEW},
            [OLD, NEW],
            self.warnings.append,
        )
        self.assertNotIn("ALFA", n.po_satisfaction)

        # Reused folder: changed marker, missing marker, and unknown marker all clear with warning.
        for label, text in [
            ("changed", po_md("4/5 - Old sprint", marker=OLD["path"])),
            ("missing", po_md("4/5 - No marker", marker=OLD["path"]).split("\n", 1)[1]),
            ("unknown", po_md("4/5 - Bad marker", marker="unknown/path")),
        ]:
            with self.subTest(label=label):
                self.warnings.clear()
                folder = os.path.join(self.root, "reuse_po_" + label)
                self.save_text(text, folder)
                n = self.load(folder)
                self.assertNotIn("ALFA", n.po_satisfaction)
                self.assertEqual(n.teams["ALFA"]["PO satisfaction"], "")
                self.assertTrue(any("PO satisfaction cleared" in w for w in self.warnings))
                reconcile_notes(n, actions(), self.selected, TODAY)
                write_report_notes(folder, n)
                self.warnings.clear()
                again = self.load(folder)
                self.assertNotIn("ALFA", again.po_satisfaction)
                self.assertFalse(any("PO satisfaction cleared" in w for w in self.warnings))

        # Missing-sprint team preserves PO satisfaction untouched.
        folder = os.path.join(self.root, "missing_sprint_po")
        self.save_text(po_md("3/5 - Keep me", marker=OLD["path"]), folder)
        self.warnings.clear()
        n = self.load(folder, selected={"ALFA": None})
        self.assertEqual(n.po_satisfaction.get("ALFA"), (3, "Keep me"))
        self.assertFalse(any("PO satisfaction cleared" in w for w in self.warnings))

    def test_po_history_discovery_authoritative_and_predecessor_state(self):
        def po_md(value, marker, team="ALFA", answer="chased: ok"):
            return markdown(answer, marker=marker, team=team).replace(
                "## Note\n", f"## PO satisfaction\n{value}\n## Note\n"
            )

        reports = os.path.join(self.root, "reports")
        # Earlier folder for OLD has score 4; lexicographically later folder for OLD is blank -> overrides to None.
        f1 = os.path.join(reports, "01_old")
        f2 = os.path.join(reports, "02_old_cleared")
        f3 = os.path.join(reports, "03_beta_only")
        self.save_text(
            po_md("4/5 - Good", OLD["path"], "ALFA") + po_md("5/5 - Beta", OLD["path"], "BETA"), f1
        )
        # Write a bad .notes-state.json in f1 (unrelated predecessor when f2 is selected) to ensure
        # discovery uses lightweight parse_notes() and does not load unrelated sidecars.
        with open(os.path.join(f1, ".notes-state.json"), "w") as f:
            f.write("{corrupt")
        self.save_text(po_md("", OLD["path"], "ALFA"), f2)
        # Put valid predecessor state in f2 to verify selected predecessor sidecar still loads.
        old_n = load_report_notes(self.root, f2, {"ALFA": OLD}, [OLD, NEW], lambda _: None)
        reconcile_notes(old_n, actions(row(sprint_path=OLD["path"])), {"ALFA": OLD}, date(2026, 11, 2))
        write_report_notes(f2, old_n)
        self.save_text(po_md("2/5 - Beta two", NEW["path"], "BETA"), f3)

        dest = os.path.join(reports, "04_dest")
        self.warnings.clear()
        n = self.load(dest, selected={"ALFA": NEW, "BETA": NEW})
        # f2 (blank) overrode f1 (4) for ALFA on OLD; BETA on OLD stayed 5; BETA on NEW is 2.
        self.assertIsNone(n.po_history["ALFA"][OLD["path"]])
        self.assertEqual(n.po_history["BETA"][OLD["path"]], 5)
        self.assertEqual(n.po_history["BETA"][NEW["path"]], 2)
        # Unrelated f1 corrupt sidecar did not warn.
        self.assertFalse(any("malformed .notes-state.json" in w for w in self.warnings))
        # Selected predecessor f2's state loaded for ALFA.
        self.assertIn("i:81", n.state["ALFA"])

        # Survives reconcile_notes and rerun when destination exists.
        reconcile_notes(n, actions(row()), {"ALFA": NEW, "BETA": NEW}, TODAY)
        self.assertEqual(n.po_history["BETA"][OLD["path"]], 5)
        write_report_notes(dest, n)
        rerun = self.load(dest, selected={"ALFA": NEW, "BETA": NEW})
        self.assertIsNone(rerun.po_history["ALFA"][OLD["path"]])
        self.assertEqual(rerun.po_history["BETA"][OLD["path"]], 5)

    def test_legacy_agreed_state_and_po_agreement_label_migrate_safely(self):
        legacy_folder = os.path.join(self.root, "legacy")
        text = markdown("private note", "2026-11-16 amber PO agreement", marker=OLD["path"]) + markdown(
            "beta waiting", "2026-11-16 amber Waiting", item="#82 Beta task", team="BETA", marker=NEW["path"]
        )
        self.save_text(text, legacy_folder)
        state = {
            "version": 1,
            "teams": {
                "ALFA": {
                    "i:81": {
                        "date": "2026-11-16",
                        "level": "amber",
                        "kinds": ["agreed", "carry"],
                        "answer": "private note",
                        "closed": OLD["path"],
                        "sprint": RUN,
                        "carry": True,
                        "local": True,
                    }
                },
                "BETA": {
                    "i:82": {
                        "date": "2026-11-16",
                        "level": "amber",
                        "kinds": ["waiting"],
                        "answer": "beta waiting",
                        "closed": NEW["path"],
                        "sprint": RUN,
                        "carry": False,
                        "local": True,
                    }
                },
            },
        }
        with open(os.path.join(legacy_folder, ".notes-state.json"), "w", encoding="utf-8") as f:
            json.dump(state, f)
        # Predecessor inheritance from a legacy folder preserves unrelated teams and does not warn.
        self.warnings.clear()
        inherited = self.load(os.path.join(self.root, "next_folder"), selected={"ALFA": NEW, "BETA": NEW})
        self.assertFalse(any("malformed .notes-state.json" in w for w in self.warnings))
        self.assertFalse(any("migrated legacy carry-over state" in w for w in self.warnings))
        self.assertEqual(inherited.state["ALFA"]["i:81"]["kinds"], ["carry"])
        self.assertIn("i:82", inherited.state["BETA"])
        inh_out = reconcile_notes(
            inherited,
            {
                "ALFA": {"people": [], "rows": [row(kinds=["carry"], badge="Carry-over")], "current": None},
                "BETA": {
                    "people": [],
                    "rows": [row(82, kinds=["waiting"], badge="Waiting", carry=False)],
                    "current": None,
                },
            },
            {"ALFA": NEW, "BETA": NEW},
            TODAY,
        )
        self.assertTrue(inh_out["ALFA"]["rows"][0]["worse"])
        self.assertFalse(inh_out["ALFA"]["rows"][0]["handled"])
        self.assertTrue(inh_out["BETA"]["rows"][0]["handled"])
        self.assertNotIn(81, carry_reasons(inherited, "ALFA"))
        self.assertFalse(inherited.copied)
        self.assertFalse(inherited.unscoped)
        self.assertTrue({"_items", "_copied", "_unscoped"}.isdisjoint(inherited.teams["ALFA"]))
        # Existing folder rerun migrates legacy state in place without losing unrelated team state.
        self.warnings.clear()
        selected_old = {"ALFA": OLD, "BETA": NEW}
        n = self.load(legacy_folder, selected=selected_old)
        self.assertFalse(any("malformed .notes-state.json" in w for w in self.warnings))
        self.assertTrue(any("migrated legacy carry-over state" in w for w in self.warnings))
        self.assertEqual(n.state["ALFA"]["i:81"]["kinds"], ["carry"])
        self.assertFalse(n.state["ALFA"]["i:81"]["local"])
        self.assertTrue(n.state["BETA"]["i:82"]["local"])
        out = reconcile_notes(
            n,
            {
                "ALFA": {"people": [], "rows": [row(kinds=["carry"], badge="Carry-over")], "current": None},
                "BETA": {
                    "people": [],
                    "rows": [row(82, kinds=["waiting"], badge="Waiting", carry=False)],
                    "current": None,
                },
            },
            selected_old,
            TODAY,
        )
        write_report_notes(legacy_folder, n)
        alfa_row = out["ALFA"]["rows"][0]
        beta_row = out["BETA"]["rows"][0]
        self.assertEqual(alfa_row["answered"], "2026-11-16 amber Carry-over")
        self.assertEqual(alfa_row["answer"], "private note")
        self.assertFalse(alfa_row["worse"])
        self.assertFalse(alfa_row["handled"])
        self.assertTrue(beta_row["handled"])
        self.assertNotIn(81, carry_reasons(n, "ALFA"))
        # Second load does not repeat migration warning or rebase.
        self.warnings.clear()
        self.load(legacy_folder, selected=selected_old)
        self.assertFalse(any("migrated legacy carry-over state" in w for w in self.warnings))
        # Non-local legacy entry normalizes silently without a migration warning.
        non_local_folder = os.path.join(self.root, "legacy_non_local")
        self.save_text(
            markdown("copied note", "2026-11-16 amber PO agreement", marker=OLD["path"]), non_local_folder
        )
        non_local_state = {
            "version": 1,
            "teams": {
                "ALFA": {
                    "i:81": {
                        "date": "2026-11-16",
                        "level": "amber",
                        "kinds": ["agreed", "carry"],
                        "answer": "copied note",
                        "closed": OLD["path"],
                        "sprint": RUN,
                        "carry": True,
                        "local": False,
                    }
                }
            },
        }
        with open(os.path.join(non_local_folder, ".notes-state.json"), "w", encoding="utf-8") as f:
            json.dump(non_local_state, f)
        self.warnings.clear()
        nl = self.load(non_local_folder, selected={"ALFA": OLD})
        self.assertFalse(any("migrated legacy carry-over state" in w for w in self.warnings))
        self.assertEqual(nl.state["ALFA"]["i:81"]["kinds"], ["carry"])
        self.assertFalse(nl.state["ALFA"]["i:81"]["local"])
        # Deliberate edit publishes the carry-over reason and marks Handled.
        self.save_text(
            self.read(folder=legacy_folder).replace("private note", "confirmed API wait"), legacy_folder
        )
        edited = self.load(legacy_folder, selected=selected_old)
        res = reconcile_notes(
            edited,
            {"ALFA": {"people": [], "rows": [row(kinds=["carry"], badge="Carry-over")], "current": None}},
            selected_old,
            TODAY,
        )
        self.assertTrue(res["ALFA"]["rows"][0]["handled"])
        self.assertEqual(carry_reasons(edited, "ALFA").get(81), "confirmed API wait")
        # Standalone Markdown import without sidecar accepts legacy PO agreement label.
        md_only = parse_notes(markdown("agreed: split", "2026-11-16 amber PO agreement"))
        res_md = reconcile_notes(
            md_only,
            actions(row(kinds=["carry"], badge="Carry-over", sprint_path=NEW["path"])),
            self.selected,
            TODAY,
        )
        self.assertFalse(md_only.warnings)
        self.assertEqual(md_only.state["ALFA"]["i:81"]["kinds"], ["carry"])
        self.assertTrue(md_only.state["ALFA"]["i:81"]["carry"])
        self.assertFalse(res_md["ALFA"]["rows"][0]["handled"])
        self.assertNotIn(81, carry_reasons(md_only, "ALFA"))

    def test_non_local_carry_is_not_handled_and_none_closed_never_publishes(self):
        prior = os.path.join(self.root, "prior_carry")
        self.save_text(markdown("agreed: split"), prior)
        n = load_report_notes(self.root, prior, self.selected, [OLD, NEW], self.warnings.append)
        first = reconcile_notes(n, actions(row(kinds=["carry"], badge="Carry-over")), self.selected, TODAY)
        write_report_notes(prior, n)
        # Local plain text including legacy prefix is preserved verbatim as Reason.
        self.assertEqual(carry_reasons(n, "ALFA").get(81), "agreed: split")
        self.assertTrue(first["ALFA"]["rows"][0]["handled"])
        # Same-closed-sprint inheritance clears local -> not Handled and no Reason published.
        copied = self.load(os.path.join(self.root, "same_sprint_copy"))
        out = reconcile_notes(
            copied, actions(row(kinds=["carry"], badge="Carry-over")), self.selected, TODAY
        )["ALFA"]["rows"][0]
        self.assertFalse(out["worse"])
        self.assertFalse(out["handled"])
        self.assertNotIn(81, carry_reasons(copied, "ALFA"))
        # Missing closed sprint marker never matches meta["closed"] == None.
        markerless = parse_notes(markdown("should not publish"))
        markerless.markers.clear()
        markerless.state = {
            "ALFA": {
                "i:81": {
                    "date": "2026-11-16",
                    "level": "amber",
                    "kinds": ["carry"],
                    "answer": "should not publish",
                    "closed": None,
                    "carry": True,
                    "local": True,
                }
            }
        }
        reconcile_notes(markerless, actions(), {"ALFA": None}, TODAY)
        self.assertFalse(markerless.items)
        self.assertNotIn(81, carry_reasons(markerless, "ALFA"))


if __name__ == "__main__":
    unittest.main()
