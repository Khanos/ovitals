"""GTK application UI for Omarchy Vitals."""

from __future__ import annotations

from collections import defaultdict
import socket
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

    def apply_filter(self, kind: SensorKind | None) -> bool:
        visible = False
        for row in self.rows:
            show = kind is None or row.spec.kind == kind
            row.set_visible(show)
            visible = visible or show
        self.set_visible(visible)
        return visible


class VitalsWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application, title="oVitals")
        self.set_default_size(980, 720)
        self.set_size_request(720, 520)
        self.manager = SensorManager()
        self.theme = OmarchyTheme()
        self.readings = self.manager.sample()
        self.rows: dict[str, SensorRow] = {}
        self.sections: list[Section] = []
        self.metric_cards: dict[SensorKind, MetricCard] = {}
        self.filter_kind: SensorKind | None = None
        self.session_started = time.monotonic()
        self.timer_id = 0

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)
        self.toast_overlay.set_child(self._build_content())
        self.theme.refresh(force=True)
        self._sync_theme_name()
        self._update(self.readings)
        self.timer_id = GLib.timeout_add(1000, self._on_tick)

    def _build_content(self) -> Gtk.Widget:
        toolbar = Adw.ToolbarView()
        toolbar.add_css_class("vitals-root")
        toolbar.add_top_bar(self._build_header())

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(self._build_summary())
        page.append(self._build_filters())
        page.append(self._build_table())
        page.append(self._build_footer())
        toolbar.set_content(page)
        return toolbar

    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()
        header.add_css_class("vitals-header")
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        mark = Gtk.Label(label="O")
        mark.add_css_class("app-mark")
        mark.set_size_request(28, 28)
        title_box.append(mark)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title = Gtk.Label(label="oVitals", xalign=0)
        title.add_css_class("title-label")
        self.subtitle = Gtk.Label(
            label=f"{socket.gethostname()}  ·  live sensors",
            xalign=0,
        )
        self.subtitle.add_css_class("subtitle-label")
        labels.append(title)
        labels.append(self.subtitle)
        title_box.append(labels)
        header.set_title_widget(title_box)

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

    def _build_filters(self) -> Gtk.Widget:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        bar.add_css_class("filter-bar")
        counts = self.manager.counts()
        total = len(self.manager.specs)
        previous: Gtk.ToggleButton | None = None
        options: list[tuple[str, SensorKind | None, int]] = [("All", None, total)]
        options.extend((KIND_LABELS[kind], kind, counts[kind]) for kind in SensorKind)
        for label, kind, count in options:
            button = Gtk.ToggleButton(label=f"{label}  {count}")
            button.add_css_class("filter-button")
            if previous is not None:
                button.set_group(previous)
            else:
                button.set_active(True)
            button.connect("toggled", self._filter_changed, kind)
            bar.append(button)
            previous = button
        return bar

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

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        contents = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroller.set_child(contents)
        self.stack.add_named(scroller, "sensors")

        self.empty = Adw.StatusPage(
            icon_name="dialog-information-symbolic",
            title="No sensors in this category",
            description="The running kernel is not exposing this sensor type.",
        )
        self.empty.add_css_class("empty-state")
        self.stack.add_named(self.empty, "empty")
        shell.append(self.stack)

        grouped: dict[str, list[SensorReading]] = defaultdict(list)
        for reading in self.readings:
            grouped[reading.spec.section].append(reading)
        for section_name, readings in grouped.items():
            section = Section(section_name)
            self.sections.append(section)
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

    def _filter_changed(self, button: Gtk.ToggleButton, kind: SensorKind | None) -> None:
        if not button.get_active():
            return
        self.filter_kind = kind
        any_visible = any(section.apply_filter(kind) for section in self.sections)
        self.stack.set_visible_child_name("sensors" if any_visible else "empty")
        if not any_visible and kind is not None:
            self.empty.set_title(f"No {KIND_LABELS[kind].lower()} available")
            descriptions = {
                SensorKind.TEMPERATURE: "No temperature inputs were found under Linux hwmon.",
                SensorKind.FREQUENCY: "CPU or GPU clock-speed sensors are not available to this session.",
                SensorKind.VOLTAGE: "Many boards require a Super I/O kernel module before voltage inputs appear.",
                SensorKind.FAN: "Fan RPM is not currently reported by hwmon or the active GPU driver.",
            }
            self.empty.set_description(descriptions[kind])

    def _reset_stats(self, _button: Gtk.Button) -> None:
        self.manager.reset()
        self.session_started = time.monotonic()
        self.readings = self.manager.sample()
        self._update(self.readings)
        self.toast_overlay.add_toast(Adw.Toast(title="Minimum and maximum values reset"))

    def _on_tick(self) -> bool:
        if self.theme.refresh():
            self._sync_theme_name()
        self.readings = self.manager.sample()
        self._update(self.readings)
        elapsed = int(time.monotonic() - self.session_started)
        self.session.set_label(f"Session {elapsed // 60:02d}:{elapsed % 60:02d}")
        return GLib.SOURCE_CONTINUE

    def _sync_theme_name(self) -> None:
        self.subtitle.set_label(f"{socket.gethostname()}  ·  {self.theme.name}")

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
