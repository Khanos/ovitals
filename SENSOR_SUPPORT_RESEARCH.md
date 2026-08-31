# Gigabyte B650I AX fan and voltage support

Research date: 2026-08-31

## What the test system exposes

The test system is a Gigabyte B650I AX (BIOS FA8) with an AMD Ryzen 5 9600X and an
NVIDIA GeForce RTX 5070. Linux currently exposes these hardware-monitoring
drivers:

| Driver | Data available |
| --- | --- |
| `k10temp` | CPU Tctl and CCD temperature |
| `gigabyte_wmi` | Six unnamed board temperatures |
| `nvme` | SSD temperatures |
| `r8169` | Ethernet controller temperature |
| `acpitz` | ACPI temperature |

There are no `fan*_input` or `in*_input` files under `/sys/class/hwmon`, so no
application using Linux's standard hardware-monitoring API can currently read a
motherboard fan speed or voltage. oVitals already supports those files and will
discover them automatically when the correct driver exposes them.

The active `gigabyte_wmi` driver cannot provide the missing values: its upstream
source defines six temperature channels and no fan or voltage channels.

## Motherboard fans and voltages: the most promising path

Gigabyte specifies an ITE I/O controller and three PWM/DC fan headers for this
board. The current kernel includes the in-tree `it87` driver. Its documentation
lists support for recent ITE controllers, including IT8689E, and describes fan
tachometers and voltage inputs. The exact ITE model fitted to this board must be
identified before loading or forcing a driver.

The safe discovery sequence is:

1. Run `sudo sensors-detect` interactively in a local terminal.
2. Allow the CPU and Super-I/O checks.
3. Do not force ISA or SMBus scans if the tool proposes skipping them.
4. Record the detected chip ID and recommended module.
5. If it recommends `it87`, first test it only for the current boot:

   ```bash
   sudo modprobe it87
   sensors
   ```

6. Confirm that a new hwmon device contains plausible `fan*_input` and
   `in*_input` readings before making the module persistent.

Do not start with `force_id`, `ignore_resource_conflict`,
`acpi_enforce_resources=lax`, or `fix_pwm_polarity`. Those options bypass safety
checks; the installed module explicitly marks the polarity option dangerous.
`sensors-detect --auto` is also inappropriate here because its own manual warns
that automatic probing can select potentially dangerous scans.

If the in-tree driver recognizes the chip and behaves correctly, persistence can
later be added with a narrowly scoped file in `/etc/modules-load.d/`. If it does
not recognize the exact chip, compare the detected ID with the current upstream
driver before considering the out-of-tree `frankcrawford/it87` DKMS module.

### Correct voltage names and scaling

Super-I/O voltage channels are often unlabeled, and motherboard resistor dividers
can require `compute` rules. The lm-sensors project explicitly recommends using a
board-specific `sensors3.conf` to relabel and scale them, with BIOS values as a
reference.

oVitals currently reads raw hwmon values. Before presenting newly exposed board
voltages as authoritative, the app should gain a `libsensors` provider so it
honors those labels and `compute` rules. A practical implementation is:

- discover and sample motherboard hwmon values through `libsensors`;
- retain direct sysfs sampling as a fallback;
- deduplicate chips by bus/address;
- reject implausible or unconfigured voltage rails from the summary card;
- show the raw chip/channel identifier in a tooltip for troubleshooting.

Using `sensors -j` is a useful prototype, but direct `libsensors` integration is
preferable for a one-second GUI refresh because it avoids spawning a process on
every sample.

## NVIDIA GPU fan support

Host-side verification confirms that `nvidia-smi` can read the RTX 5070, so
oVitals can show the GPU temperature, clocks, and fan percentage. The card
reported 0% fan at idle during verification, which is a valid zero-RPM state.

With healthy NVML, oVitals can support:

- GPU temperature and clocks;
- fan percentage through the existing `nvidia-smi` provider;
- per-fan RPM through NVML's `nvmlDeviceGetFanSpeedRPM`;
- multiple GPU fans through the indexed NVML APIs.

Direct NVML is the better long-term backend than parsing `nvidia-smi`. NVIDIA's
current API documents both indexed fan percentage and RPM queries. Graphics-core
voltage is not a viable target: NVIDIA has deprecated the graphics voltage value,
reports it as unavailable, and is removing it from `nvidia-smi`.

## USB fan controllers, pumps, and smart PSUs

`liquidctl` 1.16.0 is installed. It can report fan/pump RPM, temperatures, and—in
some supported controllers or PSUs—voltage/current. This is separate from the
Gigabyte motherboard fan headers. An optional oVitals provider should:

1. enumerate supported devices without initializing or changing them;
2. query read-only status at a slower interval (for example every 2–3 seconds);
3. map temperature, fan, pump, and voltage records into the normal sensor model;
4. time out quickly and disappear cleanly when no supported device is attached.

The ITE USB HID device visible in the boot log is a Gigabyte device, but that does
not prove it exposes cooling telemetry; liquidctl's Gigabyte motherboard support
is primarily for RGB. A host-side `liquidctl list --verbose` is needed to confirm
whether any supported cooling device is present.

## CPU voltage limitations

This Ryzen 5 9600X exposes only temperatures through the running `k10temp`
driver. No CPU voltage channel exists in its hwmon directory. The likely useful
CPU voltage for this machine is therefore the motherboard Super-I/O's Vcore/SoC
rail after `it87` detection and board-specific calibration. An out-of-tree AMD
telemetry module should not be the first option, especially without confirmed
Zen 5 support.

## Recommended order of work

1. Interactively identify the ITE Super-I/O with `sensors-detect`.
2. Test the in-tree `it87` driver for one boot and validate readings against BIOS.
3. Add calibrated labels/compute rules and a `libsensors` backend to oVitals.
4. Add per-fan NVIDIA RPM via direct NVML (percentage already works).
5. Probe `liquidctl` on the host and add its read-only provider only if a cooling
   device is actually detected.
6. Make any kernel-module persistence changes only after the readings are stable.

## Primary references

- [Gigabyte B650I AX product page](https://www.gigabyte.com/us/Motherboard/B650I-AX-rev-11)
- [Gigabyte B650I AX manual](https://download.gigabyte.com/FileList/Manual/mb_manual_b650i-ax_1101_e.pdf)
- [Linux `gigabyte_wmi` source](https://github.com/torvalds/linux/blob/master/drivers/platform/x86/gigabyte-wmi.c)
- [Linux `it87` driver documentation](https://docs.kernel.org/next/hwmon/it87.html)
- [Linux hwmon sysfs specification](https://github.com/torvalds/linux/blob/master/Documentation/hwmon/sysfs-interface.rst)
- [lm-sensors installation and configuration guidance](https://github.com/lm-sensors/lm-sensors/blob/master/INSTALL)
- [NVIDIA NVML device query API](https://docs.nvidia.com/deploy/nvml-api/group__nvmlDeviceQueries.html)
- [NVIDIA `nvidia-smi` documentation](https://docs.nvidia.com/deploy/nvidia-smi/index.html)
- [liquidctl supported devices and API usage](https://github.com/liquidctl/liquidctl)
- [Out-of-tree `it87` driver, only if required](https://github.com/frankcrawford/it87)
