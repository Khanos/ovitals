"""Load the active Omarchy palette and expose it as application CSS."""

from __future__ import annotations

from pathlib import Path
import tomllib

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402


DEFAULTS = {
    "mode": "dark",
    "accent": "#7aa2f7",
    "selection": "#283457",
    "muted": "#27324a",
    "background": "#10131c",
    "dark_background": "#0b0e14",
    "darker_background": "#07090e",
    "lighter_background": "#171b27",
    "foreground": "#d9e0ee",
    "dark_foreground": "#737b98",
    "light_foreground": "#a9b1d6",
    "red": "#f7768e",
    "yellow": "#e0af68",
    "green": "#9ece6a",
    "cyan": "#7dcfff",
    "blue": "#7aa2f7",
}


class OmarchyTheme:
    def __init__(self) -> None:
        state = Path.home() / ".local/state/omarchy/current"
        self.colors_path = state / "theme/colors.toml"
        self.name_path = state / "theme.name"
        self.provider: Gtk.CssProvider | None = None
        self.signature: tuple[int, int] | None = None
        self.name = "Omarchy"
        self.colors = dict(DEFAULTS)

    def _file_signature(self) -> tuple[int, int]:
        def stamp(path: Path) -> int:
            try:
                return path.stat().st_mtime_ns
            except OSError:
                return 0

        return stamp(self.colors_path), stamp(self.name_path)

    def refresh(self, force: bool = False) -> bool:
        signature = self._file_signature()
        if not force and signature == self.signature:
            return False
        self.signature = signature
        colors = dict(DEFAULTS)
        try:
            loaded = tomllib.loads(self.colors_path.read_text(encoding="utf-8"))
            colors.update({key: value for key, value in loaded.items() if isinstance(value, str)})
        except (OSError, tomllib.TOMLDecodeError):
            pass
        try:
            slug = self.name_path.read_text(encoding="utf-8").strip()
            self.name = slug.replace("-", " ").title() or "Omarchy"
        except OSError:
            self.name = "Omarchy"
        self.colors = colors
        self._apply()
        return True

    def _apply(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        if self.provider is not None:
            Gtk.StyleContext.remove_provider_for_display(display, self.provider)
        provider = Gtk.CssProvider()
        provider.load_from_string(self._css())
        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self.provider = provider
        manager = Adw.StyleManager.get_default()
        manager.set_color_scheme(
            Adw.ColorScheme.FORCE_LIGHT
            if self.colors["mode"] == "light"
            else Adw.ColorScheme.FORCE_DARK
        )

    def _css(self) -> str:
        c = self.colors
        return f"""
@define-color accent_color {c['accent']};
@define-color accent_bg_color {c['accent']};
@define-color accent_fg_color {c['darker_background']};
@define-color window_bg_color {c['background']};
@define-color window_fg_color {c['foreground']};
@define-color view_bg_color {c['background']};
@define-color view_fg_color {c['foreground']};
@define-color headerbar_bg_color {c['dark_background']};
@define-color headerbar_fg_color {c['foreground']};
@define-color card_bg_color {c['lighter_background']};
@define-color card_fg_color {c['foreground']};
@define-color popover_bg_color {c['lighter_background']};
@define-color popover_fg_color {c['foreground']};

.vitals-root {{
  background: {c['background']};
  color: {c['foreground']};
}}

.vitals-header {{
  background: {c['dark_background']};
  border-bottom: 1px solid alpha({c['muted']}, .65);
  box-shadow: none;
}}

.app-mark {{
  min-width: 28px;
  min-height: 28px;
  border-radius: 9px;
  color: {c['darker_background']};
  background: {c['accent']};
  font-weight: 900;
  font-family: monospace;
}}

.title-label {{ font-size: 16px; font-weight: 750; }}
.subtitle-label {{ color: {c['dark_foreground']}; font-size: 11px; }}
.summary-strip {{ padding: 18px 20px 14px; }}

.metric-card {{
  background: {c['lighter_background']};
  border: 1px solid alpha({c['muted']}, .72);
  border-radius: 12px;
  padding: 14px 15px;
}}

.metric-title {{
  color: {c['dark_foreground']};
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 1.1px;
}}
.metric-value {{ font-family: monospace; font-size: 25px; font-weight: 750; }}
.metric-unit {{ color: {c['light_foreground']}; font-size: 12px; font-weight: 650; }}
.metric-detail {{ color: {c['dark_foreground']}; font-size: 11px; }}
.metric-temperature .metric-value {{ color: {c['yellow']}; }}
.metric-frequency .metric-value {{ color: {c['cyan']}; }}
.metric-voltage .metric-value {{ color: {c['blue']}; }}
.metric-fan .metric-value {{ color: {c['green']}; }}

.filter-bar {{ padding: 0 20px 12px; }}
.filter-button {{
  background: transparent;
  color: {c['dark_foreground']};
  border: 0;
  border-radius: 8px;
  box-shadow: none;
  padding: 6px 11px;
}}
.filter-button:hover {{ background: alpha({c['selection']}, .55); color: {c['foreground']}; }}
.filter-button:checked {{ background: {c['selection']}; color: {c['foreground']}; }}

.table-shell {{
  margin: 0 20px 18px;
  background: {c['dark_background']};
  border: 1px solid alpha({c['muted']}, .72);
  border-radius: 12px;
}}
.table-head {{
  padding: 9px 14px;
  color: {c['dark_foreground']};
  font-size: 10px;
  font-weight: 800;
  letter-spacing: .8px;
  border-bottom: 1px solid alpha({c['muted']}, .72);
}}
.section-head {{
  padding: 11px 14px 7px;
  color: {c['light_foreground']};
  font-size: 11px;
  font-weight: 800;
  letter-spacing: .8px;
}}
.sensor-row {{
  padding: 8px 14px;
  border-top: 1px solid alpha({c['muted']}, .38);
}}
.sensor-row:hover {{ background: alpha({c['selection']}, .30); }}
.sensor-name {{ color: {c['light_foreground']}; font-size: 12px; }}
.sensor-value {{ font-family: monospace; font-size: 12px; font-feature-settings: "tnum"; }}
.sensor-current {{ color: {c['foreground']}; font-weight: 700; }}
.sensor-min {{ color: {c['dark_foreground']}; }}
.sensor-max {{ color: {c['light_foreground']}; }}
.sensor-unit {{ color: {c['dark_foreground']}; font-size: 10px; }}
.sensor-glyph {{ color: {c['dark_foreground']}; font-family: monospace; }}
.sensor-hot {{ color: {c['yellow']}; }}
.sensor-critical {{ color: {c['red']}; font-weight: 800; }}

.status-footer {{
  padding: 0 20px 14px;
  color: {c['dark_foreground']};
  font-size: 10px;
}}
.live-dot {{ color: {c['green']}; font-size: 13px; }}
.empty-state {{ color: {c['dark_foreground']}; padding: 50px; }}
.empty-state .title {{ color: {c['foreground']}; }}
"""
