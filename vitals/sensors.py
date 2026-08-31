"""Sensor discovery and sampling using Linux kernel interfaces.

The primary source is hwmon, which keeps the application dependency-free and
lets it work with whatever drivers the running kernel already exposes. CPU
frequency comes from cpufreq. NVIDIA data is added opportunistically when
``nvidia-smi`` is healthy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import glob
import re
import shutil
import subprocess
from typing import Callable, Iterable


class SensorKind(str, Enum):
    TEMPERATURE = "temperature"
    FREQUENCY = "frequency"
    VOLTAGE = "voltage"
    FAN = "fan"


@dataclass(frozen=True, slots=True)
class SensorSpec:
    sensor_id: str
    section: str
    name: str
    kind: SensorKind
    unit: str
    reader: Callable[[], float | None]


@dataclass(slots=True)
class SensorStats:
    current: float
    minimum: float
    maximum: float
    samples: int = 1

    def add(self, value: float) -> None:
        self.current = value
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)
        self.samples += 1


@dataclass(frozen=True, slots=True)
class SensorReading:
    spec: SensorSpec
    stats: SensorStats | None


SECTION_ORDER = {
    "CPU": 0,
    "GPU": 1,
    "Motherboard": 2,
    "Storage": 3,
    "Networking": 4,
    "System": 5,
}


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


def _number_reader(path: Path, divisor: float = 1.0) -> Callable[[], float | None]:
    def read() -> float | None:
        raw = _read_text(path)
        if raw is None:
            return None
        try:
            return float(raw) / divisor
        except ValueError:
            return None

    return read


def _natural_index(path: Path) -> int:
    match = re.search(r"(\d+)", path.stem)
    return int(match.group(1)) if match else 0


def _chip_section(chip: str) -> str:
    if chip.startswith(("k10temp", "coretemp", "zenpower")):
        return "CPU"
    if chip.startswith(("amdgpu", "nouveau", "nvidia")):
        return "GPU"
    if chip.startswith("nvme"):
        return "Storage"
    if chip.startswith(("r8169", "iwlwifi")):
        return "Networking"
    if chip.startswith(("acpitz", "pch_", "gigabyte", "asus", "nct", "it8")):
        return "Motherboard"
    return "System"


def _fallback_label(chip: str, kind: SensorKind, index: int) -> str:
    if kind == SensorKind.TEMPERATURE:
        if chip.startswith("acpitz"):
            return "Firmware-reported system temperature (ACPI)"
        if chip.startswith("r8169"):
            return "Ethernet controller temperature"
        if chip.startswith("gigabyte"):
            return f"Motherboard temperature sensor {index}"
        return f"Temperature sensor {index}"
    if kind == SensorKind.FAN:
        return f"Fan {index} speed"
    return f"Motherboard voltage input {index}"


def humanize_hwmon_label(
    chip: str,
    kind: SensorKind,
    label: str,
    index: int,
) -> str:
    """Turn kernel-facing labels into clear, conservative display names."""
    cleaned = " ".join(label.replace("_", " ").split())
    lowered = cleaned.casefold()

    if chip.startswith(("k10temp", "zenpower")) and kind == SensorKind.TEMPERATURE:
        if lowered == "tctl":
            return "CPU control temperature (Tctl)"
        if lowered == "tdie":
            return "CPU die temperature (Tdie)"
        match = re.fullmatch(r"tccd(\d+)", lowered)
        if match:
            number = match.group(1)
            return f"CPU chiplet {number} temperature (Tccd{number})"

    if chip.startswith("nvme") and kind == SensorKind.TEMPERATURE:
        if lowered == "composite":
            return "SSD overall temperature"
        match = re.fullmatch(r"sensor\s+(\d+)", lowered)
        if match:
            return f"SSD temperature sensor {match.group(1)}"

    if chip.startswith("acpitz") and kind == SensorKind.TEMPERATURE:
        return "Firmware-reported system temperature (ACPI)"
    if chip.startswith("r8169") and kind == SensorKind.TEMPERATURE:
        return "Ethernet controller temperature"
    if chip.startswith("gigabyte") and kind == SensorKind.TEMPERATURE:
        return f"Motherboard temperature sensor {index}"

    if kind == SensorKind.VOLTAGE:
        voltage_names = {
            "vcore": "CPU core voltage (Vcore)",
            "vsoc": "CPU SoC voltage (VSoC)",
            "+12v": "+12 V rail voltage",
            "+5v": "+5 V rail voltage",
            "+3.3v": "+3.3 V rail voltage",
            "3.3v": "+3.3 V rail voltage",
        }
        if lowered in voltage_names:
            return voltage_names[lowered]
        if "voltage" not in lowered:
            return f"{cleaned} voltage"

    if kind == SensorKind.FAN and "speed" not in lowered:
        return f"{cleaned} speed" if "fan" in lowered else f"{cleaned} fan speed"

    if kind == SensorKind.TEMPERATURE and not any(
        word in lowered for word in ("temperature", "temp", "tctl", "tdie", "tccd")
    ):
        return f"{cleaned} temperature"

    return cleaned


def _natural_text_key(value: str) -> tuple[tuple[int, int | str], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", value)
        if part
    )


class NvidiaSnapshot:
    """One cached nvidia-smi call shared by several virtual sensors."""

    FIELDS = (
        "name",
        "temperature.gpu",
        "clocks.current.graphics",
        "clocks.current.memory",
        "fan.speed",
    )

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def refresh(self) -> bool:
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    f"--query-gpu={','.join(self.FIELDS)}",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=0.8,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            self.values = {}
            return False
        if result.returncode != 0 or not result.stdout.strip():
            self.values = {}
            return False
        parts = [part.strip() for part in result.stdout.splitlines()[0].split(",")]
        if len(parts) != len(self.FIELDS):
            self.values = {}
            return False
        self.values = dict(zip(self.FIELDS, parts, strict=True))
        return True

    def read(self, field: str) -> float | None:
        raw = self.values.get(field)
        if raw is None or raw.lower() in {"n/a", "[not supported]"}:
            return None
        try:
            return float(raw)
        except ValueError:
            return None


class SensorManager:
    """Discovers sensors, samples values, and tracks session extrema."""

    def __init__(
        self,
        hwmon_root: Path = Path("/sys/class/hwmon"),
        cpufreq_root: Path = Path("/sys/devices/system/cpu/cpufreq"),
    ) -> None:
        self.hwmon_root = hwmon_root
        self.cpufreq_root = cpufreq_root
        self.nvidia: NvidiaSnapshot | None = None
        self.specs = self._discover()
        self.stats: dict[str, SensorStats] = {}

    def _discover(self) -> list[SensorSpec]:
        specs = [*self._discover_hwmon(), *self._discover_cpufreq()]
        specs.extend(self._discover_nvidia())
        return sorted(
            specs,
            key=lambda spec: (
                SECTION_ORDER.get(spec.section, 99),
                list(SensorKind).index(spec.kind),
                _natural_text_key(spec.sensor_id),
            ),
        )

    def _discover_hwmon(self) -> Iterable[SensorSpec]:
        for directory_name in sorted(glob.glob(str(self.hwmon_root / "hwmon*"))):
            directory = Path(directory_name)
            chip = _read_text(directory / "name") or directory.name
            section = _chip_section(chip)
            patterns = (
                ("temp*_input", SensorKind.TEMPERATURE, "°C", 1000.0),
                ("in*_input", SensorKind.VOLTAGE, "V", 1000.0),
                ("fan*_input", SensorKind.FAN, "RPM", 1.0),
            )
            for pattern, kind, unit, divisor in patterns:
                for path in sorted(directory.glob(pattern), key=_natural_index):
                    index = _natural_index(path)
                    prefix = "in" if kind == SensorKind.VOLTAGE else kind.value[:4]
                    label = _read_text(directory / f"{prefix}{index}_label")
                    label = label or _fallback_label(chip, kind, index)
                    label = humanize_hwmon_label(chip, kind, label, index)
                    yield SensorSpec(
                        sensor_id=f"hwmon:{chip}:{path.name}",
                        section=section,
                        name=label,
                        kind=kind,
                        unit=unit,
                        reader=_number_reader(path, divisor),
                    )

    def _discover_cpufreq(self) -> Iterable[SensorSpec]:
        if not self.cpufreq_root.exists():
            return
        policies = sorted(
            self.cpufreq_root.glob("policy*"),
            key=lambda path: _natural_index(path),
        )
        for policy in policies:
            path = policy / "scaling_cur_freq"
            if not path.exists():
                continue
            cpu = _read_text(policy / "affected_cpus") or str(_natural_index(policy))
            cpu = cpu.split()[0]
            yield SensorSpec(
                sensor_id=f"cpufreq:{policy.name}",
                section="CPU",
                name=f"Logical CPU {cpu} clock speed",
                kind=SensorKind.FREQUENCY,
                unit="MHz",
                reader=_number_reader(path, 1000.0),
            )

    def _discover_nvidia(self) -> Iterable[SensorSpec]:
        if shutil.which("nvidia-smi") is None:
            return []
        snapshot = NvidiaSnapshot()
        if not snapshot.refresh():
            return []
        self.nvidia = snapshot
        fields = (
            ("temperature.gpu", "GPU core temperature", SensorKind.TEMPERATURE, "°C"),
            ("clocks.current.graphics", "GPU core clock speed", SensorKind.FREQUENCY, "MHz"),
            ("clocks.current.memory", "GPU memory clock speed", SensorKind.FREQUENCY, "MHz"),
            ("fan.speed", "GPU fan speed", SensorKind.FAN, "%"),
        )
        return [
            SensorSpec(
                sensor_id=f"nvidia:{field}",
                section="GPU",
                name=name,
                kind=kind,
                unit=unit,
                reader=lambda field=field: snapshot.read(field),
            )
            for field, name, kind, unit in fields
        ]

    def sample(self) -> list[SensorReading]:
        if self.nvidia is not None:
            self.nvidia.refresh()
        readings: list[SensorReading] = []
        for spec in self.specs:
            value = spec.reader()
            if value is not None:
                state = self.stats.get(spec.sensor_id)
                if state is None:
                    state = SensorStats(value, value, value)
                    self.stats[spec.sensor_id] = state
                else:
                    state.add(value)
            readings.append(SensorReading(spec, self.stats.get(spec.sensor_id)))
        return readings

    def reset(self) -> None:
        """Start a fresh extrema session without changing discovered sensors."""
        self.stats.clear()

    def counts(self) -> dict[SensorKind, int]:
        counts = {kind: 0 for kind in SensorKind}
        for spec in self.specs:
            counts[spec.kind] += 1
        return counts


def format_value(value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    if unit == "°C":
        return f"{value:.1f}"
    if unit == "V":
        return f"{value:.3f}"
    if unit in {"MHz", "RPM", "%"}:
        return f"{value:,.0f}"
    return f"{value:.1f}"
