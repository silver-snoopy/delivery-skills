# Idea: easy notes (answer on the DM page)

Status: idea, not designed yet. Raised 2026-11-17.

## The idea

Today the DM answers action rows and writes team notes in `notes.md`, which is a separate file next to the report. Instead, the DM could type answers and notes directly on the dm_ page, then press **Export** to get a `notes.md`. The next `report` run reads that file as it does now. The DM no longer has to open `notes.md` and find the right table row.

## First thoughts

- A browser has no good simple markdown editor built in, but none may be needed. The page could show one plain text box per action row (the Answer cell) and per team section (Summary, Exec note, Note, Status). The page then writes the markdown itself, so the DM never sees the table syntax.
- The report is a static local HTML file with no server, so Export would be a file download (a `notes.md` the DM saves over the old one). The page cannot write the file in place.
- `notes.md` keeps its current format and stays the single source. The page is only an easier way to fill it in. The agent flow (AGENTS.md / SKILL.md) can keep writing `notes.md` directly.

## Open questions

- Which part of filling in `notes.md` is the real pain: opening the file, finding the row, or typing the table format?
- Should the page also show answers already recorded (pre-filled boxes), and the tool-owned Answered cell as read-only?
- Where does the exported file go? Download, then the DM moves it, or a save dialog where the browser supports one.
- Must stay within the code cap in `tests/test_size.py`, and the page must stay standard-library HTML and JavaScript with no external libraries.
- dm_ is shared with team leads. Should they also be able to export answers, or only the DM?
