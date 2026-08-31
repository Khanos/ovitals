#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
bin_home="${HOME}/.local/bin"
app_dir="$data_home/omarchy-vitals"

required_packages=(
  python
  python-gobject
  gtk4
  libadwaita
  lm_sensors
  i2c-tools
  desktop-file-utils
  hicolor-icon-theme
)

# oVitals uses nvidia-smi when an NVIDIA driver is present. Keep this
# conditional so the installer does not add NVIDIA packages to other systems.
if [[ -d /sys/module/nvidia ]] || [[ -d /proc/driver/nvidia/gpus ]]; then
  required_packages+=(nvidia-utils)
fi

missing_packages=()
for package in "${required_packages[@]}"; do
  if ! pacman -Q "$package" >/dev/null 2>&1; then
    missing_packages+=("$package")
  fi
done

if (( ${#missing_packages[@]} > 0 )); then
  if ! command -v omarchy >/dev/null; then
    echo "Cannot install required packages: the omarchy command was not found." >&2
    exit 1
  fi
  echo "Installing required packages: ${missing_packages[*]}"
  omarchy pkg add "${missing_packages[@]}"
else
  echo "All required runtime and sensor packages are already installed."
fi

install -d "$app_dir" "$bin_home" "$data_home/applications" "$data_home/icons/hicolor/scalable/apps"
cp -R "$project_dir/vitals" "$app_dir/"
install -Dm755 "$project_dir/run-installed" "$app_dir/ovitals-launcher"
ln -sfn "$app_dir/ovitals-launcher" "$bin_home/ovitals"
desktop-file-install \
  --dir="$data_home/applications" \
  --set-key=Exec \
  --set-value="$app_dir/ovitals-launcher" \
  "$project_dir/data/com.omarchy.OVitals.desktop"
install -Dm644 "$project_dir/data/com.omarchy.OVitals.svg" "$data_home/icons/hicolor/scalable/apps/com.omarchy.OVitals.svg"

if command -v update-desktop-database >/dev/null; then
  update-desktop-database "$data_home/applications"
fi

echo "oVitals installed. Search for 'ovitals' in the app menu or run: ovitals"

if ! compgen -G '/sys/class/hwmon/hwmon*/fan*_input' >/dev/null; then
  echo "Note: no motherboard fan inputs are currently exposed by the kernel."
  echo "Run 'sudo sensors-detect' interactively before loading a Super-I/O driver."
fi

if ! compgen -G '/sys/class/hwmon/hwmon*/in*_input' >/dev/null; then
  echo "Note: no motherboard voltage inputs are currently exposed by the kernel."
fi

if [[ -d /sys/module/nvidia ]] && ! nvidia-smi >/dev/null 2>&1; then
  echo "Note: the NVIDIA driver is loaded, but NVML is unavailable; GPU fan data will be hidden."
fi
