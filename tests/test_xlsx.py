import os
import tempfile
import unittest
import zipfile
from datetime import date
from xml.sax.saxutils import escape

from ledger.xlsx import match_name, read_vacations


def col_letters(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def build_workbook(path, rows, merges=(), sheet_name="Sheet1"):
    """rows: {row_number: {col_number: str | int}}; strings go to sharedStrings."""
    shared = []
    row_xml = []
    for r in sorted(rows):
        cells = []
        for c in sorted(rows[r]):
            v = rows[r][c]
            ref = "%s%d" % (col_letters(c), r)
            if isinstance(v, int):
                cells.append('<c r="%s"><v>%d</v></c>' % (ref, v))
            else:
                shared.append(v)
                cells.append('<c r="%s" t="s"><v>%d</v></c>' % (ref, len(shared) - 1))
        row_xml.append('<row r="%d">%s</row>' % (r, "".join(cells)))
    merge_xml = ""
    if merges:
        merge_xml = '<mergeCells count="%d">%s</mergeCells>' % (
            len(merges), "".join('<mergeCell ref="%s"/>' % m for m in merges))
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    sheet = ('<?xml version="1.0"?><worksheet xmlns="%s"><sheetData>%s</sheetData>%s</worksheet>'
             % (ns, "".join(row_xml), merge_xml))
    sst = ('<?xml version="1.0"?><sst xmlns="%s">%s</sst>'
           % (ns, "".join("<si><t>%s</t></si>" % escape(s) for s in shared)))
    wb = ('<?xml version="1.0"?><workbook xmlns="%s" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          '<sheets><sheet name="%s" sheetId="1" r:id="rId1"/></sheets></workbook>' % (ns, sheet_name))
    rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/></Relationships>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/sharedStrings.xml", sst)
        z.writestr("xl/worksheets/sheet1.xml", sheet)


E, F, G, AJ, AK, BO = 5, 6, 7, 36, 37, 67


def calendar_rows(titles=True, trailing=()):
    header = {1: "CT", 2: "Active", 3: "Squad", 4: "Name", E: 30, F: 31}
    for d in range(1, 31):
        header[G + d - 1] = d
    for d in range(1, 32):
        header[AK + d - 1] = d
    for i, d in enumerate(trailing):
        header[BO + 1 + i] = d
    rows = {1: {1: "Team Calendar"}, 3: header}
    if titles:
        rows[2] = {G: "November 2026", AK: "December 2026"}
    rows[4] = {4: "Maya R.", 3: "Alfa", F: "x", G + 2: "x", G + 3: "x", AK + 23: "H"}
    rows[5] = {4: "Zoë Brandt", 3: "Beta"}
    return rows


MERGES = ["A1:C2", "G2:AJ2", "AK2:BO2"]


class XlsxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "v.xlsx")

    def test_reads_team_calendar_layout(self):
        build_workbook(self.path, calendar_rows(), MERGES)
        v = read_vacations(self.path, half_day_codes=["H"])
        self.assertEqual(v.days_off["Maya R."], {date(2026, 10, 31): 1.0, date(2026, 11, 3): 1.0,
                                                 date(2026, 11, 4): 1.0, date(2026, 12, 24): 0.5})
        self.assertEqual(v.squad["Maya R."], "Alfa")
        self.assertEqual(v.squad["Zoë Brandt"], "Beta")
        self.assertEqual(v.days_off.get("Zoë Brandt", {}), {})
        self.assertEqual(v.warnings, [])

    def test_untitled_leading_run_is_previous_month(self):
        build_workbook(self.path, calendar_rows(), MERGES)
        v = read_vacations(self.path)
        self.assertIn(date(2026, 10, 31), v.days_off["Maya R."])
        self.assertEqual(v.days_off["Maya R."][date(2026, 12, 24)], 1.0)

    def test_trailing_partial_run_is_next_month(self):
        rows = calendar_rows(trailing=(1, 2, 3))
        rows[4][BO + 2] = "x"
        build_workbook(self.path, rows, MERGES)
        v = read_vacations(self.path)
        self.assertEqual(v.days_off["Maya R."][date(2027, 1, 2)], 1.0)
        self.assertEqual(v.warnings, [])

    def test_sheet_without_titles_is_a_warning(self):
        build_workbook(self.path, calendar_rows(titles=False), ["A1:C2"])
        v = read_vacations(self.path)
        self.assertEqual(v.days_off, {})
        self.assertIn("Sheet1", v.warnings[0])

    def test_match_name_ignores_case_accents_order(self):
        self.assertEqual(match_name("doe JOHN", ["John Doe", "Maya R."]), "John Doe")
        self.assertEqual(match_name("Doe John", ["John Doe"]), "John Doe")
        self.assertEqual(match_name("Zoë Brandt", ["Zoe Brandt"]), "Zoe Brandt")
        self.assertIsNone(match_name("Nobody", ["John Doe"]))


if __name__ == "__main__":
    unittest.main()
