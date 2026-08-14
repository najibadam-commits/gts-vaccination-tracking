"""GTS design system — tokens, icons, CSS and reusable UI primitives.

This module owns *presentation only*. It contains no pipeline logic and is
imported by ``app.py`` purely to render chrome. Keeping it separate means the
look of the app can be changed without going anywhere near the stage scripts.

How the theming works
---------------------
Every colour, radius, shadow and font size lives in :data:`TOKENS` as a plain
Python dict, one entry per theme ("light" / "dark"). :func:`build_css` turns the
active token set into a ``:root { --gts-*: ... }`` block and prepends it to the
static stylesheet, which references those variables exclusively. Because the
switch happens server-side in Python, the toggle is instant and needs no
JavaScript — Streamlit strips ``<script>`` from ``st.markdown`` anyway.

A note on HTML strings
----------------------
Streamlit's markdown renderer treats any line indented by 4+ spaces as a code
block, so raw tags leak through as literal text. Every HTML helper here funnels
its output through :func:`_html`, which strips per-line indentation and joins
the result into a single line. Always build markup through these helpers rather
than writing multi-line f-strings inline.
"""

from __future__ import annotations

import base64
import os

# ---------------------------------------------------------------- design tokens

_SHARED = {
    # typography
    "font": "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    "font-mono": "'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace",
    "fs-xs": "11.5px",
    "fs-sm": "12.5px",
    "fs-base": "13.5px",
    "fs-md": "15px",
    "fs-lg": "17px",
    "fs-xl": "21px",
    "fs-2xl": "26px",
    # spacing scale
    "sp-1": "4px",
    "sp-2": "8px",
    "sp-3": "12px",
    "sp-4": "16px",
    "sp-5": "20px",
    "sp-6": "24px",
    "sp-8": "32px",
    # radii
    "r-sm": "6px",
    "r-md": "9px",
    "r-lg": "13px",
    "r-xl": "18px",
    "r-full": "999px",
    # motion
    "ease": "cubic-bezier(0.4, 0, 0.2, 1)",
    "dur": "160ms",
}

TOKENS = {
    "light": dict(
        _SHARED,
        **{
            # surfaces
            "bg": "#F6F8FC",
            "bg-alt": "#EEF2F8",
            "surface": "#FFFFFF",
            "surface-2": "#F5F8FC",
            "surface-3": "#EAF0F8",
            "sidebar-bg": "#FFFFFF",
            "overlay": "rgba(15, 23, 42, 0.45)",
            # lines
            "border": "#E3E9F2",
            "border-strong": "#CBD6E4",
            "border-focus": "#2E75B6",
            # text
            "text": "#0F1B2D",
            "text-muted": "#5A6B82",
            "text-subtle": "#8A9AB0",
            "text-inverse": "#FFFFFF",
            # brand
            "brand": "#1F4E79",
            "brand-hover": "#17405F",
            "brand-light": "#2E75B6",
            "brand-tint": "#EAF2FA",
            "brand-tint-strong": "#D6E6F5",
            "brand-grad": "linear-gradient(135deg, #1F4E79 0%, #2E75B6 100%)",
            # semantic
            "ok": "#0E8A50",
            "ok-tint": "#E4F5EC",
            "warn": "#B45309",
            "warn-tint": "#FDF3E3",
            "danger": "#C02626",
            "danger-tint": "#FCEBEB",
            "info": "#1D5FBF",
            "info-tint": "#E8F0FD",
            "neutral-tint": "#EFF3F8",
            # elevation
            "shadow-xs": "0 1px 2px rgba(15, 27, 45, 0.05)",
            "shadow-sm": "0 1px 3px rgba(15, 27, 45, 0.07), 0 1px 2px rgba(15, 27, 45, 0.04)",
            "shadow-md": "0 6px 16px -4px rgba(15, 27, 45, 0.10), 0 2px 6px -2px rgba(15, 27, 45, 0.06)",
            "shadow-lg": "0 18px 38px -10px rgba(15, 27, 45, 0.18)",
            "ring": "0 0 0 3px rgba(46, 117, 182, 0.20)",
            "scrim": "rgba(255, 255, 255, 0.65)",
        },
    ),
    "dark": dict(
        _SHARED,
        **{
            "bg": "#0A1120",
            "bg-alt": "#0E1728",
            "surface": "#121C2E",
            "surface-2": "#182437",
            "surface-3": "#1F2D43",
            "sidebar-bg": "#0E1728",
            "overlay": "rgba(2, 6, 15, 0.65)",
            "border": "#233247",
            "border-strong": "#33455F",
            "border-focus": "#5AA7E8",
            "text": "#E8EFF9",
            "text-muted": "#9AACC4",
            "text-subtle": "#6B7E97",
            "text-inverse": "#0A1120",
            "brand": "#4A9BE0",
            "brand-hover": "#63ACE9",
            "brand-light": "#7CBCEE",
            "brand-tint": "rgba(74, 155, 224, 0.13)",
            "brand-tint-strong": "rgba(74, 155, 224, 0.24)",
            "brand-grad": "linear-gradient(135deg, #1B4A75 0%, #2E75B6 100%)",
            "ok": "#3DD68C",
            "ok-tint": "rgba(61, 214, 140, 0.13)",
            "warn": "#F0B429",
            "warn-tint": "rgba(240, 180, 41, 0.13)",
            "danger": "#F87171",
            "danger-tint": "rgba(248, 113, 113, 0.13)",
            "info": "#60A5FA",
            "info-tint": "rgba(96, 165, 250, 0.13)",
            "neutral-tint": "rgba(154, 172, 196, 0.10)",
            "shadow-xs": "0 1px 2px rgba(0, 0, 0, 0.30)",
            "shadow-sm": "0 1px 3px rgba(0, 0, 0, 0.40), 0 1px 2px rgba(0, 0, 0, 0.24)",
            "shadow-md": "0 6px 18px -4px rgba(0, 0, 0, 0.55), 0 2px 6px -2px rgba(0, 0, 0, 0.35)",
            "shadow-lg": "0 18px 42px -10px rgba(0, 0, 0, 0.70)",
            "ring": "0 0 0 3px rgba(90, 167, 232, 0.28)",
            "scrim": "rgba(10, 17, 32, 0.65)",
        },
    ),
}

DEFAULT_THEME = "light"


# ------------------------------------------------------------------- utilities

def _html(markup: str) -> str:
    """Flatten markup to one line so Streamlit never sees it as a code block."""
    return "".join(line.strip() for line in markup.strip().splitlines())


def img_b64(path: str | None) -> str | None:
    """Base64-encode an image so it can be inlined in markup. None if missing."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as fh:
            return base64.b64encode(fh.read()).decode()
    except Exception:
        return None


# ----------------------------------------------------------------------- icons
# Stroke-based 20x20 glyphs in the Lucide idiom. `currentColor` lets every icon
# inherit the colour of its container, so one definition serves every context.

_ICON_PATHS = {
    "compass": '<circle cx="12" cy="12" r="9"/><polygon points="16.2 7.8 14.1 14.1 7.8 16.2 9.9 9.9 16.2 7.8"/>',
    "database": '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
    "satellite": '<path d="M13 7 9 3 3 9l4 4"/><path d="m17 11 4 4-6 6-4-4"/><path d="m8 12 4 4"/><path d="M16 8a4 4 0 0 1 0 8"/>',
    "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.4"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/>',
    "map": '<polygon points="2 6 9 3 15 6 22 3 22 18 15 21 9 18 2 21"/><path d="M9 3v15M15 6v15"/>',
    "layers": '<polygon points="12 2 22 8 12 14 2 8"/><polyline points="2 13 12 19 22 13"/>',
    "file": '<path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7z"/><polyline points="14 2 14 7 19 7"/><path d="M9 13h6M9 17h4"/>',
    "package": '<path d="M21 8v8l-9 5-9-5V8l9-5z"/><polyline points="3 8 12 13 21 8"/><path d="M12 13v8"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><path d="M12 15V3"/>',
    "chart": '<path d="M3 3v18h18"/><rect x="7" y="12" width="3" height="6" rx="1"/><rect x="12" y="8" width="3" height="10" rx="1"/><rect x="17" y="5" width="3" height="13" rx="1"/>',
    "play": '<polygon points="6 3 20 12 6 21"/>',
    "refresh": '<path d="M3 12a9 9 0 0 1 15.5-6.2L21 8"/><polyline points="21 3 21 8 16 8"/><path d="M21 12a9 9 0 0 1-15.5 6.2L3 16"/><polyline points="3 21 3 16 8 16"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><polyline points="8.5 12.2 11 14.7 15.8 9.4"/>',
    "circle": '<circle cx="12" cy="12" r="8.5"/>',
    "dot": '<circle cx="12" cy="12" r="4"/>',
    "alert": '<path d="M12 3 2 20h20z"/><path d="M12 9v5M12 17.2v.1"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 7.8v.1"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 9 19.4a1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.6 9a1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9"/><path d="M16 3.1a4 4 0 0 1 0 7.8"/>',
    "sparkle": '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9.5" r="1.8"/><polyline points="21 16 15.5 10.5 4 20"/>',
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    "pin": '<path d="M12 21s7-6.2 7-11a7 7 0 1 0-14 0c0 4.8 7 11 7 11z"/><circle cx="12" cy="10" r="2.6"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/>',
    "arrow-right": '<path d="M5 12h14"/><polyline points="13 6 19 12 13 18"/>',
}


def icon(name: str, size: int = 18, stroke: float = 1.7, cls: str = "") -> str:
    """Return an inline SVG glyph that inherits its container's colour."""
    paths = _ICON_PATHS.get(name, _ICON_PATHS["circle"])
    return _html(
        f'<svg class="gts-ico {cls}" width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'fill="none" stroke="currentColor" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true">{paths}</svg>'
    )


# ------------------------------------------------------------------ stylesheet

# CSS requires @import to precede every other rule, so it is kept out of the
# static block and emitted first by build_css — ahead of the :root token block.
_FONT_IMPORT = (
    "@import url('https://fonts.googleapis.com/css2"
    "?family=Inter:wght@400;450;500;600;700&display=swap');"
)

_STATIC_CSS = """
/* ============================ base ============================ */
html, body, [class*="css"], .stApp, [data-testid="stAppViewContainer"] {
    font-family: var(--gts-font);
    font-feature-settings: 'cv02', 'cv03', 'cv04', 'tnum';
    -webkit-font-smoothing: antialiased;
}
.stApp, [data-testid="stAppViewContainer"] { background: var(--gts-bg); }
[data-testid="stMain"] { background: var(--gts-bg); }
[data-testid="stHeader"] { background: transparent; height: 0; }
[data-testid="stToolbar"] { right: 8px; }
footer, #MainMenu { visibility: hidden; }
[data-testid="stDecoration"] { display: none; }

[data-testid="stMainBlockContainer"], .block-container {
    padding-top: 1.6rem;
    padding-bottom: 4rem;
    max-width: 1500px;
}

/* text colour is applied broadly, then walked back for widgets that own theirs */
[data-testid="stMain"] p, [data-testid="stMain"] li, [data-testid="stMain"] span,
[data-testid="stMain"] label, [data-testid="stMain"] h1, [data-testid="stMain"] h2,
[data-testid="stMain"] h3, [data-testid="stMain"] h4 { color: var(--gts-text); }
[data-testid="stMain"] a { color: var(--gts-brand-light); }
.gts-ico { flex: none; vertical-align: middle; }

/* ============================ app bar ============================ */
.gts-appbar {
    display: flex; align-items: center; gap: var(--gts-sp-4);
    background: var(--gts-surface);
    border: 1px solid var(--gts-border);
    border-radius: var(--gts-r-xl);
    padding: 16px 22px;
    margin-bottom: var(--gts-sp-5);
    box-shadow: var(--gts-shadow-sm);
    position: relative;
    overflow: hidden;
}
.gts-appbar::before {
    content: ''; position: absolute; inset: 0 auto 0 0; width: 4px;
    background: var(--gts-brand-grad);
}
.gts-appbar-logo {
    height: 38px; width: auto; object-fit: contain;
    padding-left: 6px;
}
.gts-appbar-divider {
    width: 1px; height: 34px; background: var(--gts-border); flex: none;
}
.gts-appbar-title {
    margin: 0; font-size: var(--gts-fs-xl); font-weight: 700;
    letter-spacing: -0.4px; color: var(--gts-text); line-height: 1.15;
}
.gts-appbar-sub {
    margin: 3px 0 0; font-size: var(--gts-fs-sm); color: var(--gts-text-muted);
    letter-spacing: 0.1px;
}
.gts-appbar-spacer { flex: 1 1 auto; }
.gts-appbar-meta { display: flex; align-items: center; gap: var(--gts-sp-2); flex-wrap: wrap; }

/* ============================ pills ============================ */
.gts-pill {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 11px; border-radius: var(--gts-r-full);
    font-size: var(--gts-fs-xs); font-weight: 600; letter-spacing: 0.2px;
    border: 1px solid transparent; white-space: nowrap; line-height: 1.5;
}
.gts-pill.ok      { background: var(--gts-ok-tint);      color: var(--gts-ok);      border-color: color-mix(in srgb, var(--gts-ok) 26%, transparent); }
.gts-pill.warn    { background: var(--gts-warn-tint);    color: var(--gts-warn);    border-color: color-mix(in srgb, var(--gts-warn) 26%, transparent); }
.gts-pill.danger  { background: var(--gts-danger-tint);  color: var(--gts-danger);  border-color: color-mix(in srgb, var(--gts-danger) 26%, transparent); }
.gts-pill.info    { background: var(--gts-info-tint);    color: var(--gts-info);    border-color: color-mix(in srgb, var(--gts-info) 26%, transparent); }
.gts-pill.brand   { background: var(--gts-brand-tint);   color: var(--gts-brand-light); border-color: color-mix(in srgb, var(--gts-brand-light) 30%, transparent); }
.gts-pill.neutral { background: var(--gts-neutral-tint); color: var(--gts-text-muted); border-color: var(--gts-border); }

/* ============================ section headers ============================ */
.gts-sec { display: flex; align-items: flex-start; gap: 12px; margin: 2px 0 14px; }
.gts-sec-ico {
    display: inline-flex; align-items: center; justify-content: center;
    width: 34px; height: 34px; min-width: 34px; border-radius: 10px;
    background: var(--gts-brand-tint); color: var(--gts-brand-light);
    border: 1px solid color-mix(in srgb, var(--gts-brand-light) 22%, transparent);
}
.gts-sec-body { min-width: 0; flex: 1; }
.gts-sec-title {
    margin: 0; font-size: var(--gts-fs-md); font-weight: 650;
    color: var(--gts-text); letter-spacing: -0.15px; line-height: 1.35;
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
}
.gts-sec-sub {
    margin: 3px 0 0; font-size: var(--gts-fs-sm); color: var(--gts-text-muted);
    line-height: 1.55; max-width: 78ch;
}
.gts-divider { height: 1px; background: var(--gts-border); margin: 18px 0 16px; border: 0; }

/* ============================ stat cards ============================ */
.gts-stats { display: grid; gap: 10px; margin: 2px 0 4px; }
.gts-stat {
    background: var(--gts-surface-2);
    border: 1px solid var(--gts-border);
    border-radius: var(--gts-r-md);
    padding: 11px 14px;
    transition: border-color var(--gts-dur) var(--gts-ease), transform var(--gts-dur) var(--gts-ease);
}
.gts-stat:hover { border-color: var(--gts-border-strong); transform: translateY(-1px); }
.gts-stat-label {
    font-size: var(--gts-fs-xs); font-weight: 600; letter-spacing: 0.5px;
    text-transform: uppercase; color: var(--gts-text-subtle); margin: 0 0 5px;
    display: flex; align-items: center; gap: 5px;
}
.gts-stat-value {
    font-size: var(--gts-fs-xl); font-weight: 680; color: var(--gts-text);
    margin: 0; letter-spacing: -0.5px; line-height: 1.1;
    font-variant-numeric: tabular-nums;
}
.gts-stat-sub { font-size: var(--gts-fs-xs); color: var(--gts-text-muted); margin: 4px 0 0; }
.gts-stat.accent { background: var(--gts-brand-tint); border-color: color-mix(in srgb, var(--gts-brand-light) 24%, transparent); }
.gts-stat.accent .gts-stat-value { color: var(--gts-brand-light); }

/* ============================ empty state ============================ */
.gts-empty {
    display: flex; flex-direction: column; align-items: center; text-align: center;
    padding: 44px 24px; border: 1.5px dashed var(--gts-border-strong);
    border-radius: var(--gts-r-lg); background: var(--gts-surface-2);
}
.gts-empty-ico {
    display: inline-flex; align-items: center; justify-content: center;
    width: 46px; height: 46px; border-radius: 14px; margin-bottom: 14px;
    background: var(--gts-surface); color: var(--gts-text-subtle);
    border: 1px solid var(--gts-border);
}
.gts-empty-title { margin: 0; font-size: var(--gts-fs-md); font-weight: 620; color: var(--gts-text); }
.gts-empty-body { margin: 6px 0 0; font-size: var(--gts-fs-sm); color: var(--gts-text-muted); max-width: 46ch; line-height: 1.6; }

/* ============================ results banner ============================ */
.gts-result {
    display: flex; align-items: center; gap: 14px;
    background: var(--gts-surface); border: 1px solid var(--gts-border);
    border-left: 4px solid var(--gts-ok);
    border-radius: var(--gts-r-lg); padding: 15px 20px; margin-bottom: var(--gts-sp-4);
    box-shadow: var(--gts-shadow-sm);
}
.gts-result-ico {
    display: inline-flex; align-items: center; justify-content: center;
    width: 38px; height: 38px; min-width: 38px; border-radius: 11px;
    background: var(--gts-ok-tint); color: var(--gts-ok);
}
.gts-result-title { margin: 0; font-size: var(--gts-fs-lg); font-weight: 660; color: var(--gts-text); letter-spacing: -0.2px; }
.gts-result-sub { margin: 3px 0 0; font-size: var(--gts-fs-sm); color: var(--gts-text-muted); }

/* ============================ sidebar ============================ */
section[data-testid="stSidebar"] {
    background: var(--gts-sidebar-bg);
    border-right: 1px solid var(--gts-border);
}
section[data-testid="stSidebar"] > div { padding-top: 1.1rem; }
section[data-testid="stSidebar"] [data-testid="stSidebarContent"] { padding-left: 4px; padding-right: 4px; }
section[data-testid="stSidebar"] p, section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] label, section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2, section[data-testid="stSidebar"] h3 { color: var(--gts-text); }
section[data-testid="stSidebar"] hr { border-color: var(--gts-border); margin: 14px 0; }
[data-testid="stSidebarCollapseButton"] button { color: var(--gts-text-muted) !important; }

.gts-brand { display: flex; align-items: center; gap: 10px; padding: 0 6px 2px; }
.gts-brand img { height: 26px; width: auto; object-fit: contain; }
.gts-brand-name { font-size: var(--gts-fs-base); font-weight: 680; color: var(--gts-text); letter-spacing: -0.2px; line-height: 1.2; margin: 0; }
.gts-brand-sub { font-size: var(--gts-fs-xs); color: var(--gts-text-subtle); margin: 1px 0 0; letter-spacing: 0.2px; }

.gts-eyebrow {
    font-size: var(--gts-fs-xs); font-weight: 660; letter-spacing: 0.7px;
    text-transform: uppercase; color: var(--gts-text-subtle);
    margin: 0 0 8px; padding: 0 6px; display: flex; align-items: center; gap: 6px;
}

/* readiness list */
.gts-check { display: flex; flex-direction: column; gap: 1px; padding: 0 2px; }
.gts-check-row {
    display: flex; align-items: center; gap: 9px;
    padding: 6px 8px; border-radius: var(--gts-r-sm);
    font-size: var(--gts-fs-sm); color: var(--gts-text-muted);
    transition: background var(--gts-dur) var(--gts-ease);
}
.gts-check-row:hover { background: var(--gts-surface-2); }
.gts-check-row.done { color: var(--gts-text); }
.gts-check-row.done .gts-check-ico { color: var(--gts-ok); }
.gts-check-row.todo .gts-check-ico { color: var(--gts-text-subtle); }
.gts-check-row.opt .gts-check-ico { color: var(--gts-border-strong); }
.gts-check-ico { display: inline-flex; flex: none; }
.gts-check-label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gts-check-tag { font-size: 10px; color: var(--gts-text-subtle); text-transform: uppercase; letter-spacing: 0.4px; font-weight: 600; }

/* readiness meter */
.gts-meter { padding: 2px 8px 4px; }
.gts-meter-top { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 7px; }
.gts-meter-count { font-size: var(--gts-fs-lg); font-weight: 680; color: var(--gts-text); letter-spacing: -0.3px; font-variant-numeric: tabular-nums; }
.gts-meter-count span { font-size: var(--gts-fs-sm); font-weight: 500; color: var(--gts-text-subtle); }
.gts-meter-state { font-size: var(--gts-fs-xs); font-weight: 620; letter-spacing: 0.3px; }
.gts-meter-track { height: 6px; border-radius: var(--gts-r-full); background: var(--gts-surface-3); overflow: hidden; }
.gts-meter-fill {
    height: 100%; border-radius: var(--gts-r-full);
    background: var(--gts-brand-grad);
    transition: width 420ms var(--gts-ease);
}
.gts-meter-fill.ready { background: linear-gradient(135deg, var(--gts-ok) 0%, color-mix(in srgb, var(--gts-ok) 62%, white) 100%); }

/* ============================ tabs ============================ */
.stTabs [data-baseweb="tab-list"] {
    gap: 4px; background: var(--gts-surface);
    border: 1px solid var(--gts-border); border-radius: var(--gts-r-lg);
    padding: 5px; margin-bottom: 16px; box-shadow: var(--gts-shadow-xs);
    overflow-x: auto; scrollbar-width: none;
}
.stTabs [data-baseweb="tab-list"]::-webkit-scrollbar { display: none; }
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] { display: none; }
.stTabs [data-baseweb="tab"] {
    height: auto; padding: 8px 16px; border-radius: var(--gts-r-md);
    font-size: var(--gts-fs-base); font-weight: 560; color: var(--gts-text-muted);
    background: transparent; border: none; white-space: nowrap;
    transition: background var(--gts-dur) var(--gts-ease), color var(--gts-dur) var(--gts-ease);
}
.stTabs [data-baseweb="tab"]:hover { background: var(--gts-surface-2); color: var(--gts-text); }
.stTabs [aria-selected="true"] {
    background: var(--gts-brand) !important; color: #fff !important;
    box-shadow: var(--gts-shadow-sm);
}
.stTabs [aria-selected="true"] p { color: #fff !important; font-weight: 600; }
.stTabs [data-baseweb="tab"] p { font-size: var(--gts-fs-base); margin: 0; }
.stTabs [data-baseweb="tab-panel"] { padding-top: 2px; }

/* ============================ cards ============================ */
div[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: var(--gts-r-lg) !important;
    border: 1px solid var(--gts-border) !important;
    background: var(--gts-surface) !important;
    box-shadow: var(--gts-shadow-xs);
    margin-bottom: 14px;
    transition: box-shadow var(--gts-dur) var(--gts-ease), border-color var(--gts-dur) var(--gts-ease);
}
div[data-testid="stVerticalBlockBorderWrapper"]:hover { box-shadow: var(--gts-shadow-sm); }
div[data-testid="stVerticalBlockBorderWrapper"] > div > div[data-testid="stVerticalBlock"] { padding: 16px 18px; gap: 0.65rem; }
/* nested cards stay flat so they read as sub-groups, not stacked panels */
div[data-testid="stVerticalBlockBorderWrapper"] div[data-testid="stVerticalBlockBorderWrapper"] {
    background: var(--gts-surface-2) !important; box-shadow: none;
}
section[data-testid="stSidebar"] div[data-testid="stVerticalBlockBorderWrapper"] {
    background: var(--gts-surface-2) !important; box-shadow: none;
}

/* ============================ buttons ============================ */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
    border-radius: var(--gts-r-md);
    font-weight: 580;
    font-size: var(--gts-fs-base);
    padding: 8px 16px;
    border: 1px solid var(--gts-border-strong);
    background: var(--gts-surface);
    color: var(--gts-text);
    transition: all var(--gts-dur) var(--gts-ease);
    box-shadow: var(--gts-shadow-xs);
}
.stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {
    border-color: var(--gts-brand-light);
    color: var(--gts-brand-light);
    background: var(--gts-surface-2);
    transform: translateY(-1px);
    box-shadow: var(--gts-shadow-sm);
}
.stButton > button:active, .stDownloadButton > button:active { transform: translateY(0); box-shadow: var(--gts-shadow-xs); }
.stButton > button:focus-visible, .stDownloadButton > button:focus-visible { box-shadow: var(--gts-ring); outline: none; }

.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
    background: var(--gts-brand-grad); color: #fff; border: none;
    box-shadow: var(--gts-shadow-sm);
}
.stButton > button[kind="primary"]:hover:not(:disabled) {
    filter: brightness(1.08); color: #fff; box-shadow: var(--gts-shadow-md); transform: translateY(-1px);
}
.stButton > button[kind="primary"]:disabled, .stButton > button:disabled {
    opacity: 0.45; cursor: not-allowed; transform: none; box-shadow: none; filter: none;
}
.stDownloadButton > button {
    justify-content: flex-start; text-align: left; width: 100%;
}

/* ============================ file uploader ============================ */
[data-testid="stFileUploader"] > label { display: none; }
[data-testid="stFileUploaderDropzone"] {
    border-radius: var(--gts-r-md);
    border: 1.5px dashed var(--gts-border-strong);
    background: var(--gts-surface-2);
    padding: 16px 18px;
    transition: all var(--gts-dur) var(--gts-ease);
}
[data-testid="stFileUploaderDropzone"]:hover {
    border-color: var(--gts-brand-light);
    background: var(--gts-brand-tint);
}
[data-testid="stFileUploaderDropzoneInstructions"] { color: var(--gts-text-muted); }
[data-testid="stFileUploaderDropzoneInstructions"] span { color: var(--gts-text); font-size: var(--gts-fs-base); }
[data-testid="stFileUploaderDropzoneInstructions"] small { color: var(--gts-text-subtle); font-size: var(--gts-fs-xs); }
[data-testid="stFileUploaderDropzone"] button {
    border-radius: var(--gts-r-sm); border: 1px solid var(--gts-border-strong);
    background: var(--gts-surface); color: var(--gts-text); font-weight: 560;
}
[data-testid="stFileUploaderFile"] {
    background: var(--gts-surface-2); border: 1px solid var(--gts-border);
    border-radius: var(--gts-r-sm); padding: 7px 10px; margin-top: 6px;
}
[data-testid="stFileUploaderFileName"] { color: var(--gts-text); font-size: var(--gts-fs-sm); }
[data-testid="stFileUploaderFile"] small { color: var(--gts-text-subtle); }

/* ============================ inputs ============================ */
[data-testid="stWidgetLabel"] p, .stTextInput label, .stNumberInput label, .stSelectbox label {
    font-size: var(--gts-fs-sm) !important; font-weight: 560 !important;
    color: var(--gts-text-muted) !important; margin-bottom: 3px !important;
}
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="select"] > div {
    border-radius: var(--gts-r-sm) !important;
    background: var(--gts-surface-2) !important;
    border-color: var(--gts-border) !important;
}
[data-baseweb="input"] input, [data-baseweb="base-input"] input, [data-baseweb="select"] {
    color: var(--gts-text) !important; font-size: var(--gts-fs-base) !important;
}
[data-baseweb="input"]:focus-within, [data-baseweb="select"] > div:focus-within {
    border-color: var(--gts-border-focus) !important; box-shadow: var(--gts-ring);
}
[data-baseweb="popover"] [role="listbox"], [data-baseweb="menu"] {
    background: var(--gts-surface) !important;
    border: 1px solid var(--gts-border) !important;
    border-radius: var(--gts-r-md) !important;
    box-shadow: var(--gts-shadow-lg) !important;
}
[data-baseweb="menu"] li { color: var(--gts-text) !important; font-size: var(--gts-fs-base) !important; }
[data-baseweb="menu"] li:hover { background: var(--gts-brand-tint) !important; }
[data-testid="stNumberInputStepUp"], [data-testid="stNumberInputStepDown"] {
    background: var(--gts-surface-2) !important; color: var(--gts-text-muted) !important;
}
[data-testid="stTooltipContent"] {
    background: var(--gts-surface) !important; color: var(--gts-text) !important;
    border: 1px solid var(--gts-border); border-radius: var(--gts-r-md);
    box-shadow: var(--gts-shadow-lg); font-size: var(--gts-fs-sm);
}
[data-testid="stTooltipHoverTarget"] svg { color: var(--gts-text-subtle); }

/* radio rendered as a segmented control */
[data-testid="stRadio"] [role="radiogroup"] { gap: 6px; }
[data-testid="stRadio"] label {
    background: var(--gts-surface-2); border: 1px solid var(--gts-border);
    border-radius: var(--gts-r-md); padding: 7px 13px; margin: 0;
    transition: all var(--gts-dur) var(--gts-ease); cursor: pointer;
}
[data-testid="stRadio"] label:hover { border-color: var(--gts-brand-light); background: var(--gts-brand-tint); }
[data-testid="stRadio"] label p { font-size: var(--gts-fs-base) !important; color: var(--gts-text) !important; }
[data-testid="stCheckbox"] label p { font-size: var(--gts-fs-base) !important; color: var(--gts-text) !important; }

/* ============================ metrics ============================ */
div[data-testid="stMetric"] {
    background: var(--gts-surface-2);
    border: 1px solid var(--gts-border);
    border-radius: var(--gts-r-md);
    padding: 11px 14px;
    transition: border-color var(--gts-dur) var(--gts-ease);
}
div[data-testid="stMetric"]:hover { border-color: var(--gts-border-strong); }
[data-testid="stMetricLabel"] p {
    font-size: var(--gts-fs-xs) !important; font-weight: 600 !important;
    letter-spacing: 0.5px; text-transform: uppercase; color: var(--gts-text-subtle) !important;
}
[data-testid="stMetricValue"] {
    font-size: var(--gts-fs-xl) !important; font-weight: 680 !important;
    color: var(--gts-text) !important; letter-spacing: -0.5px;
}

/* ============================ expander ============================ */
[data-testid="stExpander"] details {
    border: 1px solid var(--gts-border) !important;
    border-radius: var(--gts-r-md) !important;
    background: var(--gts-surface-2) !important;
    overflow: hidden;
}
[data-testid="stExpander"] summary {
    font-size: var(--gts-fs-sm); font-weight: 560; color: var(--gts-text-muted);
    padding: 9px 13px;
}
[data-testid="stExpander"] summary:hover { color: var(--gts-brand-light); background: var(--gts-surface-3); }
[data-testid="stExpander"] [data-testid="stExpanderDetails"] { padding: 4px 13px 13px; }

/* ============================ alerts / status ============================ */
[data-testid="stAlert"] {
    border-radius: var(--gts-r-md); border-width: 1px; border-style: solid;
    padding: 11px 14px; font-size: var(--gts-fs-sm);
}
[data-testid="stAlert"] p { font-size: var(--gts-fs-sm); }
[data-testid="stAlertContentSuccess"] { background: var(--gts-ok-tint); border-color: color-mix(in srgb, var(--gts-ok) 28%, transparent); color: var(--gts-text); }
[data-testid="stAlertContentWarning"] { background: var(--gts-warn-tint); border-color: color-mix(in srgb, var(--gts-warn) 28%, transparent); color: var(--gts-text); }
[data-testid="stAlertContentError"]   { background: var(--gts-danger-tint); border-color: color-mix(in srgb, var(--gts-danger) 28%, transparent); color: var(--gts-text); }
[data-testid="stAlertContentInfo"]    { background: var(--gts-info-tint); border-color: color-mix(in srgb, var(--gts-info) 28%, transparent); color: var(--gts-text); }

[data-testid="stStatusWidget"], [data-testid="stExpander"] { border-radius: var(--gts-r-md); }
[data-testid="stStatus"] {
    border: 1px solid var(--gts-border) !important;
    border-radius: var(--gts-r-md) !important;
    background: var(--gts-surface-2) !important;
}
[data-testid="stProgress"] > div > div { background: var(--gts-surface-3); border-radius: var(--gts-r-full); }
[data-testid="stProgress"] > div > div > div { background: var(--gts-brand-grad); border-radius: var(--gts-r-full); }
[data-testid="stProgress"] p { font-size: var(--gts-fs-sm); color: var(--gts-text-muted); }

/* ============================ media & misc ============================ */
[data-testid="stImage"] img {
    border-radius: var(--gts-r-md); border: 1px solid var(--gts-border);
    background: var(--gts-surface);
}
[data-testid="stCaptionContainer"] p, .stCaption, [data-testid="stCaptionContainer"] {
    font-size: var(--gts-fs-sm) !important; color: var(--gts-text-muted) !important;
}
[data-testid="stDataFrame"] { border-radius: var(--gts-r-md); border: 1px solid var(--gts-border); overflow: hidden; }
code { background: var(--gts-surface-3) !important; color: var(--gts-brand-light) !important;
       border-radius: 5px; padding: 1px 5px; font-size: 0.9em; font-family: var(--gts-font-mono); }

::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--gts-border-strong); border-radius: var(--gts-r-full); border: 3px solid var(--gts-bg); }
::-webkit-scrollbar-thumb:hover { background: var(--gts-text-subtle); }

/* entrance animation for the main column, kept subtle and short */
@keyframes gtsFadeUp { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
[data-testid="stMainBlockContainer"] > div { animation: gtsFadeUp 260ms var(--gts-ease) both; }
@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }
}

/* laptop-width tightening: 1280-1440px screens are the common case here */
@media (max-width: 1440px) {
    [data-testid="stMainBlockContainer"], .block-container { padding-left: 2.2rem; padding-right: 2.2rem; }
    .gts-appbar { padding: 14px 18px; }
    .gts-appbar-title { font-size: var(--gts-fs-lg); }
}
@media (max-width: 1100px) {
    .gts-appbar-meta { display: none; }
    .stTabs [data-baseweb="tab"] { padding: 8px 12px; }
}
"""


def build_css(theme: str = DEFAULT_THEME) -> str:
    """Compose the stylesheet: font import, token variables, then static rules.

    Order matters — ``@import`` is only honoured when it comes before any other
    rule, so it is emitted ahead of the ``:root`` block rather than living in
    :data:`_STATIC_CSS`.
    """
    tokens = TOKENS.get(theme, TOKENS[DEFAULT_THEME])
    root = "".join(f"--gts-{key}:{value};" for key, value in tokens.items())
    return f"<style>{_FONT_IMPORT}:root{{{root}}}{_STATIC_CSS}</style>"


# ------------------------------------------------------------ UI primitives
# Each returns an HTML string; the caller passes it to st.markdown(...,
# unsafe_allow_html=True). Returning strings rather than rendering directly
# keeps them composable (e.g. a stat grid built from several stat cards).

def app_bar(title: str, subtitle: str, logo_b64: str | None = None,
            meta: list[str] | None = None) -> str:
    """Top-of-page identity bar with optional trailing status pills."""
    logo = f'<img class="gts-appbar-logo" src="data:image/png;base64,{logo_b64}" alt="" />' if logo_b64 else ""
    divider = '<div class="gts-appbar-divider"></div>' if logo_b64 else ""
    meta_html = f'<div class="gts-appbar-meta">{"".join(meta)}</div>' if meta else ""
    return _html(f"""
        <div class="gts-appbar">
            {logo}{divider}
            <div>
                <p class="gts-appbar-title">{title}</p>
                <p class="gts-appbar-sub">{subtitle}</p>
            </div>
            <div class="gts-appbar-spacer"></div>
            {meta_html}
        </div>
    """)


def pill(text: str, kind: str = "neutral", glyph: str | None = None) -> str:
    """Small status chip. `kind` is one of ok/warn/danger/info/brand/neutral."""
    g = icon(glyph, size=13) if glyph else ""
    return _html(f'<span class="gts-pill {kind}">{g}{text}</span>')


def section(title: str, subtitle: str | None = None, glyph: str = "circle",
            badge: str | None = None) -> str:
    """Header for a card: icon tile, title, optional subtitle and trailing pill."""
    sub = f'<p class="gts-sec-sub">{subtitle}</p>' if subtitle else ""
    badge_html = badge or ""
    return _html(f"""
        <div class="gts-sec">
            <span class="gts-sec-ico">{icon(glyph, size=18)}</span>
            <div class="gts-sec-body">
                <p class="gts-sec-title">{title}{badge_html}</p>
                {sub}
            </div>
        </div>
    """)


def stat(label: str, value: str, sub: str | None = None, accent: bool = False) -> str:
    """A single KPI tile. Compose several with :func:`stat_grid`."""
    sub_html = f'<p class="gts-stat-sub">{sub}</p>' if sub else ""
    cls = "gts-stat accent" if accent else "gts-stat"
    return _html(f"""
        <div class="{cls}">
            <p class="gts-stat-label">{label}</p>
            <p class="gts-stat-value">{value}</p>
            {sub_html}
        </div>
    """)


def stat_grid(cards: list[str], columns: int = 4) -> str:
    """Lay out stat tiles on a responsive grid that collapses on narrow screens."""
    return _html(
        f'<div class="gts-stats" style="grid-template-columns:repeat({columns}, minmax(0, 1fr));">'
        f'{"".join(cards)}</div>'
    )


def empty_state(title: str, body: str, glyph: str = "package") -> str:
    """Placeholder shown where content will appear once a step is completed."""
    return _html(f"""
        <div class="gts-empty">
            <span class="gts-empty-ico">{icon(glyph, size=22)}</span>
            <p class="gts-empty-title">{title}</p>
            <p class="gts-empty-body">{body}</p>
        </div>
    """)


def result_banner(title: str, subtitle: str) -> str:
    """Success header shown above the downloads once a run finishes."""
    return _html(f"""
        <div class="gts-result">
            <span class="gts-result-ico">{icon("check-circle", size=20)}</span>
            <div>
                <p class="gts-result-title">{title}</p>
                <p class="gts-result-sub">{subtitle}</p>
            </div>
        </div>
    """)


def brand_mark(name: str, sub: str, logo_b64: str | None = None) -> str:
    """Sidebar logo lockup."""
    logo = f'<img src="data:image/png;base64,{logo_b64}" alt="" />' if logo_b64 else ""
    return _html(f"""
        <div class="gts-brand">
            {logo}
            <div>
                <p class="gts-brand-name">{name}</p>
                <p class="gts-brand-sub">{sub}</p>
            </div>
        </div>
    """)


def eyebrow(text: str, glyph: str | None = None) -> str:
    """Uppercase micro-heading used to label sidebar groups."""
    g = icon(glyph, size=12) if glyph else ""
    return _html(f'<p class="gts-eyebrow">{g}{text}</p>')


def readiness_meter(done: int, total: int, ready: bool) -> str:
    """Progress bar over the required inputs, with a ready/incomplete label."""
    pct = int(round(done / total * 100)) if total else 0
    state_text = "Ready to run" if ready else "Incomplete"
    state_color = "var(--gts-ok)" if ready else "var(--gts-text-subtle)"
    fill_cls = "gts-meter-fill ready" if ready else "gts-meter-fill"
    return _html(f"""
        <div class="gts-meter">
            <div class="gts-meter-top">
                <span class="gts-meter-count">{done}<span>/{total} required</span></span>
                <span class="gts-meter-state" style="color:{state_color};">{state_text}</span>
            </div>
            <div class="gts-meter-track">
                <div class="{fill_cls}" style="width:{pct}%;"></div>
            </div>
        </div>
    """)


def checklist(items: list[tuple[str, bool, bool]]) -> str:
    """Render (label, done, required) triples as an input-readiness list."""
    rows = []
    for label, done, required in items:
        if done:
            cls, glyph = "done", "check-circle"
        elif required:
            cls, glyph = "todo", "circle"
        else:
            cls, glyph = "opt", "dot"
        tag = "" if required else '<span class="gts-check-tag">optional</span>'
        rows.append(
            f'<div class="gts-check-row {cls}">'
            f'<span class="gts-check-ico">{icon(glyph, size=15)}</span>'
            f'<span class="gts-check-label">{label}</span>{tag}</div>'
        )
    return _html(f'<div class="gts-check">{"".join(rows)}</div>')
