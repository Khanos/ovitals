# oVitals

A compact native hardware monitor for Omarchy. oVitals displays Linux sensor
readings in a HWiNFO-inspired table with live **Current**, session **Minimum**,
and session **Maximum** values.

## Highlights

- Native GTK 4/libadwaita interface on Wayland
- Reads `hwmon` and `cpufreq` directly; no monitoring daemon required
- Temperatures, CPU/GPU clocks, voltages, and fan speeds when exposed by drivers
- Optional NVIDIA readings through `nvidia-smi`
- Live Omarchy palette reload from the active `colors.toml`
- Category filters and a reset-session control
- Clear empty states when the kernel does not expose a sensor class

## Clone and run

Arch/Omarchy dependencies are GTK 4, libadwaita, Python, and PyGObject. They are
already present on a normal Omarchy desktop.

```bash
git clone https://github.com/Khanos/ovitals.git
cd ovitals
./run
```

To add oVitals to the application launcher:

```bash
./install.sh
```

This installs the `ovitals` command and an `oVitals` menu entry under `~/.local`.
It also checks the native GTK and sensor-tool dependencies and installs anything
missing through `omarchy pkg add`. Package installation can request your sudo
password in the terminal; the application itself runs unprivileged. Run the
installer again after changing the source.

The installer does not automatically probe or force motherboard sensor drivers.
`sensors-detect` warns that blind automatic probing can be unsafe, so the exact
Super-I/O chip must be identified interactively before enabling a driver. See
the [`SENSOR_SUPPORT_RESEARCH.md`](SENSOR_SUPPORT_RESEARCH.md) Gigabyte B650I AX
case study for the recommended sequence.

## Sensor availability

oVitals reports what Linux exposes rather than guessing. If a category is empty,
check `sensors` first. Motherboard voltage and fan readings often need the board's
Super I/O driver (for example `nct6775`) to be loaded. NVIDIA readings require a
working `nvidia-smi`; the rest of the app does not depend on it.

## Tests

```bash
make check
```

The test suite uses a temporary fake sysfs tree and does not touch system files.
