#!/bin/sh
# Manual install of BoardstromTank on Venus OS, for people who prefer SSH
# over PackageManager. Installs to the same location PackageManager uses
# (/data/BoardstromTank), so PackageManager can adopt and update it later.
# Registers the service now and re-registers it after firmware updates via
# /data/rc.local.
set -e
DEST=/data/BoardstromTank
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "$SRC" != "$DEST" ]; then
  mkdir -p "$DEST"
  cp -r "$SRC/." "$DEST/"
fi
chmod +x "$DEST/setup" "$DEST/services/dbus-boardstrom-tank/run" \
  "$DEST/services/dbus-boardstrom-tank/log/run"

# Service registration now + after every firmware update.
ln -sfn "$DEST/services/dbus-boardstrom-tank" /service/dbus-boardstrom-tank
touch /data/rc.local
chmod +x /data/rc.local
grep -q dbus-boardstrom-tank /data/rc.local || \
  echo 'ln -sfn /data/BoardstromTank/services/dbus-boardstrom-tank /service/dbus-boardstrom-tank' >> /data/rc.local

echo "Installed. The tank appears in Device List when a sensor is heard."
echo "Calibrate in the GX UI: Device List -> Boardstrom Tank Sensor -> Setup"
echo "-> set 'Sensor value when empty/full' (distance in mm)."
echo "Logs: tail -F /var/log/dbus-boardstrom-tank/current | tai64nlocal"
