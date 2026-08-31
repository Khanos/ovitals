# oVitals Agent Reference

## Purpose and current state

oVitals is a minimal native hardware monitor for Omarchy/Hyprland. It is Python
with GTK 4 and libadwaita, runs natively on Wayland, and follows the active
Omarchy palette. Public repository: <https://github.com/Khanos/ovitals>.

The UI intentionally contains:

- four summary cards: temperatures, clock speeds, voltages, and fan speeds;
- one always-visible, hardware-grouped table with Current/Minimum/Maximum;
- a compact left-aligned heartbeat mark and `oVitals` title;
- a reset-extrema button and session timer.

Do not reintroduce category tabs, About, Pause, hostname, or theme-name text
without an explicit request. All sensors should remain visible together.

## Architecture

- `vitals/app.py`: GTK widgets, heartbeat drawing, rows, summary cards, timer.
- `vitals/sensors.py`: discovery, sampling, human labels, session extrema.
- `vitals/theme.py`: live palette loading from
  `~/.local/state/omarchy/current/theme/colors.toml` and application CSS.
- `data/`: desktop entry and scalable application icon.
- `install.sh`: dependency check and user-local menu installation.
- `tests/test_sensors.py`: fake-sysfs discovery, formatting, labels, extrema.
- `SENSOR_SUPPORT_RESEARCH.md`: detailed fan/voltage investigation and sources.

Sensor sources:

1. `/sys/class/hwmon` for temperature, voltage, and fan inputs.
2. `/sys/devices/system/cpu/cpufreq` for logical-CPU clocks.
3. `nvidia-smi` opportunistically for NVIDIA temperature, clocks, and fan %.

Missing or unreadable inputs are never fabricated. Min/max values are tracked in
memory from application start or the last reset.

## Naming and UX rules

Prefer human-readable, technically conservative names. Keep useful raw AMD names
in parentheses, for example:

- `Tctl` -> `CPU control temperature (Tctl)`
- `Tccd1` -> `CPU chiplet 1 temperature (Tccd1)`
- `CPU 0 clock` -> `Logical CPU 0 clock speed`
- NVMe `Composite` -> `SSD overall temperature`

Never guess the meaning of unlabeled Gigabyte WMI probes. Use
`Motherboard temperature sensor N`. Natural numeric ordering is required.

The heartbeat mark is drawn in GTK and uses the active Omarchy accent. Keep the
header short and left aligned. Preserve the dense, minimal HWiNFO-inspired table.

## Installation

Run:

```bash
sh install.sh
```

The installer checks packages with `pacman` and installs missing dependencies via
`omarchy pkg add`. It installs under `~/.local/share/omarchy-vitals`, creates the
`~/.local/bin/ovitals` symlink, installs the icon, and generates a desktop entry
whose `Exec` is the absolute installed launcher path.

The installed launcher must resolve its own symlink with `readlink -f` before
importing `vitals`. This fixes the former `No module named vitals` menu-launch
failure. Always test both the source launcher and installed launcher after
changing installation code.

## Test hardware and sensor limitations

Development/test machine:

- Gigabyte B650I AX, BIOS FA8
- AMD Ryzen 5 9600X
- NVIDIA GeForce RTX 5070

`gigabyte_wmi` exposes six temperatures only. The kernel currently exposes no
motherboard `fan*_input` or `in*_input`, so motherboard fan RPM and voltages are
absent; this is a driver limitation, not a UI failure. The likely route is the
ITE Super-I/O plus `it87`, but the exact chip must be detected first.

Safety constraints:

- Never run `sensors-detect --auto`; its manual warns automatic probes may be
  unsafe.
- Do not blindly use `force_id`, `ignore_resource_conflict`,
  `acpi_enforce_resources=lax`, or `fix_pwm_polarity`.
- Do not invent voltage scaling or motherboard probe labels.
- Prefer interactive `sudo sensors-detect`, validate against BIOS, and only then
  consider persistent module configuration.
- Never modify `/usr/share/omarchy`; it is package-owned.

NVIDIA monitoring works on the host. A 0% fan value can be a valid zero-RPM idle
state. NVIDIA has deprecated supported graphics-core voltage reporting.

## Verification

Before installation or commit:

```bash
make check
git diff --check
```

GUI smoke test on the active Wayland session:

```bash
timeout 8 ./run
```

Expected automated result: five unit tests pass, Python compilation succeeds,
and the desktop file validates. GUI smoke-test timeout code 124 is expected when
the healthy app is deliberately stopped after eight seconds; stderr should be
empty.

After installer changes, reinstall with `sh install.sh` and verify:

```bash
timeout 8 ~/.local/bin/ovitals
desktop-file-validate ~/.local/share/applications/com.omarchy.OVitals.desktop
```

## Future work already researched

Priorities, only when requested:

1. A `libsensors` backend so board-specific labels and voltage `compute` rules
   are honored instead of showing raw sysfs voltage values.
2. Direct NVML for indexed NVIDIA fan RPM and multiple fans.
3. A read-only `liquidctl` provider if supported USB cooling hardware is found.

Keep direct sysfs as a fallback, deduplicate providers, use short timeouts, and
avoid adding a daemon unless required.

## Git and release handoff

This directory is its own Git repository on branch `main`, with `origin` pointing
to `Khanos/ovitals`. Python caches are ignored. GitHub CLI is authenticated via
the system keyring. Keep the worktree clean, use focused commits, and push only
after tests pass when publication is part of the request.
