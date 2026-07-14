# BoardstromTank — radar tank sensor on your Cerbo GX / Venus OS

Venus OS driver for the [Boardstrom Tank Sensor](https://boardstrom.com/tank-sensor.html),
the DIY no-holes radar tank sensor. The driver listens for the sensor's
Bluetooth broadcast and publishes each sensor as a native Victron tank, so
your levels show up everywhere a wired sender would: **Remote Console, the
GX Touch display, VRM, and MQTT**.

- No configuration files: calibrate right in the GX tank Setup page, like a
  Mopeka sensor
- Multiple sensors supported, each its own tank
- Passive listening only; the sensor keeps broadcasting to the Boardstrom
  app at the same time

Status: **beta**, tested on a Raspberry Pi 4 running Venus OS (July 2026).

## Requirements

- A Venus OS device with Bluetooth: Cerbo GX, Ekrano GX, or Venus OS on a
  Raspberry Pi (built-in BT on Pi 3/4, or a USB dongle)
- A flashed [Boardstrom Tank Sensor](https://boardstrom.com/tank-sensor.html)
  in Bluetooth range

## Install with PackageManager (recommended)

If you don't have SetupHelper yet, install it once:
[kwindrem/SetupHelper](https://github.com/kwindrem/SetupHelper) (a USB-stick
"blind install" is available, no command line needed). Then:

1. Open **Settings → Package manager → Inactive packages → new**
2. Enter package name `BoardstromTank`, GitHub user `pacificmeister`,
   tag `latest`
3. Proceed to download and install it

Updates then arrive through PackageManager like any other package.

## Install manually (SSH)

```sh
scp -r BoardstromTank root@<venus-ip>:/data/
ssh root@<venus-ip> sh /data/BoardstromTank/install.sh
```

## Configure (all in the GX UI)

The tank appears in **Device List** as "Boardstrom Tank Sensor" within a
minute of the sensor broadcasting (every 10 s for the first 10 min after
battery insert, then once a minute). It shows a level right away using
default calibration (empty = 500 mm, full = 50 mm). To make it accurate,
open the tank and go to **Setup**:

- **Sensor value when empty** — distance in mm from the sensor to the
  liquid surface when the tank is EMPTY (the larger number)
- **Sensor value when full** — distance in mm when FULL
- **Capacity** and **Fluid type** to taste; rename via **Device → Name**

The live distance is shown on the same page as the sensor value, so
calibration is just reading it off at the two levels. All settings persist
on the GX and survive reboots and firmware updates.

## Verify / troubleshoot

Log: `tail -F /var/log/dbus-boardstrom-tank/current | tai64nlocal`

The log prints each sensor's id and live distance while uncalibrated. If no
tank appears: check the sensor battery, get closer, and re-seat the battery
to put the sensor in fast-broadcast mode for 10 minutes.

## Uninstall

Via PackageManager (Active packages → BoardstromTank → uninstall), or for a
manual install: `rm /service/dbus-boardstrom-tank`, remove the
dbus-boardstrom-tank line from `/data/rc.local`, `rm -rf /data/BoardstromTank`.

## How it works

The sensor broadcasts a 13-byte manufacturer-data advert (distance, battery,
temperature, quality) about once a minute. The driver does a passive BLE
scan via BlueZ's D-Bus API (no pairing, no connection) and publishes
`com.victronenergy.tank` services with the standard writable calibration
paths, persisted in localsettings. Advert format and firmware:
[boardstrom-radar-firmware](https://github.com/pacificmeister/boardstrom-radar-firmware).

## Support

- [r/boardstrom](https://www.reddit.com/r/boardstrom/)
- merten@boardstrom.com
