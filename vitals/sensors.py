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


@dataclass(frozen=True, slots=True)
class LoadReading:
    percent: float
    used_gib: float
    total_gib: float


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


def _mirrored_temperature_sources(directories: list[Path]) -> set[Path]:
    """Find duplicated multi-channel temperature banks exposed by two drivers."""
    banks: dict[tuple[str, ...], list[tuple[Path, str, int]]] = {}
    for directory in directories:
        paths = sorted(directory.glob("temp*_input"), key=_natural_index)
        # Three matching channels makes accidental equality between devices
        # very unlikely, while leaving independent single sensors untouched.
        if len(paths) < 3:
            continue
        values = tuple(_read_text(path) or "" for path in paths)
        if not all(values):
            continue
        chip = _read_text(directory / "name") or directory.name
        labeled = sum(
            (directory / f"temp{_natural_index(path)}_label").is_file()
            for path in paths
        )
        banks.setdefault(values, []).append((directory, chip, labeled))

    mirrored: set[Path] = set()
    for sources in banks.values():
        if len(sources) < 2:
            continue
        # Prefer the source with labels. On Gigabyte boards, gigabyte_wmi is
        # the clearer name for the motherboard bank also mirrored by IT87.
        sources.sort(
            key=lambda source: (
                -source[2],
                0 if source[1].startswith("gigabyte_wmi") else 1,
                source[1],
            )
        )
        mirrored.update(source[0] for source in sources[1:])
    return mirrored


class CpuLoadMonitor:
    """Sample per-logical-CPU utilization from successive /proc/stat reads."""

    def __init__(self, stat_path: Path = Path("/proc/stat")) -> None:
        self.stat_path = stat_path
        self.previous: dict[int, tuple[int, int]] = {}

    def _read_times(self) -> dict[int, tuple[int, int]]:
        raw = _read_text(self.stat_path)
        if raw is None:
            return {}
        times: dict[int, tuple[int, int]] = {}
        for line in raw.splitlines():
            fields = line.split()
            if not fields or not fields[0].startswith("cpu"):
                continue
            index_text = fields[0][3:]
            if not index_text.isdigit():
                continue
            try:
                values = [int(value) for value in fields[1:]]
            except ValueError:
                continue
            if len(values) < 4:
                continue
            # guest and guest_nice are already included in user and nice.
            total = sum(values[:8])
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            times[int(index_text)] = (idle, total)
        return times

    def sample(self) -> tuple[float, ...]:
        current = self._read_times()
        loads: list[float] = []
        for index in sorted(current):
            idle, total = current[index]
            previous = self.previous.get(index)
            if previous is None:
                loads.append(0.0)
                continue
            previous_idle, previous_total = previous
            total_delta = total - previous_total
            idle_delta = idle - previous_idle
            if total_delta <= 0:
                loads.append(0.0)
                continue
            busy = 100.0 * (total_delta - idle_delta) / total_delta
            loads.append(max(0.0, min(100.0, busy)))
        self.previous = current
        return tuple(loads)


class MemoryLoadMonitor:
    """Sample system memory usage from /proc/meminfo."""

    def __init__(self, meminfo_path: Path = Path("/proc/meminfo")) -> None:
        self.meminfo_path = meminfo_path

    def sample(self) -> LoadReading | None:
        raw = _read_text(self.meminfo_path)
        if raw is None:
            return None
        values: dict[str, int] = {}
        for line in raw.splitlines():
            fields = line.split()
            if len(fields) < 2:
                continue
            key = fields[0].rstrip(":")
            if key not in {"MemTotal", "MemAvailable"}:
                continue
            try:
                values[key] = int(fields[1])
            except ValueError:
                return None
        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        if total is None or available is None or total <= 0:
            return None
        used = max(0, total - available)
        return LoadReading(
            percent=max(0.0, min(100.0, 100.0 * used / total)),
            used_gib=used / 1024.0 / 1024.0,
            total_gib=total / 1024.0 / 1024.0,
        )


class VramLoadMonitor:
    """Sample GPU memory usage from nvidia-smi when NVIDIA exposes it."""

    def sample(self) -> LoadReading | None:
        if shutil.which("nvidia-smi") is None:
            return None
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=0.8,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0 or not result.stdout.strip():
            return None
        fields = [field.strip() for field in result.stdout.splitlines()[0].split(",")]
        if len(fields) != 2:
            return None
        try:
            used_mib = float(fields[0])
            total_mib = float(fields[1])
        except ValueError:
            return None
        if total_mib <= 0:
            return None
        return LoadReading(
            percent=max(0.0, min(100.0, 100.0 * used_mib / total_mib)),
            used_gib=used_mib / 1024.0,
            total_gib=total_mib / 1024.0,
        )


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
        directories = [
            Path(directory_name)
            for directory_name in sorted(glob.glob(str(self.hwmon_root / "hwmon*")))
        ]
        mirrored_temperature_sources = _mirrored_temperature_sources(directories)
        for directory in directories:
            chip = _read_text(directory / "name") or directory.name
            section = _chip_section(chip)
            patterns = (
                ("temp*_input", SensorKind.TEMPERATURE, "°C", 1000.0),
                ("in*_input", SensorKind.VOLTAGE, "V", 1000.0),
                ("fan*_input", SensorKind.FAN, "RPM", 1.0),
            )
            for pattern, kind, unit, divisor in patterns:
                if (
                    kind is SensorKind.TEMPERATURE
                    and directory in mirrored_temperature_sources
                ):
                    continue
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
