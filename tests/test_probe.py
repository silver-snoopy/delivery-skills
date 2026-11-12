import contextlib
import io
import json
import os
import re
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ledger.ado import probe
from ledger.ado.auth import AzTokenAuth
from ledger.ado.client import AdoClient
from ledger.ado.probe import run_probe
from ledger.ado.probe_export import (CHECK_IDS, DETAIL_CODES, STATUSES,
                                     export, export_markdown)
from ledger.ado.transport import Response
from ledger.cli import main
from ledger.errors import LedgerError
from ledger.home import Home
from ledger.setup_check import setup_check
from ledger.testing import fake_ado
from ledger.testing.builder import DEFAULT_PHASES, PortfolioBuilder
from ledger.testing.fake_ado import FakeAdo

SPRINTS = [("S37", "2026-10-26", "2026-11-06"),
           ("S38", "2026-11-09", "2026-11-20")]
ORG = "https://dev.azure.com/demo"


class _Proc:
    def __init__(self, stdout, returncode=0):
        self.stdout, self.returncode, self.stderr = stdout, returncode, ""


def az_auth():
    token = json.dumps({"accessToken": "tok", "expires_on": 1900000000})
    return AzTokenAuth(run=lambda argv, **kw: _Proc(token))


class Wrap:
    """A transport over FakeAdo carrying an auth object, with hooks."""

    def __init__(self, inner, auth=None, deny=None, extra_field=None):
        self.inner, self.auth = inner, auth
        self.deny, self.extra_field = deny, extra_field

    def request(self, method, url, body=None):
        if self.deny and self.deny in url:
            return Response(403, {}, {"message": "denied"})
        resp = self.inner.request(method, url, body)
        if self.extra_field and "/revisions" in url and resp.status == 200:
            for v in resp.body["value"]:
                v["fields"][self.extra_field] = "x"
        return resp


def portfolio(team="Claims", area="Demo\\Claims", phases=None,
              display="Anna K.", title="Claim intake", project=None,
              sprint="S38", ids=(1, 2, 3), wit="User Story", qa="QA",
              reason="Moved"):
    b = PortfolioBuilder(tz="Europe/Zurich")
    b.team(team, area=area, sprints=[SPRINTS[0], (sprint,) + SPRINTS[1][1:]],
           phases=phases)
    anna = b.person("anna", display, team=team, start="2026-01-05")
    qa_actor = b.other("qa")
    kw = {"project": project} if project else {}
    b.ticket(ids[0], team=team, type=wit, title=title, description="d",
             acceptance="a", parent=50).on(
        "2026-10-26 09:00", iteration="S37", state="Active", assignee=anna,
        column="Doing", **kw).on(
        "2026-11-02 09:00", state="In QA", assignee=qa_actor, column=qa).on(
        "2026-11-04 09:00", state="Closed", reason="Completed",
        column="Done")
    b.ticket(ids[1], team=team, type=wit, title="Lookup").on(
        "2026-10-27 09:00", state="Active", assignee=anna, column="Doing",
        **kw).on("2026-10-28 09:00", state="Closed", reason=reason,
                 column="Done")
    b.ticket(ids[2], team=team, type=wit, title="Search").on(
        "2026-11-10 09:00", state="Active", assignee=anna, **kw)
    return b


def clients(transport, org=ORG, project="Demo"):
    def client_for(kind):
        return AdoClient(transport, org, project)
    return client_for


def ticks():
    t = iter(range(1000))
    return lambda: float(next(t))


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.home = Home(Path(tempfile.mkdtemp()))

    def probe(self, b, transport=None, fake=None, team="Claims", **kw):
        fake = fake or FakeAdo(b)
        transport = transport or Wrap(fake, auth=az_auth())
        kw.setdefault("alias", fake.alias("p:anna"))
        kw.setdefault("timezone", "Europe/Zurich")
        return run_probe(self.home, clients(transport, fake.org_url,
                                            fake.project),
                         fake.org_url, fake.project, team, now=ticks(), **kw)

    def status(self, report):
        return {c["id"]: c["status"] for c in report["checks"]}

    def test_probe_all_observed_against_fake(self):
        report = self.probe(portfolio())
        self.assertEqual([c["id"] for c in report["checks"]], list(CHECK_IDS))
        self.assertEqual(set(self.status(report).values()), {"observed"},
                         [c for c in report["checks"]
                          if c["status"] != "observed"])
        self.assertEqual(report["counts"]["transport_urllib_ok"], True)
        self.assertEqual(report["timing"]["revision_fetches"], 3)
        self.assertEqual(report["timing"]["seconds"], 1.0)
        self.assertEqual(sorted(report["reasons"]), ["Completed", "Moved"])
        a5 = [c for c in report["checks"] if c["id"] == "A5"][0]
        self.assertEqual(a5["sample"], 3)
        for name in ("probe-report.json", "probe-report.md",
                     "probe-export.json", "probe-export.md"):
            p = self.home.path("probe", name)
            self.assertTrue(p.exists(), name)
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(self.home.read_json("probe", "probe-export.json"),
                         export(report))
        self.assertFalse([p for p in self.home.path("probe").iterdir()
                          if p.name.startswith(".")])

    def test_probe_uses_supplied_tickets(self):
        report = self.probe(portfolio(), alias=None, ticket_ids=(1,))
        st = self.status(report)
        self.assertEqual(st["A3"], "not checked")
        self.assertEqual(st["A4"], "not checked")
        self.assertEqual(st["A5"], "observed")
        a5 = [c for c in report["checks"] if c["id"] == "A5"][0]
        self.assertEqual(a5["sample"], 1)

    def test_probe_reports_failure_not_exception(self):
        fake = FakeAdo(portfolio())
        report = self.probe(None, fake=fake,
                            transport=Wrap(fake, auth=az_auth(),
                                           deny="/_apis/work/boards"))
        st = self.status(report)
        self.assertEqual(st["A11"], "failed")
        a11 = [c for c in report["checks"] if c["id"] == "A11"][0]
        self.assertIn("403", a11["detail"])
        self.assertEqual(a11["code"], "http_error")
        for cid in ("A12", "A13", "A14", "A7", "A10"):
            self.assertEqual(st[cid], "observed", cid)

    def test_unexpected_exception_becomes_failed_and_later_checks_run(self):
        with mock.patch.object(probe, "check_a7",
                               side_effect=RuntimeError("boom")):
            report = self.probe(portfolio())
        st = self.status(report)
        self.assertEqual(st["A7"], "failed")
        a7 = [c for c in report["checks"] if c["id"] == "A7"][0]
        self.assertEqual(a7["code"], "exception")
        self.assertIn("boom", a7["detail"])
        self.assertEqual(st["A8"], "observed")
        self.assertEqual(st["A14"], "observed")

    def test_no_transport_works_marks_ado_checks_not_checked(self):
        def client_for(kind):
            raise LedgerError("no transport")
        report = run_probe(self.home, client_for, ORG, "Demo", "Claims",
                           alias="x", timezone="UTC", now=ticks())
        st = self.status(report)
        self.assertEqual(st["A2"], "failed")
        self.assertEqual(st["ENV-python"], "observed")
        for cid in ("A3", "A5", "A7", "A11", "A13"):
            self.assertEqual(st[cid], "not checked", cid)
        self.assertEqual(st["A1"], "not checked")

    def test_bad_timezone_fails_env_and_a14(self):
        report = self.probe(portfolio(), timezone="Nowhere/ACME")
        st = self.status(report)
        self.assertEqual(st["ENV-timezone"], "failed")
        self.assertEqual(st["A14"], "failed")

    def test_export_drops_planted_secrets(self):
        s = "ACME-SECRET"
        phases = list(DEFAULT_PHASES) + [{"state": s + " Review",
                                          "phase": "handoff"}]
        b = portfolio(team=s + " Team", area="Demo\\" + s, phases=phases,
                      display=s + " Person", title=s + " title",
                      project=s + "-Proj", sprint=s + " S1",
                      ids=(987654, 987655, 987656), wit=s + " Story",
                      qa=s + " Column", reason=s + " Reason")
        fake = FakeAdo(b, project=s + "-Proj")
        fake.fail_next(500, body={"message": s + " exploded"})
        transport = Wrap(fake, auth=az_auth(), extra_field="Custom." + s)
        report = self.probe(None, fake=fake, transport=transport,
                            team=s + " Team",
                            alias=fake.identity("p:anna")["displayName"])
        local = json.dumps(report)
        self.assertIn(s + " exploded", local)
        self.assertIn(s + " Review", local)
        self.assertIn("Custom." + s, local)
        self.assertIn(s + " Reason", local)
        self.assertIn(s + " Story", local)
        self.assertEqual(self.status(report)["A5"], "observed")
        self.assertNotIn(s, json.dumps(export(report)))
        self.assertNotIn(s, export_markdown(report))
        self.assertNotIn(s, self.home.path(
            "probe", "probe-export.json").read_text())
        self.assertNotIn(s, self.home.path(
            "probe", "probe-export.md").read_text())
        self.assertIn("987654", local)
        for text in (json.dumps(export(report)), export_markdown(report)):
            for tid in ("987654", "987655", "987656"):
                self.assertIsNone(re.search(r"(?<!\d)%s(?!\d)" % tid, text))

    def test_export_only_allowlisted_keys(self):
        report = self.probe(portfolio())
        report["extra"] = "ACME"
        report["checks"].append({"id": "ACME", "status": "observed",
                                 "sample": 1, "detail": "x", "code": "ok"})
        report["checks"][0]["code"] = "ACME"
        report["timing"]["seconds"] = "ACME"
        report["counts"]["ACME"] = 5
        out = export(report)
        self.assertEqual(set(out), {"tool_version", "checks", "timing",
                                    "shapes", "categories", "counts"})
        self.assertEqual(len(out["checks"]), len(CHECK_IDS))
        for c in out["checks"]:
            self.assertEqual(set(c), {"id", "status", "sample", "detail"})
            self.assertIn(c["id"], CHECK_IDS)
            self.assertIn(c["status"], STATUSES)
            self.assertIn(c["detail"], DETAIL_CODES)
            self.assertIsInstance(c["sample"], int)
        self.assertEqual(out["checks"][0]["detail"], "unknown")
        self.assertEqual(out["timing"]["seconds"], 0.0)
        self.assertNotIn("ACME", out["counts"])
        self.assertNotIn("ACME", json.dumps(out))
        shapes = out["shapes"]["revisions"]
        self.assertIn("System.State", shapes["standard"])
        self.assertEqual(shapes["other_count"], 0)
        cats = {c["category"]: c["count"] for c in out["categories"]}
        self.assertEqual(cats["Completed"], 1)
        self.assertEqual(out["counts"]["transport_az_rest_ok"], True)

    def test_probe_command_without_config(self):
        fake = FakeAdo(portfolio())
        fake_ado.REGISTRY["probe-test"] = fake
        self.addCleanup(fake_ado.REGISTRY.pop, "probe-test", None)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ,
                             {"LEDGER_TEST_TRANSPORT": "probe-test"}), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = main(["--home", str(self.home.root), "probe", "--org", ORG,
                         "--project", "Demo", "--team", "Claims",
                         "--alias", fake.alias("p:anna")])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("observed", out.getvalue())
        report = self.home.read_json("probe", "probe-report.json")
        st = self.status(report)
        self.assertEqual(st["A2"], "observed")
        self.assertEqual(st["A1"], "not checked")


class ExportRobustnessTests(unittest.TestCase):
    def test_field_name_with_newline_not_exported(self):
        out = export({"shapes": {"revisions": ["System.Title\n",
                                               "System.State"]}})
        self.assertEqual(out["shapes"]["revisions"],
                         {"standard": ["System.State"], "other_count": 1})

    def test_non_finite_numbers_become_zero(self):
        bad = [float("inf"), float("nan"), float("-inf")]
        for v in bad:
            out = export({"checks": [{"id": "A1", "status": "observed",
                                      "sample": v, "code": "ok"}],
                          "timing": {"seconds": v, "revision_fetches": v},
                          "counts": {"wiql_ids": v}})
            self.assertEqual(out["checks"][0]["sample"], 0)
            self.assertEqual(out["timing"]["seconds"], 0.0)
            self.assertEqual(out["timing"]["revision_fetches"], 0)
            self.assertEqual(out["counts"]["wiql_ids"], 0)
            json.dumps(out, allow_nan=False)


class ProbeCommandConfigTests(unittest.TestCase):
    def setUp(self):
        self.home = Home(Path(tempfile.mkdtemp()))
        self.home.path("config.json").write_text("{broken")
        fake = FakeAdo(portfolio())
        fake_ado.REGISTRY["probe-cfg"] = fake
        self.addCleanup(fake_ado.REGISTRY.pop, "probe-cfg", None)

    def run_cli(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ,
                             {"LEDGER_TEST_TRANSPORT": "probe-cfg"}), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = main(["--home", str(self.home.root), "probe"] + list(extra))
        return code, out.getvalue(), err.getvalue()

    def test_malformed_config_with_arguments_continues(self):
        code, out, err = self.run_cli("--org", ORG, "--project", "Demo",
                                      "--team", "Claims")
        self.assertEqual(code, 0, err)
        report = self.home.read_json("probe", "probe-report.json")
        self.assertIn("config.json", report["config_note"])
        self.assertNotIn("config_note",
                         self.home.read_json("probe", "probe-export.json"))

    def test_malformed_config_without_arguments_is_error(self):
        code, out, err = self.run_cli("--org", ORG)
        self.assertEqual(code, 2)
        self.assertIn("--team", err)


class SetupCheckTests(unittest.TestCase):
    def setUp(self):
        self.home = Home(Path(tempfile.mkdtemp()))

    def test_setup_check_flags_unknown_state_and_identityless_person(self):
        b = portfolio()
        b.person("bence", "Bence T.", team="Claims", start="2026-01-05")
        fake = FakeAdo(b)
        self.home.write_json(b._config_json(), "config.json")
        roster = b._roster_json()
        roster["people"][0].update(fake.roster_patch()["anna"])
        self.home.write_json(roster, "roster.json")
        self.home.write_json({"states": {"User Story": [
            {"name": n, "category": "InProgress"}
            for n in ("New", "Active", "In QA", "Closed", "Removed")]}},
            "probe", "probe-report.json")
        r = setup_check(self.home)
        self.assertFalse(r["ok"])
        self.assertEqual(len(r["problems"]), 2, r["problems"])
        self.assertTrue(r["summary"].startswith("2 problem(s)"))
        joined = "\n".join(r["problems"])
        self.assertIn("Claims", joined)
        self.assertIn("In Review", joined)
        self.assertIn("bence", joined)

    def test_setup_check_ok(self):
        b = portfolio()
        fake = FakeAdo(b)
        self.home.write_json(b._config_json(), "config.json")
        roster = b._roster_json()
        roster["people"][0].update(fake.roster_patch()["anna"])
        self.home.write_json(roster, "roster.json")
        r = setup_check(self.home)
        self.assertTrue(r["ok"], r["problems"])
        self.assertTrue(r["summary"].startswith("OK"))

    def _good_home(self):
        b = portfolio()
        fake = FakeAdo(b)
        self.home.write_json(b._config_json(), "config.json")
        roster = b._roster_json()
        roster["people"][0].update(fake.roster_patch()["anna"])
        self.home.write_json(roster, "roster.json")

    def check_cli(self, name):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--home", str(self.home.root), "--json",
                         "setup-check"])
        self.assertEqual(code, 0)
        r = json.loads(out.getvalue())
        self.assertFalse(r["ok"])
        self.assertTrue(any(name in p for p in r["problems"]), r["problems"])

    def test_setup_check_invalid_json_config(self):
        self._good_home()
        self.home.path("config.json").write_text("{nope")
        self.check_cli("config.json")

    def test_setup_check_roster_is_a_list(self):
        self._good_home()
        self.home.write_json([1, 2], "roster.json")
        self.check_cli("roster.json")

    def test_setup_check_team_not_a_dict(self):
        self._good_home()
        cfg = self.home.read_json("config.json")
        cfg["teams"] = ["Claims"]
        self.home.write_json(cfg, "config.json")
        self.check_cli("config.json")

    def test_setup_check_corrupt_probe_report(self):
        self._good_home()
        self.home.path("probe").mkdir()
        self.home.path("probe", "probe-report.json").write_text("{x")
        self.check_cli("probe-report.json")

    def test_setup_check_reports_missing_config_as_problem(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--home", str(self.home.root), "setup-check"])
        self.assertEqual(code, 0)
        self.assertIn("2 problem(s)", out.getvalue())
        self.assertIn("config.json", out.getvalue())
        self.assertIn("roster.json", out.getvalue())


if __name__ == "__main__":
    unittest.main()
