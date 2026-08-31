"""GTK application UI for Omarchy Vitals."""

from __future__ import annotations

from collections import defaultdict
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .sensors import SensorKind, SensorManager, SensorReading, format_value
from .theme import OmarchyTheme


KIND_LABELS = {
    SensorKind.TEMPERATURE: "Temperatures",
    SensorKind.FREQUENCY: "Clock speeds",
    SensorKind.VOLTAGE: "Voltages",
    SensorKind.FAN: "Fan speeds",
}
KIND_ICONS = {
    SensorKind.TEMPERATURE: "◇",
    SensorKind.FREQUENCY: "⌁",
    SensorKind.VOLTAGE: "ϟ",
    SensorKind.FAN: "✣",
}


class VitalMark(Gtk.DrawingArea):
    """Small theme-colored heartbeat mark for the compact header."""

    def __init__(self) -> None:
        super().__init__()
        self.set_content_width(28)
        self.set_content_height(20)
        self.add_css_class("vital-mark")
        self.set_draw_func(self._draw)

    def _draw(self, _area: Gtk.DrawingArea, context: object, width: int, height: int) -> None:
        color = self.get_color()
        context.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        context.set_line_width(2.2)
        points = (
            (1, height * 0.58),
            (width * 0.22, height * 0.58),
            (width * 0.33, height * 0.18),
            (width * 0.48, height * 0.84),
            (width * 0.62, height * 0.36),
            (width * 0.72, height * 0.58),
            (width - 1, height * 0.58),
        )
        context.move_to(*points[0])
        for point in points[1:]:
            context.line_to(*point)
        context.stroke()


class MetricCard(Gtk.Box):
    def __init__(self, kind: SensorKind) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.kind = kind
        self.add_css_class("metric-card")
        self.add_css_class(f"metric-{kind.value}")
        self.set_hexpand(True)
        title = Gtk.Label(label=KIND_LABELS[kind].upper(), xalign=0)
        title.add_css_class("metric-title")
        self.append(title)
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        line.set_valign(Gtk.Align.BASELINE)
        self.value = Gtk.Label(label="—", xalign=0)
        self.value.add_css_class("metric-value")
        self.unit = Gtk.Label(label="", xalign=0)
        self.unit.add_css_class("metric-unit")
        line.append(self.value)
        line.append(self.unit)
        self.append(line)
        self.detail = Gtk.Label(label="Not reported by hardware", xalign=0, ellipsize=3)
        self.detail.add_css_class("metric-detail")
        self.append(self.detail)

    def update(self, readings: list[SensorReading]) -> None:
        available = [reading for reading in readings if reading.stats is not None]
        if not available:
            self.value.set_label("—")
            self.unit.set_label("")
            self.detail.set_label("Not reported by hardware")
            return
        selected = max(available, key=lambda reading: reading.stats.current)  # type: ignore[union-attr]
        stats = selected.stats
        assert stats is not None
        self.value.set_label(format_value(stats.current, selected.spec.unit))
        self.unit.set_label(selected.spec.unit)
        self.detail.set_label(selected.spec.name)


class SensorRow(Gtk.Grid):
    def __init__(self, reading: SensorReading) -> None:
        super().__init__(column_spacing=10)
        self.spec = reading.spec
        self.add_css_class("sensor-row")
        self.set_column_homogeneous(False)

        glyph = Gtk.Label(label=KIND_ICONS[self.spec.kind], xalign=0)
        glyph.add_css_class("sensor-glyph")
        glyph.set_size_request(18, -1)
        self.attach(glyph, 0, 0, 1, 1)

        name = Gtk.Label(label=self.spec.name, xalign=0, ellipsize=3)
        name.set_hexpand(True)
        name.add_css_class("sensor-name")
        self.attach(name, 1, 0, 1, 1)

        self.current = self._value_label("sensor-current")
        self.minimum = self._value_label("sensor-min")
        self.maximum = self._value_label("sensor-max")
        self.attach(self.current, 2, 0, 1, 1)
        self.attach(self.minimum, 3, 0, 1, 1)
        self.attach(self.maximum, 4, 0, 1, 1)
        self.update(reading)

    @staticmethod
    def _value_label(css_class: str) -> Gtk.Label:
        label = Gtk.Label(label="—", xalign=1)
        label.set_size_request(108, -1)
        label.add_css_class("sensor-value")
        label.add_css_class(css_class)
        return label

    def update(self, reading: SensorReading) -> None:
        stats = reading.stats
        for css_class in ("sensor-hot", "sensor-critical"):
            self.current.remove_css_class(css_class)
        if stats is None:
            values = ("—", "—", "—")
        else:
            values = tuple(
                f"{format_value(value, self.spec.unit)} {self.spec.unit}"
                for value in (stats.current, stats.minimum, stats.maximum)
            )
            if self.spec.kind == SensorKind.TEMPERATURE:
                if stats.current >= 95:
                    self.current.add_css_class("sensor-critical")
                elif stats.current >= 80:
                    self.current.add_css_class("sensor-hot")
        self.current.set_label(values[0])
        self.minimum.set_label(values[1])
        self.maximum.set_label(values[2])


class Section(Gtk.Box):
    def __init__(self, name: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.name = name
        self.rows: list[SensorRow] = []
        self.heading = Gtk.Label(label=name.upper(), xalign=0)
        self.heading.add_css_class("section-head")
        self.append(self.heading)

    def add_reading(self, reading: SensorReading) -> SensorRow:
        row = SensorRow(reading)
        self.rows.append(row)
        self.append(row)
        return row

class VitalsWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application, title="oVitals")
        self.set_default_size(980, 720)
        self.set_size_request(720, 520)
        self.manager = SensorManager()
        self.theme = OmarchyTheme()
        self.readings = self.manager.sample()
        self.rows: dict[str, SensorRow] = {}
        self.metric_cards: dict[SensorKind, MetricCard] = {}
        self.session_started = time.monotonic()
        self.timer_id = 0

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)
        self.toast_overlay.set_child(self._build_content())
        self.theme.refresh(force=True)
        self._update(self.readings)
        self.timer_id = GLib.timeout_add(1000, self._on_tick)

    def _build_content(self) -> Gtk.Widget:
        toolbar = Adw.ToolbarView()
        toolbar.add_css_class("vitals-root")
        toolbar.add_top_bar(self._build_header())

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(self._build_summary())
        page.append(self._build_table())
        page.append(self._build_footer())
        toolbar.set_content(page)
        return toolbar

    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()
        header.add_css_class("vitals-header")
        header.set_title_widget(Gtk.Box())
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title_box.set_valign(Gtk.Align.CENTER)
        title_box.append(VitalMark())
        title = Gtk.Label(label="oVitals", xalign=0)
        title.add_css_class("title-label")
        title_box.append(title)
        header.pack_start(title_box)

        reset = Gtk.Button(icon_name="view-refresh-symbolic")
        reset.set_tooltip_text("Reset minimum and maximum values")
        reset.connect("clicked", self._reset_stats)
        header.pack_end(reset)

        return header

    def _build_summary(self) -> Gtk.Widget:
        summary = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        summary.add_css_class("summary-strip")
        for kind in SensorKind:
            card = MetricCard(kind)
            self.metric_cards[kind] = card
            summary.append(card)
        return summary

    @staticmethod
    def _column_header(label: str, xalign: float, width: int = -1) -> Gtk.Label:
        widget = Gtk.Label(label=label, xalign=xalign)
        if width > 0:
            widget.set_size_request(width, -1)
        return widget

    def _build_table(self) -> Gtk.Widget:
        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        shell.add_css_class("table-shell")
        header = Gtk.Grid(column_spacing=10)
        header.add_css_class("table-head")
        header.attach(self._column_header("", 0, 18), 0, 0, 1, 1)
        sensor = self._column_header("READING", 0)
        sensor.set_hexpand(True)
        header.attach(sensor, 1, 0, 1, 1)
        header.attach(self._column_header("CURRENT", 1, 108), 2, 0, 1, 1)
        header.attach(self._column_header("MINIMUM", 1, 108), 3, 0, 1, 1)
        header.attach(self._column_header("MAXIMUM", 1, 108), 4, 0, 1, 1)
        shell.append(header)

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        contents = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroller.set_child(contents)
        shell.append(scroller)

        grouped: dict[str, list[SensorReading]] = defaultdict(list)
        for reading in self.readings:
            grouped[reading.spec.section].append(reading)
        for section_name, readings in grouped.items():
            section = Section(section_name)
            contents.append(section)
            for reading in readings:
                self.rows[reading.spec.sensor_id] = section.add_reading(reading)
        return shell

    def _build_footer(self) -> Gtk.Widget:
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        footer.add_css_class("status-footer")
        self.live_dot = Gtk.Label(label="●")
        self.live_dot.add_css_class("live-dot")
        footer.append(self.live_dot)
        self.status = Gtk.Label(label="Sampling every 1 second", xalign=0)
        footer.append(self.status)
        spacer = Gtk.Box(hexpand=True)
        footer.append(spacer)
        self.session = Gtk.Label(label="Session 00:00", xalign=1)
        footer.append(self.session)
        return footer

    def _reset_stats(self, _button: Gtk.Button) -> None:
        self.manager.reset()
        self.session_started = time.monotonic()
        self.readings = self.manager.sample()
        self._update(self.readings)
        self.toast_overlay.add_toast(Adw.Toast(title="Minimum and maximum values reset"))

    def _on_tick(self) -> bool:
        self.theme.refresh()
        self.readings = self.manager.sample()
        self._update(self.readings)
        elapsed = int(time.monotonic() - self.session_started)
        self.session.set_label(f"Session {elapsed // 60:02d}:{elapsed % 60:02d}")
        return GLib.SOURCE_CONTINUE

    def _update(self, readings: list[SensorReading]) -> None:
        by_kind: dict[SensorKind, list[SensorReading]] = defaultdict(list)
        for reading in readings:
            by_kind[reading.spec.kind].append(reading)
            row = self.rows.get(reading.spec.sensor_id)
            if row is not None:
                row.update(reading)
        for kind, card in self.metric_cards.items():
            card.update(by_kind[kind])

    def do_close_request(self) -> bool:
        if self.timer_id:
            GLib.source_remove(self.timer_id)
            self.timer_id = 0
        return False


class VitalsApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id="com.omarchy.OVitals",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.window: VitalsWindow | None = None
        self.set_accels_for_action("app.quit", ["<Primary>q"])

    def do_activate(self) -> None:
        if self.window is None:
            self.window = VitalsWindow(self)
        self.window.present()

def run() -> int:
    # GTK chooses Wayland and the preferred renderer from the active Omarchy
    # session. Keeping this automatic also makes diagnostics portable.
    return VitalsApplication().run(None)
