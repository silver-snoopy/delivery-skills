import base64
import json
import os
import stat
import tempfile
import unittest
from unittest import mock

from ledger.theme import DEFAULT_THEME_REPORTS, KNOWN_THEME_REPORTS, Theme, load_theme


def _write(path: str, content: str | bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    if isinstance(content, bytes):
        with open(path, mode) as f:
            f.write(content)
    else:
        with open(path, mode, encoding="utf-8") as f:
            f.write(content)
    return path


class ThemeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.work = os.path.join(self.root, "work")
        self.cwd = os.path.join(self.root, "cwd")
        os.makedirs(self.work)
        os.makedirs(self.cwd)

    def test_constants_and_empty_path(self):
        self.assertEqual(KNOWN_THEME_REPORTS, ("manager", "dm", "person"))
        self.assertEqual(DEFAULT_THEME_REPORTS, ("manager",))
        warnings: list[str] = []
        self.assertIsNone(load_theme("", self.work, self.work, warnings.append))
        self.assertEqual(warnings, [])

    def test_direct_css_file_and_case_insensitive_extension(self):
        css_path = _write(os.path.join(self.root, "brand.CSS"), ":root { --accent: #123456; }")
        _write(os.path.join(self.work, "theme", "theme.css"), "body { color: red; }")
        _write(
            os.path.join(self.work, "theme", "theme.json"),
            json.dumps({"source": os.path.realpath(css_path), "header_note": "Ignored"}),
        )
        warnings: list[str] = []
        theme = load_theme(css_path, self.work, self.work, warnings.append)
        self.assertEqual(theme, Theme(css=":root { --accent: #123456; }"))
        self.assertEqual(warnings, [])

    def test_direct_folder_full_pack_with_logos_and_json_only(self):
        pack = os.path.join(self.root, "acme")
        svg_bytes = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
        _write(os.path.join(pack, "theme.css"), "body.report-manager { --accent: #0055aa; }")
        _write(os.path.join(pack, "assets", "logo.SVG"), svg_bytes)
        _write(
            os.path.join(pack, "theme.json"),
            json.dumps(
                {
                    "logo": "assets/logo.SVG",
                    "logo_alt": "Acme Corp",
                    "header_note": "Confidential",
                    "footer_note": "Prepared by DM",
                }
            ),
        )
        warnings: list[str] = []
        theme = load_theme(pack, self.work, self.work, warnings.append)
        self.assertEqual(warnings, [])
        assert theme is not None
        self.assertEqual(theme.css, "body.report-manager { --accent: #0055aa; }")
        self.assertEqual(
            theme.logo_uri,
            f"data:image/svg+xml;base64,{base64.b64encode(svg_bytes).decode('ascii')}",
        )
        self.assertEqual(theme.logo_alt, "Acme Corp")
        self.assertEqual(theme.header_note, "Confidential")
        self.assertEqual(theme.footer_note, "Prepared by DM")

        # theme.json only (no theme.css), PNG logo
        json_only = os.path.join(self.root, "json-only")
        png_bytes = b"\x89PNG\r\n\x1a\nfake"
        _write(os.path.join(json_only, "mark.png"), png_bytes)
        _write(os.path.join(json_only, "theme.json"), json.dumps({"logo": "mark.png", "header_note": "Hi"}))
        theme2 = load_theme(json_only, self.work, self.work, warnings.append)
        assert theme2 is not None
        self.assertEqual(theme2.css, "")
        self.assertTrue(theme2.logo_uri.startswith("data:image/png;base64,"))
        self.assertEqual(theme2.header_note, "Hi")

        # theme.css only (no theme.json), ready-made folder ignores <work_dir>/theme/
        css_only = os.path.join(self.root, "css-only")
        _write(os.path.join(css_only, "theme.css"), ":root { --bg: #fff; }")
        _write(
            os.path.join(self.work, "theme", "theme.json"),
            json.dumps({"source": os.path.realpath(css_only), "footer_note": "Should not load"}),
        )
        theme3 = load_theme(css_only, self.work, self.work, warnings.append)
        self.assertEqual(theme3, Theme(css=":root { --bg: #fff; }"))

    def test_base_dir_vs_work_dir_and_tilde_expansion(self):
        _write(os.path.join(self.work, "rel.css"), ":root { --accent: #111; }")
        _write(os.path.join(self.cwd, "rel.css"), ":root { --accent: #222; }")
        from_work = load_theme("rel.css", self.work, self.work)
        from_cwd = load_theme("rel.css", self.cwd, self.work)
        assert from_work is not None and from_cwd is not None
        self.assertEqual(from_work.css, ":root { --accent: #111; }")
        self.assertEqual(from_cwd.css, ":root { --accent: #222; }")

        home = os.path.join(self.root, "home")
        _write(os.path.join(home, "my.css"), ":root { --accent: #333; }")
        with mock.patch.dict(os.environ, {"HOME": home}):
            from_home = load_theme("~/my.css", self.work, self.work)
        assert from_home is not None
        self.assertEqual(from_home.css, ":root { --accent: #333; }")

    def test_raw_assets_prepared_snapshot_matching_and_fallbacks(self):
        guide = _write(os.path.join(self.root, "brand", "style-guide.md"), "# Brand\nBlue accent.")
        brand_dir = os.path.dirname(guide)
        raw_link = os.path.join(self.root, "brand-link")
        os.symlink(brand_dir, raw_link)

        # Missing prepared theme -> warn & return None
        warnings: list[str] = []
        self.assertIsNone(load_theme(guide, self.work, self.work, warnings.append))
        self.assertEqual(len(warnings), 1)
        self.assertIn("has no prepared theme", warnings[0])

        # Matching canonical source (also works via symlink)
        prepared = os.path.join(self.work, "theme")
        _write(os.path.join(prepared, "theme.css"), ":root { --accent: #003399; }")
        _write(os.path.join(prepared, "logo.webp"), b"RIFFwebp")
        _write(
            os.path.join(prepared, "theme.json"),
            json.dumps(
                {"source": os.path.realpath(brand_dir), "logo": "logo.webp", "header_note": "Prepared"}
            ),
        )
        warnings.clear()
        theme = load_theme(raw_link, self.work, self.work, warnings.append)
        self.assertEqual(warnings, [])
        assert theme is not None
        self.assertEqual(theme.css, ":root { --accent: #003399; }")
        self.assertTrue(theme.logo_uri.startswith("data:image/webp;base64,"))
        self.assertEqual(theme.header_note, "Prepared")

        # Mismatched source -> warn & return None
        warnings.clear()
        self.assertIsNone(load_theme(guide, self.work, self.work, warnings.append))
        self.assertEqual(len(warnings), 1)
        self.assertIn("does not match", warnings[0])

        # Relative source or missing source -> warn & return None
        for bad_source in ("brand", "", None):
            manifest = {} if bad_source is None else {"source": bad_source}
            _write(os.path.join(prepared, "theme.json"), json.dumps(manifest))
            warnings.clear()
            self.assertIsNone(load_theme(brand_dir, self.work, self.work, warnings.append))
            self.assertEqual(len(warnings), 1)
            self.assertIn("does not match", warnings[0])

    def test_css_breakout_and_warnings(self):
        for bad_css in ("body {} </style><script>x</script>", "/* ok */ </StYlE>"):
            p = _write(os.path.join(self.root, "bad.css"), bad_css)
            with self.assertRaises(ValueError):
                load_theme(p, self.work, self.work)

        # @import and non-data url(...) warn once
        for risky in (
            '@import url("https://fonts.example/a.css");',
            "body { background: url(logo.png); }",
            "body { background: url('https://cdn.example/bg.png'); }",
            'body { background: url("data:image/png;base64,AA"), url(rel.png); }',
        ):
            p = _write(os.path.join(self.root, "risky.css"), risky)
            warnings: list[str] = []
            theme = load_theme(p, self.work, self.work, warnings.append)
            self.assertIsNotNone(theme)
            self.assertEqual(len(warnings), 1)
            self.assertIn("@import", warnings[0])

        # data: URIs and commented-out URLs/@import (including unclosed comment) do not warn
        safe_css = (
            "/* @import 'https://example.com/a.css'; background: url(https://example.com/x.png); */\n"
            'body { background: url("data:image/png;base64,AAAA"); }\n'
            "h1 { background: url(DATA:image/svg+xml;utf8,<svg></svg>); }\n"
            "/* unclosed trailing comment with url(https://example.com/z.png) and @import 'x.css'"
        )
        p = _write(os.path.join(self.root, "safe.css"), safe_css)
        warnings = []
        theme = load_theme(p, self.work, self.work, warnings.append)
        self.assertIsNotNone(theme)
        self.assertEqual(warnings, [])

    def test_manifest_validation_and_unknown_keys(self):
        pack = os.path.join(self.root, "manifest-test")
        _write(
            os.path.join(pack, "theme.json"),
            json.dumps({"header_note": "Ok", "zzz": 1, "aaa": "x"}),
        )
        warnings: list[str] = []
        theme = load_theme(pack, self.work, self.work, warnings.append)
        assert theme is not None
        self.assertEqual(theme.header_note, "Ok")
        self.assertEqual(warnings, ["theme.json keys not recognised and ignored: aaa, zzz"])

        for bad_raw in ("{not json", "[]", '"string"'):
            _write(os.path.join(pack, "theme.json"), bad_raw)
            with self.assertRaises(ValueError):
                load_theme(pack, self.work, self.work)

        for key in ("source", "logo", "logo_alt", "header_note", "footer_note"):
            for bad_val in (None, True, 42, [], {}):
                _write(os.path.join(pack, "theme.json"), json.dumps({key: bad_val}))
                with self.assertRaises(ValueError):
                    load_theme(pack, self.work, self.work)

    def test_logo_security_limits_and_extensions(self):
        pack = os.path.join(self.root, "logo-test")
        outside = _write(os.path.join(self.root, "secret.png"), b"\x89PNG")
        os.makedirs(pack)
        os.symlink(outside, os.path.join(pack, "escape.png"))
        _write(os.path.join(pack, "notes.txt"), "not an image")
        os.symlink(os.path.join(pack, "notes.txt"), os.path.join(pack, "alias.png"))
        _write(os.path.join(pack, "bad.gif"), b"GIF89a")
        os.makedirs(os.path.join(pack, "dir.png"))
        _write(os.path.join(pack, "exact.jpg"), b"x" * (512 * 1024))
        _write(os.path.join(pack, "huge.jpeg"), b"x" * (512 * 1024 + 1))

        # Exact 512 KB limit succeeds
        _write(os.path.join(pack, "theme.json"), json.dumps({"logo": "exact.jpg"}))
        theme = load_theme(pack, self.work, self.work)
        assert theme is not None
        self.assertTrue(theme.logo_uri.startswith("data:image/jpeg;base64,"))

        bad_logos = [
            "../secret.png",
            "escape.png",
            "alias.png",
            outside,
            "~/secret.png",
            "https://example.com/logo.png",
            "http://example.com/logo.png",
            "data:image/png;base64,AAAA",
            "bad.gif",
            "noext",
            "missing.png",
            "dir.png",
            "huge.jpeg",
        ]
        for logo in bad_logos:
            _write(os.path.join(pack, "theme.json"), json.dumps({"logo": logo}))
            with self.subTest(logo=logo), self.assertRaises(ValueError):
                load_theme(pack, self.work, self.work)

    def test_missing_path_and_oserror_converted_to_value_error(self):
        with self.assertRaises(ValueError):
            load_theme("does-not-exist.css", self.work, self.work)

        # Directory named theme.css inside a ready-made folder
        bad_dir = os.path.join(self.root, "dir-named-css")
        os.makedirs(os.path.join(bad_dir, "theme.css"))
        with self.assertRaises(ValueError):
            load_theme(bad_dir, self.work, self.work)

        # Directory named theme.json inside a ready-made folder
        bad_json_dir = os.path.join(self.root, "dir-named-json")
        os.makedirs(os.path.join(bad_json_dir, "theme.json"))
        with self.assertRaises(ValueError):
            load_theme(bad_json_dir, self.work, self.work)

        # Unreadable CSS file (skip if running as root where chmod 0o000 is still readable)
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            unreadable = _write(os.path.join(self.root, "secret.css"), ":root {}")
            os.chmod(unreadable, 0)
            self.addCleanup(lambda: os.chmod(unreadable, stat.S_IRUSR | stat.S_IWUSR))
            with self.assertRaises(ValueError):
                load_theme(unreadable, self.work, self.work)

    def test_mismatched_prepared_snapshot_suppresses_unknown_key_warnings(self):
        raw = _write(os.path.join(self.root, "brand.md"), "# Brand\n")
        other = _write(os.path.join(self.root, "other.md"), "# Other\n")
        _write(
            os.path.join(self.work, "theme", "theme.json"),
            json.dumps({"source": os.path.realpath(other), "bogus": "ignored"}),
        )
        warnings: list[str] = []
        self.assertIsNone(load_theme(raw, self.work, self.work, warnings.append))
        self.assertEqual(len(warnings), 1)
        self.assertIn("does not match", warnings[0])


if __name__ == "__main__":
    unittest.main()
