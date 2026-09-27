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
    "surface": "#f8f9ff",
    "surface-dim": "#cbdbf5",
    "surface-bright": "#f8f9ff",
    "surface-container-lowest": "#ffffff",
    "surface-container-low": "#eff4ff",
    "surface-container": "#e5eeff",
    "surface-container-high": "#dce9ff",
    "surface-container-highest": "#d3e4fe",
    "on-surface": "#0b1c30",
    "on-surface-variant": "#3c4a43",
    "inverse-surface": "#213145",
    "inverse-on-surface": "#eaf1ff",
    "outline": "#6b7b72",
    "outline-variant": "#bacac1",
    "surface-tint": "#006c4f",
    "primary": "#006c4f",
    "on-primary": "#ffffff",
    "primary-container": "#00d09c",
    "on-primary-container": "#00533c",
    "inverse-primary": "#2fe0aa",
    "secondary": "#545f73",
    "on-secondary": "#ffffff",
    "secondary-container": "#d5e0f8",
    "on-secondary-container": "#586377",
    "tertiary": "#855300",
    "on-tertiary": "#ffffff",
    "tertiary-container": "#fda417",
    "on-tertiary-container": "#673f00",
    "error": "#ba1a1a",
    "on-error": "#ffffff",
    "error-container": "#ffdad6",
    "on-error-container": "#93000a",
    "primary-fixed": "#59fdc5",
    "primary-fixed-dim": "#2fe0aa",
    "on-primary-fixed": "#002116",
    "on-primary-fixed-variant": "#00513b",
    "secondary-fixed": "#d8e3fb",
    "secondary-fixed-dim": "#bcc7de",
    "on-secondary-fixed": "#111c2d",
    "on-secondary-fixed-variant": "#3c475a",
    "tertiary-fixed": "#ffddb8",
    "tertiary-fixed-dim": "#ffb95f",
    "on-tertiary-fixed": "#2a1700",
    "on-tertiary-fixed-variant": "#653e00",
    "background": "#f8f9ff",
    "on-background": "#0b1c30",
    "surface-variant": "#d3e4fe",
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


def variables(palette: dict[str, str]) -> str:
    """Render a palette as CSS custom property declarations, in token order."""
    return "\n".join(f"  --mf-{token}: {palette[token]};" for token in TOKENS)


def stylesheet(theme: Theme | str | None = None) -> str:
    """Return the CSS that paints the page in one theme, safe to inject on every rerun.

    Streamlit's own theme is fixed for the process, so this overrides the rendered widget
    surfaces, the chat bubbles and the sidebar with the palette's tokens. Both themes are emitted
    into `prefers-color-scheme` blocks and the active one is additionally applied unconditionally:
    the media query keeps the page honest if the OS flips while the app is open, and the
    unconditional block is what makes the in-app control authoritative.
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
            "[data-testid='stChatMessage'] { color: var(--mf-on-surface); }",
            "[data-testid='stChatInput'] { border-color: var(--mf-outline-variant); }",
            "[data-testid='stExpander'] details { border-color: var(--mf-outline-variant); }",
            "[data-testid='stExpander'] summary:hover { background: var(--mf-surface-container-low); }",
            f"h1, h2, h3 {{ font-family: {FONT_STACK}; letter-spacing: -0.02em; }}",
            f".mf-numeric {{ font-family: {FONT_STACK}; font-weight: 600; "
            f"font-size: {TYPE_SCALE['numeric'][1]}px; }}",
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
