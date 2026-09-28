"""Theme tests: the two palettes must stay substitutable, or the toggle is not a switch."""

from __future__ import annotations

import re

import pytest

from src import theme

HEX = re.compile(r"^#[0-9a-f]{6}$")


def test_both_palettes_define_every_token_exactly_once() -> None:
    """A theme switch is value substitution; a missing key would silently drop a colour."""
    for name, palette in theme.PALETTES.items():
        assert set(palette) == set(theme.TOKENS), name
        assert len(palette) == len(theme.TOKENS), name


def test_every_token_value_is_a_lowercase_hex_colour() -> None:
    for name, palette in theme.PALETTES.items():
        for token, value in palette.items():
            assert HEX.match(value), f"{name}.{token} = {value!r}"


def test_the_two_themes_actually_differ() -> None:
    differing = [t for t in theme.TOKENS if theme.LIGHT[t] != theme.DARK[t]]
    assert len(differing) > 30
    for token in ("background", "on-background", "surface", "on-surface", "primary"):
        assert theme.LIGHT[token] != theme.DARK[token], token


def test_background_and_foreground_differ_in_both_themes() -> None:
    """A theme whose text matches its background is unreadable, not styled."""
    for name, palette in theme.PALETTES.items():
        assert palette["background"] != palette["on-background"], name
        assert palette["surface"] != palette["on-surface"], name
        assert palette["primary"] != palette["on-primary"], name
        assert palette["error"] != palette["on-error"], name


def test_default_is_light_because_the_demo_is_a_groww_page() -> None:
    assert theme.DEFAULT_THEME is theme.Theme.LIGHT
    assert theme.resolve_theme(None) is theme.Theme.LIGHT


@pytest.mark.parametrize("value", ["light", "LIGHT", " Light ", theme.Theme.DARK])
def test_stored_preferences_are_coerced(value: object) -> None:
    assert theme.resolve_theme(value) in (theme.Theme.LIGHT, theme.Theme.DARK)


@pytest.mark.parametrize("value", ["", "sepia", "dark-mode", 7, [], {}])
def test_junk_preferences_fall_back_instead_of_raising(value: object) -> None:
    """A stale session value must not take the app down on rerun."""
    assert theme.resolve_theme(value) is theme.DEFAULT_THEME


def test_the_control_is_named_for_the_theme_it_switches_to() -> None:
    assert theme.toggle_label(theme.Theme.LIGHT) == "Dark mode"
    assert theme.toggle_label(theme.Theme.DARK) == "Light mode"
    assert theme.choice_for(theme.Theme.LIGHT).other is theme.Theme.DARK
    assert theme.choice_for(theme.Theme.DARK).other is theme.Theme.LIGHT


def test_stylesheet_emits_every_token_for_both_themes() -> None:
    css = theme.stylesheet(theme.Theme.LIGHT)
    for token in theme.TOKENS:
        for name, palette in theme.PALETTES.items():
            assert f"--mf-{token}: {palette[token]};" in css, f"{name}.{token}"
    assert "@media (prefers-color-scheme: light)" in css
    assert "@media (prefers-color-scheme: dark)" in css


def test_stylesheet_applies_the_active_theme_unconditionally() -> None:
    """The media query follows the OS; only this rule lets the in-app control win."""
    light = theme.stylesheet(theme.Theme.LIGHT)
    dark = theme.stylesheet(theme.Theme.DARK)
    assert light != dark
    assert f":root, [data-testid='stAppViewContainer'] {{\n{variables_of(theme.LIGHT)}\n}}" in light
    assert f":root, [data-testid='stAppViewContainer'] {{\n{variables_of(theme.DARK)}\n}}" in dark


def variables_of(palette: dict[str, str]) -> str:
    """Mirror of theme.variables, spelled out here so the test does not trust the source."""
    return "\n".join(f"  --mf-{token}: {palette[token]};" for token in theme.TOKENS)


def test_the_toggle_settles_in_one_step_instead_of_looping() -> None:
    """The sidebar reruns when the toggle disagrees with the theme, so the two must be able to agree.

    The two were crossed once: the stored theme was mapped back through the toggle's sense instead
    of read from it, so each rerun flipped the preference and the next one flipped it back. The app
    pinned a core at 100% and never answered a request, while every palette test stayed green.
    """
    for start in theme.Theme:
        # What app._sidebar computes: the toggle renders `value=` from the current theme, so an
        # untouched widget returns the current theme's own state. The bug was a closed cycle, which
        # never breaks, so assert it breaks rather than merely asserting where it lands.
        current = start
        settled = False
        for _ in range(5):
            toggled = current is theme.Theme.DARK
            chosen = theme.from_toggle(toggled)
            if chosen is current:
                settled = True
                break
            current = chosen
        assert settled, f"{start.value} reruns forever"


def test_stylesheet_is_a_single_injectable_style_block() -> None:
    css = theme.stylesheet()
    assert css.startswith("<style>") and css.rstrip().endswith("</style>")
    assert css.count("<style>") == 1
    assert "{" in css and "}" in css


def test_stylesheet_never_raises_on_a_bad_theme_value() -> None:
    assert theme.stylesheet("nonsense") == theme.stylesheet(theme.DEFAULT_THEME)


def test_base_config_matches_the_requested_theme() -> None:
    light = theme.base_config(theme.Theme.LIGHT)
    dark = theme.base_config(theme.Theme.DARK)
    assert 'base = "light"' in light and 'base = "dark"' in dark
    assert theme.LIGHT["background"] in light
    assert theme.DARK["background"] in dark
    assert theme.LIGHT["primary"] in light and theme.DARK["primary"] in dark


def test_type_scale_radii_and_spacing_are_shared_by_both_themes() -> None:
    """Only colour switches. A dark theme that re-flows layout is a second design to test."""
    assert set(theme.TYPE_SCALE) >= {"headline-xl", "body-md", "label-md", "numeric"}
    for role, (weight, size) in theme.TYPE_SCALE.items():
        assert weight in {"400", "500", "600", "700"}, role
        assert size >= 11, role
    assert theme.RADIUS["full"] == "9999px"
    assert set(theme.SPACING) == {"gutter", "margin", "xs", "sm", "md", "lg", "xl"}
    for token, value in theme.SPACING.items():
        assert value.endswith("rem"), token


def test_the_shell_uses_the_wireframe_geometry() -> None:
    """The two-column wireframe becomes one column plus a 380px sidebar, and the values are named."""
    css = theme.shell_css()
    assert f"[data-testid='stSidebar'] {{ width: {theme.SIDEBAR_WIDTH_PX}px; }}" in css
    assert f"section.main > div {{ max-width: {theme.CONTENT_MAX_WIDTH_PX}px; }}" in css
    assert "position: sticky" in css
    assert "backdrop-filter" in css


def test_the_shell_collapses_to_one_column_on_a_narrow_screen() -> None:
    """Below the breakpoint the sidebar card stack and the stat tiles stack, and the nav unrolls."""
    css = theme.shell_css()
    assert f"@media (max-width: {theme.NARROW_BREAKPOINT_PX}px)" in css
    narrow = css.split(f"@media (max-width: {theme.NARROW_BREAKPOINT_PX}px)")[1]
    assert ".mf-nav-links { display: none; }" in narrow
    assert ".mf-tiles, .mf-rows { grid-template-columns: 1fr; }" in narrow
    assert ".mf-user-bubble { max-width: 100%; }" in narrow


def test_keyboard_focus_is_always_visible() -> None:
    """WCAG 2.4.7: the demo's only controls are links, buttons and the chat box, so all three need it."""
    css = theme.shell_css()
    assert ":focus-visible" in css
    assert "outline: 2px solid var(--mf-primary)" in css
    assert ".mf-skip:focus" in css


def test_the_two_shells_are_identical_so_only_colour_switches() -> None:
    """Geometry in a theme-specific rule would mean the dark theme re-flows the page."""
    assert theme.stylesheet(theme.Theme.LIGHT).count(theme.shell_css()) == 1
    assert theme.stylesheet(theme.Theme.DARK).count(theme.shell_css()) == 1
