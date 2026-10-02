"""Design tokens and the light/dark switch for the UI (architecture.md §16).

Two constraints shape this module. First, a live theme toggle cannot use
`.streamlit/config.toml`: Streamlit reads that theme once at process start, so flipping it needs a
restart. A toggle that survives a rerun has to override the rendered page with CSS custom
properties, which is what `stylesheet()` emits. Second, `app.py` may import only `src.pipeline`,
`src.config`, `src.models` and `src.templates`, so the palettes live here as frozen data and the
UI reads them rather than restating hex values.

The token names come from the two Stitch design directions and are identical across both, so a
theme is a value substitution and not a second schema. Colour is the only thing that changes:
type scale, radii and spacing are shared, because a dark theme that also re-flows the layout is a
second design to test rather than a switch.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

FONT_STACK = "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif"

TOKENS: tuple[str, ...] = (
    "surface",
    "surface-dim",
    "surface-bright",
    "surface-container-lowest",
    "surface-container-low",
    "surface-container",
    "surface-container-high",
    "surface-container-highest",
    "on-surface",
    "on-surface-variant",
    "inverse-surface",
    "inverse-on-surface",
    "outline",
    "outline-variant",
    "surface-tint",
    "primary",
    "on-primary",
    "primary-container",
    "on-primary-container",
    "inverse-primary",
    "secondary",
    "on-secondary",
    "secondary-container",
    "on-secondary-container",
    "tertiary",
    "on-tertiary",
    "tertiary-container",
    "on-tertiary-container",
    "error",
    "on-error",
    "error-container",
    "on-error-container",
    "primary-fixed",
    "primary-fixed-dim",
    "on-primary-fixed",
    "on-primary-fixed-variant",
    "secondary-fixed",
    "secondary-fixed-dim",
    "on-secondary-fixed",
    "on-secondary-fixed-variant",
    "tertiary-fixed",
    "tertiary-fixed-dim",
    "on-tertiary-fixed",
    "on-tertiary-fixed-variant",
    "background",
    "on-background",
    "surface-variant",
)

LIGHT: dict[str, str] = {
    "surface": "#ffffff",
    "surface-dim": "#f0f0f2",
    "surface-bright": "#ffffff",
    "surface-container-lowest": "#ffffff",
    "surface-container-low": "#f7f7f7",
    "surface-container": "#e7e8e9",
    "surface-container-high": "#dcdde1",
    "surface-container-highest": "#c7c8ce",
    "on-surface": "#44475b",
    "on-surface-variant": "#7c7e8c",
    "inverse-surface": "#131313",
    "inverse-on-surface": "#e5e2e1",
    "outline": "#b0b2ba",
    "outline-variant": "#dcdde1",
    "surface-tint": "#04b488",
    "primary": "#04b488",
    "on-primary": "#ffffff",
    "primary-container": "#e9faf3",
    "on-primary-container": "#00533c",
    "inverse-primary": "#2fe0aa",
    "secondary": "#5367ff",
    "on-secondary": "#ffffff",
    "secondary-container": "#eef0ff",
    "on-secondary-container": "#4452d6",
    "tertiary": "#ffb61b",
    "on-tertiary": "#121212",
    "tertiary-container": "#fff5e0",
    "on-tertiary-container": "#a16b00",
    "error": "#ed5533",
    "on-error": "#ffffff",
    "error-container": "#fae9e5",
    "on-error-container": "#d23a15",
    "primary-fixed": "#e9faf3",
    "primary-fixed-dim": "#ddf5ee",
    "on-primary-fixed": "#002116",
    "on-primary-fixed-variant": "#00513b",
    "secondary-fixed": "#eef0ff",
    "secondary-fixed-dim": "#cad1fe",
    "on-secondary-fixed": "#252a44",
    "on-secondary-fixed-variant": "#51578b",
    "tertiary-fixed": "#fff5e0",
    "tertiary-fixed-dim": "#fcefb1",
    "on-tertiary-fixed": "#121212",
    "on-tertiary-fixed-variant": "#a16b00",
    "error": "#ed5533",
    "on-error": "#ffffff",
    "error-container": "#fae9e5",
    "on-error-container": "#d23a15",
    "primary-fixed": "#e9faf3",
    "primary-fixed-dim": "#ddf5ee",
    "on-primary-fixed": "#002116",
    "on-primary-fixed-variant": "#00513b",
    "secondary-fixed": "#eef0ff",
    "secondary-fixed-dim": "#cad1fe",
    "on-secondary-fixed": "#252a44",
    "on-secondary-fixed-variant": "#51578b",
    "tertiary-fixed": "#fff5e0",
    "tertiary-fixed-dim": "#fcefb1",
    "on-tertiary-fixed": "#121212",
    "on-tertiary-fixed-variant": "#a16b00",
    "background": "#f8f8f8",
    "on-background": "#44475b",
    "surface-variant": "#e9e9eb",
}

DARK: dict[str, str] = {
    "surface": "#131313",
    "surface-dim": "#131313",
    "surface-bright": "#393939",
    "surface-container-lowest": "#0e0e0e",
    "surface-container-low": "#1c1b1b",
    "surface-container": "#201f1f",
    "surface-container-high": "#2a2a2a",
    "surface-container-highest": "#353534",
    "on-surface": "#e5e2e1",
    "on-surface-variant": "#bacac1",
    "inverse-surface": "#e5e2e1",
    "inverse-on-surface": "#313030",
    "outline": "#85948c",
    "outline-variant": "#3c4a43",
    "surface-tint": "#2fe0aa",
    "primary": "#44edb7",
    "on-primary": "#003828",
    "primary-container": "#00d09c",
    "on-primary-container": "#00533c",
    "inverse-primary": "#006c4f",
    "secondary": "#50ddad",
    "on-secondary": "#003828",
    "secondary-container": "#01b386",
    "on-secondary-container": "#003d2c",
    "tertiary": "#ffc98a",
    "on-tertiary": "#472a00",
    "tertiary-container": "#fda417",
    "on-tertiary-container": "#673f00",
    "error": "#ffb4ab",
    "on-error": "#690005",
    "error-container": "#93000a",
    "on-error-container": "#ffdad6",
    "primary-fixed": "#59fdc5",
    "primary-fixed-dim": "#2fe0aa",
    "on-primary-fixed": "#002116",
    "on-primary-fixed-variant": "#00513b",
    "secondary-fixed": "#71fac8",
    "secondary-fixed-dim": "#50ddad",
    "on-secondary-fixed": "#002116",
    "on-secondary-fixed-variant": "#003d2c",
    "tertiary-fixed": "#ffddb8",
    "tertiary-fixed-dim": "#ffb95f",
    "on-tertiary-fixed": "#2a1700",
    "on-tertiary-fixed-variant": "#653e00",
    "background": "#131313",
    "on-background": "#e5e2e1",
    "surface-variant": "#353534",
}

RADIUS: dict[str, str] = {
    "sm": "0.25rem",
    "md": "0.5rem",
    "lg": "0.75rem",
    "xl": "1rem",
    "full": "9999px",
}

SPACING: dict[str, str] = {
    "gutter": "1rem",
    "margin": "1rem",
    "xs": "0.25rem",
    "sm": "0.5rem",
    "md": "0.75rem",
    "lg": "1rem",
    "xl": "1.5rem",
}

TYPE_SCALE: dict[str, tuple[str, int]] = {
    "headline-xl": ("700", 32),
    "headline-lg": ("600", 24),
    "headline-md": ("600", 20),
    "headline-sm": ("600", 18),
    "body-lg": ("400", 16),
    "body-md": ("400", 14),
    "body-sm": ("400", 12),
    "label-lg": ("600", 14),
    "label-md": ("500", 12),
    "label-sm": ("500", 11),
    "numeric": ("600", 22),
}

SIDEBAR_WIDTH_REM = 21
CONTENT_MAX_WIDTH_REM = 56


class Theme(str, Enum):
    """The two supported appearances. The value is what lands in session state."""

    LIGHT = "light"
    DARK = "dark"


DEFAULT_THEME = Theme.LIGHT

PALETTES: dict[Theme, dict[str, str]] = {Theme.LIGHT: LIGHT, Theme.DARK: DARK}


@dataclass(frozen=True)
class ThemeChoice:
    """A resolved theme selection, ready to hand to `stylesheet()`."""

    theme: Theme
    label: str
    toggled: bool

    @property
    def other(self) -> Theme:
        """Return the theme the control switches to, which is the inverse of this one."""
        return Theme.DARK if self.theme is Theme.LIGHT else Theme.LIGHT


def resolve_theme(value: str | Theme | None) -> Theme:
    """Coerce a stored preference into a Theme, defaulting rather than raising on junk."""
    if isinstance(value, Theme):
        return value
    if isinstance(value, str):
        try:
            return Theme(value.strip().lower())
        except ValueError:
            return DEFAULT_THEME
    return DEFAULT_THEME


def toggle_label(theme: Theme) -> str:
    """Return the caption for the control, named for the theme it switches *to*."""
    return "Dark mode" if theme is Theme.LIGHT else "Light mode"


def choice_for(theme: Theme, toggled: bool = False) -> ThemeChoice:
    """Build the control description for a theme, as persisted in session state."""
    return ThemeChoice(theme=theme, label=toggle_label(theme), toggled=toggled)


def from_toggle(toggled: bool) -> Theme:
    """Return the theme a toggle widget's value means: on is dark, off is light.

    The sidebar reruns whenever this disagrees with the stored preference, so the two must be able
    to agree. They were crossed once — the stored theme was mapped back through the toggle's sense
    instead of read from it — and the app rerendered itself forever, pinning a core at 100% while
    accepting requests it never answered. `toggled` is the widget's answer to "is dark mode on", so
    that question is what this returns the consequence of, in one place.
    """
    return Theme.DARK if toggled else Theme.LIGHT


def variables(palette: dict[str, str]) -> str:
    """Render a palette as CSS custom property declarations, in token order."""
    return "\n".join(f"  --mf-{token}: {palette[token]};" for token in TOKENS)


NAV_HEIGHT_PX = 64
SIDEBAR_WIDTH_PX = 380
CONTENT_MAX_WIDTH_PX = 720
NARROW_BREAKPOINT_PX = 900


def shell_css() -> str:
    """Return the layout rules that build the wireframe's shell around Streamlit's own widgets.

    Streamlit renders the page in a fixed order, so a two-column wireframe is approximated rather
    than reproduced: the top nav and breadcrumb are drawn as ordinary blocks at the top of the main
    column, the chat keeps the left two thirds, and the wireframe's right column is served by
    Streamlit's native sidebar, restyled below to match its card. The rules are deliberately
    declarative and token-driven, so both themes get the same geometry from one rule set.
    """
    return "\n".join(
        [
            f"body {{ padding-top: {NAV_HEIGHT_PX}px; }}",
            f"section.main > div {{ max-width: {CONTENT_MAX_WIDTH_PX}px; }}",
            ".mf-nav { position: sticky; top: 0; z-index: 999; margin: -1rem -1rem 0; padding: 0.75rem 1rem;",
            "  background: color-mix(in srgb, var(--mf-surface) 90%, transparent);",
            "  backdrop-filter: blur(12px); border-bottom: 1px solid var(--mf-outline-variant); }",
            ".mf-nav-row { display: flex; align-items: center; gap: 1rem; }",
            ".mf-brand { display: flex; align-items: center; gap: 0.5rem;",
            "  font-weight: 700; font-size: 18px; color: var(--mf-on-surface); }",
            ".mf-brand-mark { display: grid; place-items: center; width: 28px; height: 28px;",
            "  border-radius: 8px; background: var(--mf-primary-container); color: #04231a;",
            "  font-weight: 800; }",
            ".mf-nav-links { display: flex; gap: 0.25rem; margin-left: auto; align-items: center; }",
            ".mf-nav-link { padding: 0.25rem 0.75rem; border-radius: 8px; font-size: 14px;",
            "  color: var(--mf-on-surface-variant); }",
            ".mf-nav-link.is-active { font-weight: 600; color: var(--mf-on-surface);",
            "  background: var(--mf-surface-container-low); }",
            ".mf-crumb { font-size: 12px; color: var(--mf-on-surface-variant); padding: 0.5rem 0; }",
            ".mf-card { border: 1px solid var(--mf-outline-variant); border-radius: 12px;",
            "  padding: 1rem; margin-bottom: 1rem; background: var(--mf-surface-container-lowest);",
            "  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04); }",
            ".mf-card-head { display: flex; align-items: flex-start; gap: 0.75rem; }",
            ".mf-avatar { display: grid; place-items: center; flex: 0 0 auto; width: 36px; height: 36px;",
            "  border-radius: 10px; background: var(--mf-primary-container); color: #04231a;",
            "  font-weight: 700; }",
            ".mf-title { font-size: 18px; font-weight: 600; color: var(--mf-on-surface); }",
            ".mf-sub { font-size: 12px; color: var(--mf-on-surface-variant); }",
            ".mf-badge { display: inline-flex; align-items: center; gap: 0.25rem; padding: 0.15rem 0.5rem;",
            "  border-radius: 9999px; font-size: 11px; font-weight: 500; line-height: 1.6;",
            "  background: var(--mf-surface-container-high); color: var(--mf-on-surface-variant);",
            "  border: 1px solid var(--mf-outline-variant); }",
            ".mf-badge.is-ok { background: var(--mf-primary-container); color: var(--mf-on-primary-container);",
            "  border-color: transparent; }",
            ".mf-badge.is-warn { background: var(--mf-tertiary-container);",
            "  color: var(--mf-on-tertiary-container); border-color: transparent; }",
            ".mf-badge.is-error { background: var(--mf-error-container);",
            "  color: var(--mf-on-error-container); border-color: transparent; }",
            ".mf-note { border-left: 3px solid var(--mf-tertiary-container);",
            "  background: var(--mf-surface-container-low); border-radius: 8px; padding: 0.75rem;",
            "  font-size: 13px; color: var(--mf-on-surface-variant); }",
            ".mf-chips { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 1rem; }",
            ".mf-chip { border: 1px solid var(--mf-outline-variant); border-radius: 9999px;",
            "  background: var(--mf-surface-container-lowest); color: var(--mf-on-surface-variant);",
            "  font-size: 12px; padding: 0.3rem 0.8rem; }",
            ".mf-chip.is-active { background: var(--mf-on-surface); color: var(--mf-surface);",
            "  border-color: var(--mf-on-surface); }",
            ".mf-tiles { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; margin: 0.75rem 0; }",
            ".mf-tile { border: 1px solid var(--mf-outline-variant); border-radius: 12px; padding: 0.75rem;",
            "  background: var(--mf-surface-container-lowest); }",
            ".mf-tile.is-warn { border-color: var(--mf-tertiary-container);",
            "  background: color-mix(in srgb, var(--mf-tertiary-container) 18%, transparent); }",
            ".mf-tile.is-ok { border-color: var(--mf-primary-container);",
            "  background: color-mix(in srgb, var(--mf-primary-container) 15%, transparent); }",
            ".mf-tile-label { font-size: 12px; color: var(--mf-on-surface-variant); }",
            ".mf-tile-value { font-size: 18px; font-weight: 600; color: var(--mf-on-surface);",
            "  font-variant-numeric: tabular-nums; }",
            ".mf-tile-note { font-size: 11px; color: var(--mf-on-surface-variant); }",
            ".mf-cite { display: flex; gap: 0.5rem; border: 1px solid var(--mf-outline-variant);",
            "  border-radius: 10px; padding: 0.6rem 0.75rem; margin: 0.6rem 0;",
            "  background: var(--mf-surface-container-low); }",
            ".mf-cite-loc { font-size: 11px; color: var(--mf-on-surface-variant); }",
            ".mf-meta { display: flex; align-items: center; gap: 0.5rem; font-size: 11px;",
            "  color: var(--mf-on-surface-variant); }",
            ".mf-risk { display: flex; gap: 3px; margin-top: 0.4rem; }",
            ".mf-risk-seg { flex: 1; height: 8px; border-radius: 4px;",
            "  background: var(--mf-surface-container-high); }",
            ".mf-risk-seg.is-on { background: var(--mf-tertiary-container); }",
            ".mf-rows { display: grid; grid-template-columns: 1fr 1fr; gap: 0.4rem 0.75rem;",
            "  font-size: 12px; }",
            ".mf-rows dt { color: var(--mf-on-surface-variant); }",
            ".mf-rows dd { margin: 0; text-align: right; color: var(--mf-on-surface);",
            "  font-variant-numeric: tabular-nums; }",
            ".mf-src { display: flex; align-items: center; gap: 0.5rem; font-size: 12px;",
            "  padding: 0.4rem 0; border-bottom: 1px solid var(--mf-outline-variant);",
            "  color: var(--mf-on-surface-variant); }",
            ".mf-foot { border-top: 1px solid var(--mf-outline-variant); margin-top: 2rem;",
            "  padding-top: 1rem; font-size: 11px; color: var(--mf-on-surface-variant); }",
            ".mf-user { display: flex; justify-content: flex-end; }",
            ".mf-user-bubble { background: var(--mf-primary-container); color: var(--mf-on-primary-container);",
            "  border-radius: 12px; padding: 0.5rem 0.9rem; max-width: 80%; font-size: 14px; }",
            f"@media (max-width: {NARROW_BREAKPOINT_PX}px) {{",
            "  .mf-nav { position: static; }",
            "  .mf-nav-links { display: none; }",
            "  .mf-crumb { font-size: 11px; }",
            "  .mf-tiles, .mf-rows { grid-template-columns: 1fr; }",
            "  .mf-card { padding: 0.75rem; }",
            "  .mf-user-bubble { max-width: 100%; }",
            "  [data-testid='stAppViewContainer'] { padding-left: 0.75rem; padding-right: 0.75rem; }",
            "}",
            "a:focus-visible, button:focus-visible, [data-testid='stChatInput'] :focus-within {",
            "  outline: 2px solid var(--mf-primary); outline-offset: 2px; }",
            ".mf-skip { position: absolute; left: -9999px; }",
            ".mf-skip:focus { position: static; left: 0; padding: 0.5rem;",
            "  background: var(--mf-primary-container); color: var(--mf-on-primary-container);",
            "  border-radius: 8px; z-index: 1000; }",
            "[data-testid='stChatInput'] textarea { font-size: 14px; }",
            "[data-testid='stChatInput'] { border-radius: 12px; }",
        ]
    )


def stylesheet(theme: Theme | str | None = None) -> str:
    """Return the CSS that paints the page in one theme, safe to inject on every rerun.

    Streamlit's own theme is fixed for the process, so this overrides the rendered widget
    surfaces, the chat bubbles and the sidebar with the palette's tokens. Both themes are emitted
    into `prefers-color-scheme` blocks and the active one is additionally applied unconditionally:
    the media query keeps the page honest if the OS flips while the app is open, and the
    unconditional block is what makes the in-app control authoritative.

    Every region of the page is pinned to `--mf-background` on purpose, including the fixed strip
    at the bottom that holds the chat box. Streamlit paints that strip with its own
    `--secondary-background-color`, which is unrelated to the palette, and the result is a band of
    a second colour across the foot of a page that is otherwise one colour — it reads as a broken
    page rather than as an input area.
    """
    active = resolve_theme(theme)
    declarations = "\n".join(
        [
            f":root, [data-testid='stAppViewContainer'] {{\n{variables(PALETTES[active])}\n}}",
            f"[data-testid='stAppViewContainer'] {{\n  background: var(--mf-background);\n"
            f"  color: var(--mf-on-surface);\n  font-family: {FONT_STACK};\n}}",
            f".stApp, [data-testid='stSidebar'] {{\n  background: var(--mf-surface);\n"
            f"  color: var(--mf-on-surface);\n}}",
            "[data-testid='stSidebar'] { border-right: 1px solid var(--mf-outline-variant); }",
            "section.main, [data-testid='stMain'], [data-testid='stMainBlockContainer'] {",
            "  background: var(--mf-background); }",
            "[data-testid='stBottom'], [data-testid='stBottom'] > div,",
            "[data-testid='stBottom'] form, [data-testid='stBottom'] .stChatInput {",
            "  background: var(--mf-background); }",
            "[data-testid='stChatInput'] { border-color: var(--mf-outline-variant);",
            "  background: var(--mf-surface-container-lowest); }",
            "[data-testid='stExpander'] details { border-color: var(--mf-outline-variant); }",
            "[data-testid='stExpander'] summary:hover { background: var(--mf-surface-container-low); }",
            f"h1, h2, h3 {{ font-family: {FONT_STACK}; letter-spacing: -0.02em; }}",
            f".mf-numeric {{ font-family: {FONT_STACK}; font-weight: 600; "
            f"font-size: {TYPE_SCALE['numeric'][1]}px; }}",
            shell_css(),
        ]
    )
    media = "\n".join(
        f"@media (prefers-color-scheme: {candidate.value}) {{\n"
        f":root, [data-testid='stAppViewContainer'] {{\n{variables(PALETTES[candidate])}\n}}\n}}"
        for candidate in Theme
    )
    return f"<style>\n{media}\n{declarations}\n</style>"


def base_config(default: Theme = DEFAULT_THEME) -> str:
    """Return the `.streamlit/config.toml` body matching a theme, for the process default.

    The toggle does not use this — it cannot, mid-process — but the file is what stops the first
    paint flashing the wrong background before any script runs, which is what a projector sees.
    """
    palette = PALETTES[resolve_theme(default)]
    background = palette["background"]
    foreground = palette["on-surface"]
    return (
        "[theme]\n"
        f'base = "{default.value}"\n'
        f'backgroundColor = "{background}"\n'
        f'secondaryBackgroundColor = "{palette["surface-container-low"]}"\n'
        f'textColor = "{foreground}"\n'
        f'primaryColor = "{palette["primary"]}"\n'
    )
