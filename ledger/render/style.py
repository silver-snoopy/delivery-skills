"""The one embedded stylesheet. System fonts, colours as custom properties
with light and dark values, no external resources."""

_BASE = """
:root{
  --bg:#f5f7f8; --surface:#ffffff; --ink:#16242c; --muted:#5a6b75; --line:#d6dee2;
  --accent:#1f6f8b; --accent-soft:#e1eef3;
  --good:#2c7a4b; --good-soft:#e2f1e7; --warn:#96650a; --warn-soft:#f8ecd2;
  --crit:#a8322d; --crit-soft:#f7e1df;
  --ours:#1f6f8b; --other:#8a6fb0; --wait:#c58a1c; --idle:#9aa7ae; --parked:#c9d2d6; --done:#2c7a4b;
  --text:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme: dark){ :root{
  --bg:#10181c; --surface:#18232a; --ink:#e6edf0; --muted:#9bacb5; --line:#2e3d46;
  --accent:#6bb8d1; --accent-soft:#1d3640;
  --good:#6cc48d; --good-soft:#1b3326; --warn:#e0b04f; --warn-soft:#3a2f14;
  --crit:#ee8a84; --crit-soft:#3d1f1d;
  --ours:#6bb8d1; --other:#ab94cc; --wait:#d9a53f; --idle:#62727b; --parked:#45535b; --done:#6cc48d;
  color-scheme:dark } }
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--text);font-size:15px;line-height:1.5}
.page{max-width:1080px;margin:0 auto;padding:28px 16px 56px;display:flex;flex-direction:column;gap:32px}
h1,h2,h3,h4{margin:0;line-height:1.2}
h1{font-size:28px;font-weight:700}
h2{font-size:20px;font-weight:600}
h3{font-size:17px;font-weight:600}
h4{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin-bottom:8px}
p{margin:0;max-width:68ch}
a{color:var(--accent)}
.muted{color:var(--muted)}
.small{font-size:13px}
.id{font-family:var(--mono);font-size:13px;white-space:nowrap}
.num{font-variant-numeric:tabular-nums}
section{display:flex;flex-direction:column;gap:14px;min-width:0}
.head{display:flex;flex-wrap:wrap;gap:16px;justify-content:space-between;align-items:flex-end}
.head > div{display:flex;flex-direction:column;gap:4px;min-width:0}
.figures{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:8px;overflow:hidden}
.figures > div{background:var(--surface);padding:14px 16px;display:flex;flex-direction:column;gap:2px}
.figures b{font-size:24px;font-weight:600;font-variant-numeric:tabular-nums;line-height:1.1}
.figures span{font-size:13px;color:var(--muted)}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:9px 12px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}
tr:last-child td{border-bottom:0}
th{font-size:12px;font-weight:600;color:var(--muted);background:var(--bg)}
td.c,th.c{text-align:center}
td.wide{min-width:220px}
.pill{display:inline-block;font-size:12px;font-weight:600;padding:2px 9px;border-radius:999px;margin:1px 2px 1px 0}
.good{background:var(--good-soft);color:var(--good)}
.warn{background:var(--warn-soft);color:var(--warn)}
.crit{background:var(--crit-soft);color:var(--crit)}
.info{background:var(--accent-soft);color:var(--accent)}
.marker{font-size:12px;color:var(--muted);font-style:italic;display:block}
.spark{display:inline-flex;align-items:flex-end;gap:2px;height:26px;vertical-align:middle}
.spark i{display:block;width:6px;min-height:1px;background:var(--idle);border-radius:1px}
.spark i:last-child{background:var(--accent)}
.team{border-top:2px solid var(--ink);padding-top:16px;gap:18px}
.team-head{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:24px}
.cols > div{display:flex;flex-direction:column;gap:8px;min-width:0}
ul{margin:0;padding-left:18px;display:flex;flex-direction:column;gap:6px}
li{max-width:68ch}
.bar{display:flex;height:18px;border-radius:4px;overflow:hidden;background:var(--line)}
.bar span{display:block;height:100%}
.ours{background:var(--ours)} .other{background:var(--other)} .wait{background:var(--wait)}
.idle{background:var(--idle)} .parked{background:var(--parked)} .done{background:var(--done)}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;color:var(--muted);margin-top:8px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}
.risk{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;padding:12px 0;border-bottom:1px solid var(--line)}
.risk:last-child{border-bottom:0}
.risk p{grid-column:2}
"""

_DM = """
.restricted{font-size:12px;font-weight:600;color:var(--crit);border:1px solid var(--crit);border-radius:4px;padding:1px 7px;white-space:nowrap;align-self:flex-start}
.switch{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--surface)}
.switch button{font:inherit;font-size:13px;font-weight:500;padding:8px 14px;border:0;background:transparent;color:var(--muted);cursor:pointer}
.switch button[aria-pressed="true"]{background:var(--accent);color:var(--surface)}
.switch button:focus-visible{outline:2px solid var(--ink);outline-offset:-2px}
body:not(.view-dm) .dm-only{display:none}
.card{border:1px solid var(--line);border-radius:8px;background:var(--surface);padding:16px;display:flex;flex-direction:column;gap:14px}
.timeline{display:flex;height:30px;border-radius:4px;overflow:hidden;background:var(--line)}
.timeline span{display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;color:var(--surface);min-width:0;overflow:hidden;white-space:nowrap}
@media print{ .switch{display:none} }
"""

_PRINT = """
@media print{ body{background:#fff} .tablewrap{overflow:visible} }
"""


def css(variant: str) -> str:
    return _BASE + (_DM if variant == "dm" else "") + _PRINT
