"""Pluggable theme loading and validation for HTML reports."""

import base64
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass

KNOWN_THEME_REPORTS = ("manager", "dm", "person")
DEFAULT_THEME_REPORTS = ("manager",)
_LOGO_MIME = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_MAX_LOGO_BYTES = 512 * 1024
_MANIFEST_KEYS = {"source", "logo", "logo_alt", "header_note", "footer_note"}


@dataclass
class Theme:
    css: str = ""
    logo_uri: str = ""
    logo_alt: str = ""
    header_note: str = ""
    footer_note: str = ""


def _read_text(path: str, label: str) -> str:
    try:
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"cannot read {label} '{path}': {e}") from e


def _validate_css(css: str, warn: Callable[[str], None]) -> str:
    if re.search(r"</style", css, re.I):
        raise ValueError("theme CSS must not contain '</style'")
    clean = re.sub(r"/\*.*?(?:\*/|\Z)", "", css, flags=re.S)
    urls = re.finditer(r"""(?<![\w-])url\(\s*(?:"([^"]*)"|'([^']*)'|([^)]*))""", clean, re.I)
    if re.search(r"@import\b", clean, re.I) or any(
        not (m[1] or m[2] or m[3] or "").strip().lower().startswith("data:") for m in urls
    ):
        warn(
            "theme CSS references @import or external/relative url(...); "
            "offline or emailed reports may not load them"
        )
    return css


def _load_manifest(path: str, warn: Callable[[str], None]) -> dict[str, str]:
    try:
        data = json.loads(_read_text(path, "theme.json"))
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid theme.json '{path}': {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"theme.json must be a JSON object: '{path}'")
    if unknown := sorted(set(data) - _MANIFEST_KEYS):
        warn(f"theme.json keys not recognised and ignored: {', '.join(unknown)}")
    out = {k: data[k] for k in _MANIFEST_KEYS & data.keys()}
    for key, val in out.items():
        if not isinstance(val, str):
            raise ValueError(f"theme.json: '{key}' must be a string")
    return out


def _load_logo(theme_dir: str, logo: str) -> str:
    if not logo:
        return ""
    if os.path.isabs(logo) or logo.startswith("~") or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", logo):
        raise ValueError(f"theme logo must be a relative path inside the theme directory: '{logo}'")
    mime = _LOGO_MIME.get(os.path.splitext(logo)[1].lower())
    if not mime:
        raise ValueError(f"unsupported theme logo extension: '{logo}'")
    root, path = os.path.realpath(theme_dir), os.path.realpath(os.path.join(theme_dir, logo))
    same_drive = os.path.splitdrive(root)[0].lower() == os.path.splitdrive(path)[0].lower()
    if not same_drive or os.path.commonpath([root, path]) != root or path == root:
        raise ValueError(f"theme logo escapes theme directory: '{logo}'")
    if os.path.splitext(path)[1].lower() not in _LOGO_MIME:
        raise ValueError(f"unsupported theme logo extension: '{logo}'")
    if not os.path.isfile(path):
        raise ValueError(f"theme logo not found: '{logo}'")
    try:
        with open(path, "rb") as f:
            raw = f.read(_MAX_LOGO_BYTES + 1)
    except OSError as e:
        raise ValueError(f"cannot read theme logo '{logo}': {e}") from e
    if len(raw) > _MAX_LOGO_BYTES:
        raise ValueError(f"theme logo exceeds {_MAX_LOGO_BYTES // 1024} KB limit: '{logo}'")
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def load_theme(
    raw_path: str, base_dir: str, work_dir: str, warn: Callable[[str], None] = lambda line: None
) -> Theme | None:
    if not raw_path:
        return None
    resolved = os.path.abspath(os.path.join(base_dir, os.path.expanduser(raw_path)))
    if not os.path.exists(resolved):
        raise ValueError(f"theme path not found: '{raw_path}'")
    if os.path.isfile(resolved) and resolved.lower().endswith(".css"):
        return Theme(css=_validate_css(_read_text(resolved, "theme CSS"), warn))
    theme_dir, manifest = resolved, None
    ready = os.path.isdir(resolved) and any(
        os.path.exists(os.path.join(resolved, n)) for n in ("theme.css", "theme.json")
    )
    if not ready:
        theme_dir = os.path.join(work_dir, "theme")
        pjson = os.path.join(theme_dir, "theme.json")
        if not os.path.exists(pjson):
            warn(f"theme '{raw_path}' has no prepared theme in {theme_dir} yet; using default styling")
            return None
        pending: list[str] = []
        manifest = _load_manifest(pjson, pending.append)
        src = manifest.get("source", "")
        if not src or not os.path.isabs(src) or os.path.realpath(src) != os.path.realpath(resolved):
            warn(f"prepared theme in {theme_dir} does not match '{raw_path}'; using default styling")
            return None
        for line in pending:
            warn(line)
    css_path, json_path = os.path.join(theme_dir, "theme.css"), os.path.join(theme_dir, "theme.json")
    if manifest is None:
        manifest = _load_manifest(json_path, warn) if os.path.exists(json_path) else {}
    css = _validate_css(_read_text(css_path, "theme.css"), warn) if os.path.exists(css_path) else ""
    alt, head, foot = (manifest.get(k, "") for k in ("logo_alt", "header_note", "footer_note"))
    return Theme(css, _load_logo(theme_dir, manifest.get("logo", "")), alt, head, foot)
