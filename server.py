#!/usr/bin/python
import asyncio
import enum
import os
from typing import Any, Dict, List, Optional
from bleak import BleakClient, BleakScanner
import binascii
import logging
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method
from dbus_next import Variant, BusType

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SERVICE = None

# Vendor GATT service the control protocol talks to. The earbuds also advertise
# it, so we use it to auto-detect them without a preconfigured MAC address.
CONTROL_SERVICE_UUID = "0000a001-0000-1000-8000-00805f9b34fb"
# Fallback hint: the control endpoint advertises as e.g. "QCY-APP" (QCY makes
# SoundPeats and shares firmware). NB: not "soundpeats" — that name belongs to
# the *classic audio* device, which is not the BLE control endpoint.
NAME_HINTS = ("qcy",)
# Friendly BlueZ alias set on connect so the control endpoint shows up nicely in
# Bluetooth UIs instead of the raw advertised "QCY-APP". Empty disables it.
ALIAS = os.environ.get("SOUNDPEATS_ALIAS", "SoundPeats BLE control")


class CommandsEnum(enum.IntEnum):
    ANCSETTING = 23
    BALANCE = 22
    CLEAR_PAIR = 2
    COMPACTNESS = 17
    DIYANSHI = 9
    FACTORY_RESET = 3
    JIANTING = 10
    LEDMODE = 18
    LIGHT = 5
    MUSICACTION = 4
    NOISE = 7
    NOISEMODE = 12
    PAIRLIST = 28
    PAIRNAME = 24
    REQUESTDATA = -2
    RESET_DEFAULT = 1
    RUER = 6
    SLEEPMODE = 16
    TESTMODE = 13
    VOICE = 8
    VOICENAME = 25


class NoiseMode(enum.IntEnum):
    NORMAL = 0x02
    ANC = 0x63
    PASSTHROUGH = 0xA5


async def get_service(client: BleakClient):
    global SERVICE
    if SERVICE:
        return SERVICE
    (SERVICE,) = (s for s in client.services if s.uuid == CONTROL_SERVICE_UUID)
    return SERVICE


async def discover_earbuds(address=None, timeout=10.0):
    """Find the earbuds' BLE control endpoint.

    With an address, match it exactly. Otherwise auto-detect: pick the
    strongest-signal device whose advertisement carries the vendor control
    service (or a QCY/SoundPeats name). Returns a BLEDevice, or None.
    """
    matches = {}  # address -> (BLEDevice, rssi, has_service)
    found = asyncio.Event()  # set on a confident match to stop scanning early

    def callback(device, adv):
        if address is not None:
            if device.address.casefold() == address.casefold():
                matches[device.address] = (device, adv.rssi, True)
                found.set()
            return
        uuids = {u.casefold() for u in adv.service_uuids}
        name = (adv.local_name or device.name or "").casefold()
        has_service = CONTROL_SERVICE_UUID in uuids
        if has_service or any(h in name for h in NAME_HINTS):
            matches[device.address] = (device, adv.rssi, has_service)
            if has_service:  # confident match — no need to keep scanning
                found.set()

    scanner = BleakScanner(detection_callback=callback)
    await scanner.start()
    try:
        # Return as soon as the control endpoint shows up (usually a second or
        # two) instead of always waiting out the full timeout.
        await asyncio.wait_for(found.wait(), timeout)
    except asyncio.TimeoutError:
        pass
    await scanner.stop()
    if not matches:
        return None
    # Prefer devices actually advertising the control service (the classic audio
    # device may match by name but isn't connectable over BLE GATT); among the
    # pool, pick the strongest signal (closest device).
    service_matches = {a: m for a, m in matches.items() if m[2]}
    pool = service_matches or matches
    device, _, _ = max(pool.values(), key=lambda m: m[1])
    return device


async def set_bluez_alias(address, alias):
    """Best-effort: give the device a friendly BlueZ alias by address, so it
    shows up as e.g. "SoundPeats BLE control" instead of the raw "QCY-APP".

    Purely cosmetic and local; never fatal if it fails (e.g. polkit denies it).
    """
    bus = None
    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        intro = await bus.introspect("org.bluez", "/")
        manager = bus.get_proxy_object("org.bluez", "/", intro).get_interface(
            "org.freedesktop.DBus.ObjectManager"
        )
        objects = await manager.call_get_managed_objects()
        path = next(
            (
                p
                for p, ifaces in objects.items()
                if "org.bluez.Device1" in ifaces
                and ifaces["org.bluez.Device1"].get("Address")
                and ifaces["org.bluez.Device1"]["Address"].value.casefold()
                == address.casefold()
            ),
            None,
        )
        if path is None:
            return
        dintro = await bus.introspect("org.bluez", path)
        device = bus.get_proxy_object("org.bluez", path, dintro).get_interface(
            "org.bluez.Device1"
        )
        if await device.get_alias() != alias:
            await device.set_alias(alias)
            logger.info("Set BlueZ alias of %s to %r", address, alias)
    except Exception as e:
        logger.warning("Could not set BlueZ alias: %s", e)
    finally:
        if bus is not None:
            bus.disconnect()


def pack_data_to_send(*bArr):
    # Initialize a bytearray of size 256
    bArr2 = bytearray(256)
    i = 2
    # Concatenate the input byte arrays into bArr2
    for bArr3 in bArr:
        bArr2[i : i + len(bArr3)] = bArr3
        i += len(bArr3)
    # Create the final byte array of the correct size
    bArr4 = bytearray(i)
    bArr4[0] = 0xFF
    bArr4[1] = i - 2
    bArr4[2:] = bArr2[2:i]
    return bytes(bArr4)


async def set_noise_mode(client: BleakClient, mode: NoiseMode):
    uuid = "00001001-0000-1000-8000-00805f9b34fb"
    service = await get_service(client)
    char = service.get_characteristic(uuid)
    if not char:
        logger.error("Characteristic not found")
        return
    await client.write_gatt_char(
        char,
        pack_data_to_send(bytes([12, 1, mode.value])),
    )


DataBeanType = Dict[str, Any]


def get_data_bean(i: int, b_arr: Optional[bytearray]) -> Optional[DataBeanType]:
    try:
        command = CommandsEnum(i)
    except ValueError:
        # Unknown command id: the original app returns null here and skips it,
        # so we do the same instead of crashing the whole parse.
        logger.debug("Unknown command id %s, skipping", i)
        return None
    return {
        "command": command,
        "data": b_arr,
    }


def process_byte_array(b_arr) -> List[DataBeanType]:
    array_list = []
    if b_arr is not None and len(b_arr) >= 4:
        i = 2
        if len(b_arr) == b_arr[1] + 2:
            while i < len(b_arr):
                i2 = i + 1
                i3 = b_arr[i]
                i += 2
                i4 = b_arr[i2]
                if i4 > 0:
                    b_arr2 = b_arr[i : i + i4]
                    i += i4
                else:
                    b_arr2 = None
                data_bean = get_data_bean(i3, b_arr2)
                if data_bean is not None and data_bean not in array_list:
                    array_list.append(data_bean)
    return array_list


def parse_earbud_setting(data):
    return process_byte_array(data)


class BLEService(ServiceInterface):
    def __init__(self, bus_name):
        super().__init__(bus_name)
        self.client = None
        self.reconnect_task = None
        self.device_address = None
        self.loop = None

    async def connect(self, address=None, retry_forever=False):
        """Connect to the earbuds.

        With an address, connect to that specific device; with no address,
        auto-detect the control endpoint by its advertised vendor service. With
        retry_forever=True keep retrying every 5s (startup auto-connect and
        background reconnects); otherwise make a single attempt.
        """
        global SERVICE
        self.loop = asyncio.get_running_loop()
        while True:
            try:
                logger.info("Looking for earbuds: %s", address or "(auto-detect)")
                device = await discover_earbuds(address)
                if device is None:
                    # Expected while the earbuds are in the case / out of range;
                    # not an error, just keep looking.
                    logger.info("Earbuds not in range yet")
                else:
                    self.device_address = device.address
                    logger.info(f"Connecting to device: {device.address}")
                    SERVICE = None  # invalidate cached service for the new connection
                    client = BleakClient(
                        device, disconnected_callback=self.on_disconnect
                    )
                    # Bound the attempt: a healthy connect takes a few seconds,
                    # but BlueZ occasionally hangs ~30s before failing. Abort
                    # early and retry instead of waiting that out.
                    try:
                        await asyncio.wait_for(client.connect(), timeout=15)
                    except BaseException:
                        # Tear down a possibly half-open link before retrying.
                        try:
                            await client.disconnect()
                        except Exception:
                            pass
                        raise
                    if client.is_connected:
                        self.client = client  # publish only once fully connected
                        logger.info(f"Connected: {client}")
                        if ALIAS:
                            await set_bluez_alias(self.device_address, ALIAS)
                        return True
            except asyncio.TimeoutError:
                logger.warning("Connection attempt timed out (>15s), retrying")
            except Exception as e:
                logger.error("Connection failed: %s", e or type(e).__name__)
            if not retry_forever:
                return False
            await asyncio.sleep(3)

    def on_disconnect(self, client):
        # Called from a bleak callback; hop onto the loop so the reconnect runs
        # as a real asyncio.Task (cancellable by/visible to Connect) instead of
        # a cross-thread future.
        loop = self.loop or asyncio.get_event_loop()
        loop.call_soon_threadsafe(self._reconnect)

    def _reconnect(self):
        if self.reconnect_task is None or self.reconnect_task.done():
            logger.warning("Disconnected from device, attempting to reconnect...")
            self.reconnect_task = asyncio.create_task(
                self.connect(self.device_address, retry_forever=True)
            )

    async def disconnect(self):
        if self.client:
            await self.client.disconnect()
            logger.info("Disconnected")

    def _ensure_connected(self):
        if not self.client or not self.client.is_connected:
            raise Exception("Not connected to any device")

    async def scan(self, timeout=8.0):
        devices = {}

        def callback(device, adv):
            devices[device.address] = f"{device.name or '?'} (rssi {adv.rssi})"

        scanner = BleakScanner(detection_callback=callback)
        await scanner.start()
        await asyncio.sleep(timeout)
        await scanner.stop()
        return devices

    async def explore_services(self):
        self._ensure_connected()
        for service in self.client.services:
            logger.info("[Service] %s", service)
            for char in service.characteristics:
                if "read" in char.properties:
                    try:
                        value = await self.client.read_gatt_char(char.uuid)
                        extra = f", Value: {value}"
                    except Exception as e:
                        extra = f", Error: {e}"
                else:
                    extra = ""
                if "write-without-response" in char.properties:
                    extra += f", Max write w/o rsp size: {char.max_write_without_response_size}"
                logger.info(
                    "  [Characteristic] %s (%s)%s",
                    char,
                    ",".join(char.properties),
                    extra,
                )
                for descriptor in char.descriptors:
                    try:
                        value = await self.client.read_gatt_descriptor(
                            descriptor.handle
                        )
                        logger.info("    [Descriptor] %s, Value: %r", descriptor, value)
                    except Exception as e:
                        logger.error("    [Descriptor] %s, Error: %s", descriptor, e)

    async def battery_level(self):
        self._ensure_connected()
        uuid = "00000008-0000-1000-8000-00805f9b34fb"
        out = await self.client.read_gatt_char(uuid)
        logger.info(f"Received: {out}, {binascii.hexlify(out)}")
        # Bit 7 flags charging / in-case (e.g. 0xE4 == 100% + charging); the low
        # 7 bits are the charge percentage. Without masking it reads as e.g. 228.
        return {
            "left": out[0] & 0x7F,
            "right": out[1] & 0x7F,
            "charging_left": bool(out[0] & 0x80),
            "charging_right": bool(out[1] & 0x80),
        }

    async def get_firmware_version(self):
        self._ensure_connected()
        uuid = "00000007-0000-1000-8000-00805f9b34fb"
        service = await get_service(self.client)
        char = service.get_characteristic(uuid)
        if not char:
            logger.error("Characteristic not found")
            return
        out = await self.client.read_gatt_char(char)
        logger.info(f"Received: {out}, {binascii.hexlify(out)}")
        return out.decode()

    async def read_earbud_setting(self):
        self._ensure_connected()
        uuid = "00001002-0000-1000-8000-00805f9b34fb"
        service = await get_service(self.client)
        char = service.get_characteristic(uuid)
        if not char:
            logger.error("Characteristic not found")
            raise Exception("Characteristic not found")

        out = await self.client.read_gatt_char(char)
        logger.info(f"Received: {out}, {binascii.hexlify(out)}")
        return parse_earbud_setting(out)

    @method()
    async def GetBatteryLevel(self) -> "a{sv}":
        battery = await self.battery_level()
        return {
            "left": Variant("y", battery["left"]),
            "right": Variant("y", battery["right"]),
            "charging_left": Variant("b", battery["charging_left"]),
            "charging_right": Variant("b", battery["charging_right"]),
        }

    @method()
    async def IsConnected(self) -> "b":
        return bool(self.client and self.client.is_connected)

    @method()
    async def Connect(self, address: "s") -> "s":
        # Empty address => auto-detect the earbuds. Connects in the background
        # (retrying) so the call never blocks forever; waits briefly for a fast
        # success, otherwise reports it's still connecting. Poll GetBatteryLevel
        # to confirm once connected.
        target = address or None
        if self.client and self.client.is_connected and (
            target is None or self.device_address == target
        ):
            return "Connected"
        if self.reconnect_task and not self.reconnect_task.done():
            self.reconnect_task.cancel()
        self.reconnect_task = asyncio.create_task(
            self.connect(target, retry_forever=True)
        )
        await asyncio.wait({self.reconnect_task}, timeout=18)
        if self.client and self.client.is_connected:
            return "Connected"
        return "Still connecting in the background"

    @method()
    async def Detect(self) -> "s":
        # Auto-detect the earbuds' control address without connecting.
        device = await discover_earbuds()
        if device is None:
            raise Exception("No SoundPeats/QCY earbuds found")
        return device.address

    @method()
    async def Scan(self) -> "a{ss}":
        # Discover advertising BLE devices for a few seconds. Handy to find the
        # earbuds' control endpoint, which advertises under a different address
        # than the paired audio MAC (e.g. a "QCY-APP" entry).
        return await self.scan()

    @method()
    async def Disconnect(self) -> "s":
        await self.disconnect()
        return "Disconnected"

    @method()
    async def ExploreServices(self) -> "s":
        await self.explore_services()
        return "Services Explored"

    @method()
    async def GetFirmwareVersion(self) -> "s":
        version = await self.get_firmware_version()
        return version

    @method()
    async def GetEarbudSettings(self) -> "a{sv}":
        settings = await self.read_earbud_setting()
        return {
            str(i): Variant(
                "a{sv}",
                {
                    "command": Variant("s", setting["command"].name),
                    "data": (
                        Variant("ay", bytes(setting["data"]))
                        if setting["data"] is not None
                        else Variant("ay", b"")
                    ),
                },
            )
            for i, setting in enumerate(settings)
        }

    @method()
    async def SetNoiseMode(self, mode: "s") -> "s":
        self._ensure_connected()
        try:
            noise_mode = NoiseMode[mode.upper()]
        except KeyError:
            raise ValueError(f"Invalid noise mode: {mode}")
        await set_noise_mode(self.client, noise_mode)
        return f"Noise mode set to {mode}"


async def main():
    bus = await MessageBus().connect()
    service = BLEService("tn.aziz.soundpeats.BLEService")
    bus.export("/tn/aziz/soundpeats/BLEService", service)
    await bus.request_name("tn.aziz.soundpeats.BLEService")
    logger.info("D-Bus service started")

    # Auto-connect on startup so the service is usable right after boot. By
    # default the earbuds are auto-detected by their advertised vendor service;
    # SOUNDPEATS_DEVICE can pin a specific address if several QCY/SoundPeats
    # devices are around. Run as a background task so D-Bus stays responsive.
    address = os.environ.get("SOUNDPEATS_DEVICE") or None
    logger.info("Auto-connecting to %s", address or "(auto-detecting earbuds)")
    # Track the task so on_disconnect won't spawn a second, competing loop.
    service.reconnect_task = asyncio.create_task(
        service.connect(address, retry_forever=True)
    )

    await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
