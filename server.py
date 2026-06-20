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
from dbus_next import Variant

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SERVICE = None


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
    service_uuid = "0000a001-0000-1000-8000-00805f9b34fb"
    (SERVICE,) = (s for s in client.services if s.uuid == service_uuid)
    return SERVICE


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

    async def connect(self, address, retry_forever=False):
        """Connect to a device.

        With retry_forever=True keep retrying every 5s (used for startup
        auto-connect and background reconnects). Otherwise make a single attempt
        and return whether it succeeded, so an explicit Connect call doesn't
        block forever on an unreachable device.
        """
        self.device_address = address
        self.loop = asyncio.get_running_loop()
        while True:
            try:
                logger.info(f"Connecting to device: {address}")
                # Resolve the advertising device first (the recommended bleak
                # pattern): connecting to a bare address makes connect() do its
                # own discovery, which is slow and flaky.
                device = await BleakScanner.find_device_by_address(address, timeout=10.0)
                if device is None:
                    raise Exception(f"{address} not found (not advertising BLE)")
                self.client = BleakClient(
                    device, disconnected_callback=self.on_disconnect
                )
                await self.client.connect()
                if self.client.is_connected:
                    logger.info(f"Connected: {self.client}")
                    return True
            except Exception as e:
                logger.error(f"Connection failed: {e}")
                self.client = None
            if not retry_forever:
                return False
            await asyncio.sleep(5)

    def on_disconnect(self, client):
        logger.warning("Disconnected from device, attempting to reconnect...")
        if self.reconnect_task is None or self.reconnect_task.done():
            loop = self.loop or asyncio.get_event_loop()
            self.reconnect_task = asyncio.run_coroutine_threadsafe(
                self.connect(self.device_address, retry_forever=True), loop
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
        return {
            "left": out[0],
            "right": out[1],
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
        }

    @method()
    async def Connect(self, address: "s") -> "s":
        # Connect in the background (retrying until the device is reachable) so
        # the call never blocks forever. Wait briefly for a fast success;
        # otherwise report that it's still connecting rather than hang past the
        # D-Bus reply timeout. Poll GetBatteryLevel to confirm once connected.
        if self.client and self.client.is_connected and self.device_address == address:
            return "Connected"
        self.device_address = address
        if self.reconnect_task and not self.reconnect_task.done():
            self.reconnect_task.cancel()
        self.reconnect_task = asyncio.create_task(
            self.connect(address, retry_forever=True)
        )
        await asyncio.wait({self.reconnect_task}, timeout=18)
        if self.client and self.client.is_connected:
            return "Connected"
        return f"Still connecting to {address} in the background"

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

    # Auto-connect on startup if a device address is provided, so the service
    # is usable right after boot without a manual Connect call. Run it as a
    # background task so the D-Bus interface stays responsive while it retries.
    address = os.environ.get("SOUNDPEATS_DEVICE")
    if address:
        logger.info("Auto-connecting to %s from $SOUNDPEATS_DEVICE", address)
        asyncio.create_task(service.connect(address, retry_forever=True))

    await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
