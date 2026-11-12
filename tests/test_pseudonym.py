import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ledger.ado.pseudonym import (Pseudonymizer, load_or_create_key,
                                  normalise_text, ticket_text, to_revision,
                                  to_squad_revision)
from ledger.config import Person, Roster, Team
from ledger.errors import LedgerError
from ledger.home import Home

KEY = b"k" * 32
CUSTOMER = {"displayName": "Carl Customer", "uniqueName": "carl@customer.example",
            "id": "cid-1", "descriptor": "aad.xyz"}
ANNA = {"displayName": "Anna Vendor", "uniqueName": "anna@vendor.example",
        "id": "aid-1", "descriptor": "aad.abc"}


def roster():
    return Roster([Person("anna", "Anna Vendor", ("aid-1",),
                          ("anna@vendor.example", "Anna Vendor"), (), ())])


def pseud(key=KEY, services=("Build Service",)):
    return Pseudonymizer(key, roster(), services)


def team(column_field=None, done_field=None):
    return Team("Claims", "Claims Team", ("Demo\\Claims",), (),
                column_field, done_field)


def raw(**over):
    f = {"System.Rev": 3, "System.ChangedDate": "2026-09-01T10:00:00.5Z",
         "System.ChangedBy": CUSTOMER, "System.State": "Active",
         "System.Reason": "Moved", "System.IterationPath": "Demo\\S1",
         "System.AreaPath": "Demo\\Claims", "System.AssignedTo": ANNA,
         "System.WorkItemType": "User Story", "System.TeamProject": "Demo",
         "System.Parent": 7, "System.Tags": "a; b ;c",
         "System.Title": "T", "System.Description": "<div>Given x</div>",
         "Microsoft.VSTS.Common.AcceptanceCriteria": "<p>Then y</p>",
         "System.BoardColumn": "Doing", "System.BoardColumnDone": False}
    f.update(over)
    return {"id": 5, "rev": 3, "fields": f}


class ActorTests(unittest.TestCase):
    def test_roster_person_by_id_even_after_rename(self):
        ident = dict(ANNA, uniqueName="renamed@vendor.example",
                     displayName="Anna Renamed")
        self.assertEqual(pseud().actor(ident), "p:anna")

    def test_roster_person_by_alias(self):
        self.assertEqual(pseud().actor({"uniqueName": "ANNA@vendor.example"}),
                         "p:anna")

    def test_other_is_stable_and_not_reversible_without_key(self):
        a = pseud().actor(CUSTOMER)
        self.assertRegex(a, r"^o:[0-9a-f]{32}$")
        self.assertEqual(a, pseud().actor(CUSTOMER))
        self.assertNotEqual(a, pseud(key=b"z" * 32).actor(CUSTOMER))

    def test_service_identity(self):
        a = pseud().actor({"displayName": "build service",
                           "uniqueName": "svc@x", "id": "s1"})
        self.assertRegex(a, r"^s:[0-9a-f]{32}$")

    def test_none(self):
        self.assertIsNone(pseud().actor(None))

    def test_plain_string_identity(self):
        p = pseud()
        self.assertEqual(p.actor("Anna Vendor <anna@vendor.example>"), "p:anna")
        o = p.actor("Carl Customer <carl@customer.example>")
        self.assertRegex(o, r"^o:[0-9a-f]{32}$")
        self.assertEqual(o, p.actor({"displayName": "Carl Customer",
                                     "uniqueName": "carl@customer.example"}))
        self.assertEqual(p.actor("Carl Only"), p.actor("carl only"))
        self.assertRegex(p.actor("Build Service"), r"^s:")

    def test_id_and_name_inputs_never_collide(self):
        p = pseud()
        by_id = p.actor({"id": "x@y", "displayName": "A"})
        by_name = p.actor({"uniqueName": "x@y", "displayName": "A"})
        self.assertNotEqual(by_id, by_name)

    def test_collision_raises(self):
        p = pseud()
        a = p.actor(CUSTOMER)
        p._seen[a] = "id:something-else"
        with self.assertRaises(LedgerError):
            p.actor(CUSTOMER)


class KeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Home(Path(self.tmp.name) / "h")

    def test_create_then_reuse(self):
        k = load_or_create_key(self.home)
        self.assertEqual(len(k), 32)
        mode = stat.S_IMODE(os.stat(str(self.home.path("secret.key"))).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(str(self.home.root)).st_mode),
                         0o700)
        self.assertEqual(load_or_create_key(self.home), k)

    def test_loose_mode_existing_key_is_tightened(self):
        self.home.ensure()
        path = self.home.path("secret.key")
        path.write_bytes(b"k" * 32)
        os.chmod(str(path), 0o644)
        self.assertEqual(load_or_create_key(self.home), b"k" * 32)
        self.assertEqual(stat.S_IMODE(os.stat(str(path)).st_mode), 0o600)

    def test_wrong_length_raises(self):
        self.home.ensure()
        self.home.path("secret.key").write_bytes(b"short")
        with self.assertRaises(LedgerError) as cm:
            load_or_create_key(self.home)
        self.assertIn("original", cm.exception.hint)
        self.assertEqual(self.home.path("secret.key").read_bytes(), b"short")


class TextTests(unittest.TestCase):
    def test_normalise_text(self):
        self.assertEqual(
            normalise_text("<div>Given&nbsp;x<br/> then  y</div>"),
            "Given x then y")

    def test_none_and_block_tags(self):
        self.assertEqual(normalise_text(None), "")
        self.assertEqual(normalise_text("<p>a</p><p>b</p>"), "a b")
        self.assertEqual(normalise_text("a  b\n\tc"), "a b c")

    def test_entities_unescaped_once(self):
        self.assertEqual(normalise_text("a &amp;lt; b &lt;i&gt;"), "a &lt; b <i>")

    def test_style_and_script_bodies_ignored(self):
        self.assertEqual(
            normalise_text("a<style>p{color:red}</style>b<script>x()</script>"),
            "a b")

    def test_unclosed_style_keeps_following_text(self):
        self.assertIn("Given x then y",
                      normalise_text("<style>p{color:red}<div>Given x then y</div>"))

    def test_unclosed_style_tail_edit_changes_hash(self):
        def h(d):
            return to_revision(raw(**{"System.Description": d}),
                               pseud(), team()).desc_hash
        self.assertNotEqual(h("<style>p{}<div>Given x</div>"),
                            h("<style>p{}<div>Given z</div>"))

    def test_stray_closing_script_is_harmless(self):
        self.assertEqual(normalise_text("a</script>b<p>c</p>"), "a b c")

    def test_style_block_does_not_change_hash(self):
        def h(d):
            return to_revision(raw(**{"System.Description": d}),
                               pseud(), team())
        a = h("<div>Given x</div>")
        b = h("<style>p{margin:0}</style><div>Given x</div>")
        c = h("<style>p{margin:1px}</style><div>Given x</div>")
        self.assertEqual((a.desc_hash, a.desc_len), (b.desc_hash, b.desc_len))
        self.assertEqual(b.desc_hash, c.desc_hash)

    def test_more_block_tags_separate_words(self):
        self.assertEqual(normalise_text("<dl><dt>a</dt><dd>b</dd></dl>"), "a b")
        self.assertEqual(normalise_text("<section>a</section><footer>b</footer>"),
                         "a b")

    def test_formatting_only_edit_keeps_hash(self):
        a = to_revision(raw(**{"System.Description": "<div>Given x then y</div>"}),
                        pseud(), team())
        b = to_revision(raw(**{"System.Description":
                               "<p><b>Given</b>&nbsp;x<br/>then   y</p>"}),
                        pseud(), team())
        self.assertEqual(a.desc_hash, b.desc_hash)
        self.assertEqual(a.desc_len, len("Given x then y"))

    def test_ticket_text(self):
        t = ticket_text(raw())
        self.assertEqual(t, {"title": "T", "description": "Given x",
                             "acceptance": "Then y"})


class RevisionTests(unittest.TestCase):
    def test_fields(self):
        r = to_revision(raw(), pseud(), team())
        self.assertEqual(r.rev, 3)
        self.assertEqual(r.changed, datetime(2026, 9, 1, 10, 0, 0, 500000,
                                             tzinfo=timezone.utc))
        self.assertRegex(r.by, r"^o:")
        self.assertEqual(r.assignee, "p:anna")
        self.assertEqual((r.state, r.reason, r.iteration, r.area, r.type,
                          r.project, r.parent),
                         ("Active", "Moved", "Demo\\S1", "Demo\\Claims",
                          "User Story", "Demo", 7))
        self.assertEqual(r.tags, ("a", "b", "c"))
        self.assertEqual((r.column, r.column_done), ("Doing", False))
        self.assertEqual(len(r.desc_hash), 64)

    def test_timestamp_formats(self):
        for s, want in [
            ("2026-09-01T10:00:00Z", datetime(2026, 9, 1, 10, tzinfo=timezone.utc)),
            ("2026-09-01T10:00:00.123Z",
             datetime(2026, 9, 1, 10, 0, 0, 123000, tzinfo=timezone.utc)),
            ("2026-09-01T12:00:00+02:00", datetime(2026, 9, 1, 10, tzinfo=timezone.utc)),
            ("2026-09-01T12:00:00.1234567+02:00",
             datetime(2026, 9, 1, 10, 0, 0, 123456, tzinfo=timezone.utc)),
        ]:
            r = to_revision(raw(**{"System.ChangedDate": s}), pseud(), team())
            self.assertEqual(r.changed, want, s)
            self.assertEqual(r.changed.utcoffset().total_seconds(), 0)

    def test_tags_and_defaults(self):
        f = raw()["fields"]
        for k in ("System.Tags", "System.Parent", "System.AssignedTo",
                  "System.State", "System.BoardColumn",
                  "System.BoardColumnDone", "System.Description",
                  "Microsoft.VSTS.Common.AcceptanceCriteria"):
            del f[k]
        r = to_revision({"id": 5, "rev": 1, "fields": f}, pseud(), team())
        self.assertEqual(r.tags, ())
        self.assertIsNone(r.parent)
        self.assertIsNone(r.assignee)
        self.assertEqual(r.state, "")
        self.assertIsNone(r.column)
        self.assertIsNone(r.column_done)
        empty = to_revision(raw(**{"System.Description": ""}), pseud(), team())
        self.assertEqual(r.desc_hash, empty.desc_hash)
        self.assertEqual(r.desc_len, 0)

    def test_board_column_field_override(self):
        t = team("Custom.Col", "Custom.Done")
        r = to_revision(raw(**{"Custom.Col": "Review", "Custom.Done": 1}),
                        pseud(), t)
        self.assertEqual((r.column, r.column_done), ("Review", True))
        # configured field absent from payload: fall back to System.*
        r = to_revision(raw(), pseud(), t)
        self.assertEqual((r.column, r.column_done), ("Doing", False))

    def test_no_real_identity_in_output(self):
        r = to_revision(raw(), pseud(), team())
        out = json.dumps(r.to_json())
        for secret in ("Carl", "customer.example", "cid-1", "aad.xyz"):
            self.assertNotIn(secret, out)

    def test_squad_revision_has_no_identity_or_text(self):
        r = to_squad_revision(raw(), team())
        self.assertIsNone(r.by)
        self.assertIsNone(r.assignee)
        self.assertEqual((r.desc_hash, r.desc_len, r.ac_hash, r.ac_len),
                         ("", 0, "", 0))
        self.assertEqual(r.state, "Active")
        self.assertEqual(r.tags, ("a", "b", "c"))


if __name__ == "__main__":
    unittest.main()
