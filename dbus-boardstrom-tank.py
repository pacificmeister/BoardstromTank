#!/usr/bin/env python3
"""
dbus-boardstrom-tank — Venus OS driver for the Boardstrom radar tank sensor.

Listens for the sensor's BLE advertisement (Boardstrom advert v1) via BlueZ's
D-Bus API and publishes each sensor as a com.victronenergy.tank service, so
the tank shows up on the Cerbo/GX like any wired sender: in the Remote
Console, on the GX display, in VRM, and over MQTT.

Advert v1 (manufacturer data, company id 0xFFFF; BlueZ hands us the 13-byte
payload AFTER the company id):
  p0      magic 0x42 ('B')
  p1      version 0x01
  p2..3   distance mm u16 LE, 0xFFFF = no target
  p4..5   battery mV u16 LE, 0xFFFF = n/a
  p6      temperature degC i8, 0x7F = n/a
  p7      status: bits 0-2 quality 0-7, bit 3 unstable (slosh)
  p8..9   sync counter u16 LE
  p10..11 sensor id u16 LE
  p12     checksum, XOR of p0..p11

Because 0xFFFF is the shared Bluetooth SIG test id, ALL FIVE checks run before
a packet is trusted: length, company id, magic, version, checksum.

Configuration is done entirely in the GX UI, no shell needed: the service
publishes the standard writable tank paths (/CustomName, /FluidType,
/Capacity, /RawValueEmpty, /RawValueFull), which the stock tank Setup page
renders (Device List -> the tank -> Setup, same as Victron's own Mopeka
support). Every edit is persisted to localsettings under
/Settings/Devices/boardstrom_<id>/, so it survives reboots and firmware
updates.

  Sensor value when empty = distance (mm) to the surface when EMPTY (larger)
  Sensor value when full  = distance (mm) to the surface when FULL

Until both are set, the tank shows no level and the log prints the live
distance on every advert to make calibration a copy-paste job.

Fluid types (Venus enum): 0 Fuel, 1 Fresh water, 2 Waste water, 3 Live well,
4 Oil, 5 Black water, 6 Gasoline, 7 Diesel, 8 LPG, 9 LNG, 10 Hydraulic oil,
11 Raw water.
"""

import logging
import os
import sys
import time

import dbus
import dbus.mainloop.glib
from gi.repository import GLib

# velib_python ships with Venus OS inside dbus-systemcalc-py.
sys.path.insert(
    1, os.path.join('/opt/victronenergy/dbus-systemcalc-py', 'ext', 'velib_python')
)
from vedbus import VeDbusService  # noqa: E402
from settingsdevice import SettingsDevice  # noqa: E402

VERSION = '0.2.0-beta'
COMPANY_ID = 0xFFFF
MAGIC = 0x42
ADVERT_VERSION = 0x01
# A sensor measures every 60 s in steady state; after this long with no advert
# the tank is marked disconnected (shows as such in the GX UI).
TIMEOUT_S = 300
# Default VRM device instance; localsettings bumps it per device to stay
# unique (ClassAndVrmInstance mechanism).
DEVICE_INSTANCE_BASE = 330

log = logging.getLogger('boardstrom-tank')


def parse_advert(payload):
    """Boardstrom advert v1 -> dict, or None if not ours / corrupt."""
    if len(payload) != 13:
        return None
    if payload[0] != MAGIC or payload[1] != ADVERT_VERSION:
        return None
    xor = 0
    for b in payload[:12]:
        xor ^= b
    if xor != payload[12]:
        return None
    distance = payload[2] | (payload[3] << 8)
    battery = payload[4] | (payload[5] << 8)
    temp = payload[6]
    status = payload[7]
    sensor_id = payload[10] | (payload[11] << 8)
    return {
        'distance_mm': None if distance == 0xFFFF else distance,
        'battery_v': None if battery == 0xFFFF else battery / 1000.0,
        'temperature_c': None if temp == 0x7F else (temp - 256 if temp > 127 else temp),
        'quality': status & 0x07,
        'unstable': bool(status & 0x08),
        'sensor_id': '%04x' % sensor_id,
    }


class TankService(object):
    """One com.victronenergy.tank.* service for one sensor."""

    # Service path <-> localsettings alias, for the GUI-editable paths.
    SETTING_FOR_PATH = {
        '/CustomName': 'customname',
        '/FluidType': 'fluidtype',
        '/Capacity': 'capacity',
        '/RawValueEmpty': 'rawempty',
        '/RawValueFull': 'rawfull',
    }

    def __init__(self, sensor_id):
        self.sensor_id = sensor_id
        self.last_seen = 0
        self.last_raw = None
        self._last_log = 0
        dev = 'boardstrom_%s' % sensor_id

        # GUI-editable config persists in localsettings, like every native
        # tank sender. Capacity is in m3 (Venus convention; the GUI converts
        # to the user's volume unit). Raw values are the distance in mm.
        self.settings = SettingsDevice(dbus.SystemBus(), {
            'instance':   ['/Settings/Devices/%s/ClassAndVrmInstance' % dev,
                           'tank:%d' % DEVICE_INSTANCE_BASE, 0, 0],
            'customname': ['/Settings/Devices/%s/CustomName' % dev, '', 0, 0],
            'fluidtype':  ['/Settings/Devices/%s/FluidType' % dev, 1, 0, 11],
            'capacity':   ['/Settings/Devices/%s/Capacity' % dev, 0.2, 0.0, 1000.0],
            # Defaults chosen so a level shows out of the box (typical small
            # tank); calibrating just makes it accurate.
            'rawempty':   ['/Settings/Devices/%s/RawValueEmpty' % dev, 500.0, 0.0, 20000.0],
            'rawfull':    ['/Settings/Devices/%s/RawValueFull' % dev, 50.0, 0.0, 20000.0],
        }, self._setting_changed)
        instance = int(str(self.settings['instance']).split(':')[1])

        name = 'com.victronenergy.tank.%s' % dev
        # Each VeDbusService needs its own private connection: dbus-python
        # allows only one handler for the root path '/' per connection, and
        # the scanner (plus any second tank) shares the default system bus.
        conn = dbus.bus.BusConnection(dbus.bus.BusConnection.TYPE_SYSTEM)
        svc = self.svc = VeDbusService(name, bus=conn, register=False)
        svc.add_path('/Mgmt/ProcessName', 'dbus-boardstrom-tank')
        svc.add_path('/Mgmt/ProcessVersion', VERSION)
        svc.add_path('/Mgmt/Connection', 'Bluetooth LE')
        svc.add_path('/DeviceInstance', instance)
        svc.add_path('/ProductId', 0)
        svc.add_path('/ProductName', 'Boardstrom Tank Sensor')
        svc.add_path('/Connected', 1)
        svc.add_path('/Status', 0)
        # The stock GX tank Setup page edits these; each write lands in
        # localsettings via _gui_changed.
        svc.add_path('/CustomName', str(self.settings['customname']),
                     writeable=True, onchangecallback=self._gui_changed)
        svc.add_path('/FluidType', int(self.settings['fluidtype']),
                     writeable=True, onchangecallback=self._gui_changed)
        svc.add_path('/Capacity', float(self.settings['capacity']),
                     writeable=True, onchangecallback=self._gui_changed)
        svc.add_path('/RawValueEmpty', float(self.settings['rawempty']),
                     writeable=True, onchangecallback=self._gui_changed)
        svc.add_path('/RawValueFull', float(self.settings['rawfull']),
                     writeable=True, onchangecallback=self._gui_changed)
        svc.add_path('/RawValue', None)
        svc.add_path('/RawUnit', 'mm')
        svc.add_path('/Level', None)
        svc.add_path('/Remaining', None)
        # Extra diagnostics (visible in dbus-spy; harmless to the GUI).
        svc.add_path('/Boardstrom/BatteryVoltage', None)
        svc.add_path('/Boardstrom/Temperature', None)
        svc.add_path('/Boardstrom/Quality', None)
        svc.register()
        log.info('registered %s (instance %d)', name, instance)

    def _gui_changed(self, path, value):
        """A write from the GX UI (or MQTT/dbus): persist and recompute."""
        self.settings[self.SETTING_FOR_PATH[path]] = value
        self._recalc()
        return True

    def _setting_changed(self, alias, old, new):
        """Setting changed behind our back (dbus-spy, VRM): mirror it."""
        for path, setting in self.SETTING_FOR_PATH.items():
            if setting == alias:
                self.svc[path] = new
        self._recalc()

    def _calibrated(self):
        empty = float(self.svc['/RawValueEmpty'])
        full = float(self.svc['/RawValueFull'])
        return empty > 0 and empty > full

    def _recalc(self):
        svc = self.svc
        d = self.last_raw
        svc['/RawValue'] = d
        if d is None or not self._calibrated():
            svc['/Level'] = None
            svc['/Remaining'] = None
            return
        empty = float(svc['/RawValueEmpty'])
        full = float(svc['/RawValueFull'])
        level = (empty - d) / (empty - full) * 100.0
        level = max(0.0, min(100.0, level))
        svc['/Level'] = round(level, 1)
        cap = float(svc['/Capacity'] or 0)
        svc['/Remaining'] = round(cap * level / 100.0, 4) if cap else None

    def update(self, adv):
        self.last_seen = time.time()
        svc = self.svc
        svc['/Connected'] = 1
        svc['/Boardstrom/BatteryVoltage'] = adv['battery_v']
        svc['/Boardstrom/Temperature'] = adv['temperature_c']
        svc['/Boardstrom/Quality'] = adv['quality']

        d = adv['distance_mm']
        # Quality 0 = garbage echo; no-target = out of range. Keep the last
        # good level rather than publishing a lie.
        usable = d is not None and adv['quality'] > 0
        if usable:
            self.last_raw = float(d)
        self._recalc()
        # One line per sensor per minute so calibration (reading the live
        # distance at empty and at full) is a copy-paste job from the log.
        if usable and time.time() - self._last_log > 55:
            self._last_log = time.time()
            log.info('sensor %s: distance %d mm, level %s%%, quality %d',
                     self.sensor_id, d, self.svc['/Level'], adv['quality'])

    def check_timeout(self):
        if self.last_seen and time.time() - self.last_seen > TIMEOUT_S:
            self.svc['/Connected'] = 0


class Scanner(object):
    """Passive BLE scan via the BlueZ D-Bus API; no native deps."""

    def __init__(self, bus):
        self.bus = bus
        self.tanks = {}
        bus.add_signal_receiver(
            self._properties_changed,
            dbus_interface='org.freedesktop.DBus.Properties',
            signal_name='PropertiesChanged',
            arg0='org.bluez.Device1',
            path_keyword='path',
        )
        bus.add_signal_receiver(
            self._interfaces_added,
            dbus_interface='org.freedesktop.DBus.ObjectManager',
            signal_name='InterfacesAdded',
        )
        self._start_discovery()
        GLib.timeout_add_seconds(30, self._tick)

    def _start_discovery(self):
        adapter = self.bus.get_object('org.bluez', '/org/bluez/hci0')
        props = dbus.Interface(adapter, 'org.freedesktop.DBus.Properties')
        iface = dbus.Interface(adapter, 'org.bluez.Adapter1')
        try:
            iface.SetDiscoveryFilter({
                'Transport': dbus.String('le'),
                # Without DuplicateData BlueZ suppresses repeated adverts from
                # the same MAC, and we'd only ever see each sensor once.
                'DuplicateData': dbus.Boolean(True),
            })
            iface.StartDiscovery()
            log.info('BLE discovery started')
        except dbus.exceptions.DBusException as e:
            if 'InProgress' in e.get_dbus_name():
                # Someone else (e.g. Victron's own dbus-ble-sensors) already
                # scans; BlueZ shares the discovery session and we still get
                # PropertiesChanged events. Fine.
                log.info('discovery already running (shared with another scanner)')
            else:
                raise
        # Even when powered, make sure the adapter is on.
        if not props.Get('org.bluez.Adapter1', 'Powered'):
            props.Set('org.bluez.Adapter1', 'Powered', dbus.Boolean(True))

    def _interfaces_added(self, path, interfaces):
        dev = interfaces.get('org.bluez.Device1')
        if dev:
            self._handle_device(dev)

    def _properties_changed(self, interface, changed, invalidated, path=None):
        self._handle_device(changed)

    def _handle_device(self, props):
        md = props.get('ManufacturerData')
        if not md:
            return
        payload = md.get(dbus.UInt16(COMPANY_ID)) or md.get(COMPANY_ID)
        if payload is None:
            return
        adv = parse_advert(bytes(bytearray(payload)))
        if adv is None:
            return
        sid = adv['sensor_id']
        tank = self.tanks.get(sid)
        if tank is None:
            try:
                tank = TankService(sid)
            except Exception:
                log.exception('failed to register tank service for %s', sid)
                return
            self.tanks[sid] = tank
        tank.update(adv)

    def _tick(self):
        for tank in self.tanks.values():
            tank.check_timeout()
        return True


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(name)s %(levelname)s %(message)s',
    )
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    Scanner(bus)
    log.info('dbus-boardstrom-tank %s up; waiting for adverts', VERSION)
    GLib.MainLoop().run()


if __name__ == '__main__':
    main()
