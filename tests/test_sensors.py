from pathlib import Path
import tempfile
import unittest

from vitals.sensors import (
    SensorKind,
    SensorManager,
    format_value,
    humanize_hwmon_label,
)


class SensorManagerTests(unittest.TestCase):
    def test_discovers_hwmon_and_tracks_extrema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hwmon = root / "hwmon" / "hwmon0"
            hwmon.mkdir(parents=True)
            (hwmon / "name").write_text("k10temp\n")
            value = hwmon / "temp1_input"
            value.write_text("42500\n")
            (hwmon / "temp1_label").write_text("Tctl\n")
            manager = SensorManager(root / "hwmon", root / "cpufreq")

            self.assertEqual(len(manager.specs), 1)
            self.assertEqual(manager.specs[0].kind, SensorKind.TEMPERATURE)
            self.assertEqual(manager.specs[0].name, "CPU control temperature (Tctl)")
            self.assertEqual(manager.sample()[0].stats.current, 42.5)
            value.write_text("60000\n")
            stats = manager.sample()[0].stats
            self.assertEqual(stats.minimum, 42.5)
            self.assertEqual(stats.maximum, 60.0)

    def test_discovers_cpufreq(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = root / "cpufreq" / "policy2"
            policy.mkdir(parents=True)
            (policy / "affected_cpus").write_text("2\n")
            (policy / "scaling_cur_freq").write_text("4400000\n")
            manager = SensorManager(root / "hwmon", root / "cpufreq")

            reading = manager.sample()[0]
            self.assertEqual(reading.spec.name, "Logical CPU 2 clock speed")
            self.assertEqual(reading.stats.current, 4400.0)

    def test_humanizes_known_hardware_labels(self) -> None:
        self.assertEqual(
            humanize_hwmon_label("k10temp", SensorKind.TEMPERATURE, "Tccd1", 3),
            "CPU chiplet 1 temperature (Tccd1)",
        )
        self.assertEqual(
            humanize_hwmon_label("nvme", SensorKind.TEMPERATURE, "Composite", 1),
            "SSD overall temperature",
        )
        self.assertEqual(
            humanize_hwmon_label("nct6798", SensorKind.VOLTAGE, "Vcore", 0),
            "CPU core voltage (Vcore)",
        )
        self.assertEqual(
            humanize_hwmon_label("nct6798", SensorKind.FAN, "CPU Fan", 1),
            "CPU Fan speed",
        )

    def test_reset_starts_new_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hwmon = root / "hwmon" / "hwmon0"
            hwmon.mkdir(parents=True)
            (hwmon / "name").write_text("nct6798\n")
            value = hwmon / "fan1_input"
            value.write_text("900\n")
            manager = SensorManager(root / "hwmon", root / "cpufreq")
            manager.sample()
            value.write_text("1200\n")
            manager.sample()
            manager.reset()

            stats = manager.sample()[0].stats
            self.assertEqual(stats.minimum, 1200.0)
            self.assertEqual(stats.maximum, 1200.0)

    def test_formats_units(self) -> None:
        self.assertEqual(format_value(52.375, "°C"), "52.4")
        self.assertEqual(format_value(1.23456, "V"), "1.235")
        self.assertEqual(format_value(5200, "MHz"), "5,200")
        self.assertEqual(format_value(None, "RPM"), "—")


if __name__ == "__main__":
    unittest.main()
