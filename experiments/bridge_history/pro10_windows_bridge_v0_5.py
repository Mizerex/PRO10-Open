# PRO10 Windows Bridge v0.5 - estabilidade, clima/hora e bateria

import asyncio
import csv
import json
import logging
import math
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from bleak import BleakClient, BleakScanner

APP_VERSION = "0.5"
UART_NOTIFY = "6e400003-b5a3-f393-e0a9-e50e24dcca9f"
UART_WRITE = "6e400002-b5a3-f393-e0a9-e50e24dcca9f"
BATTERY_UUID = "00002a19-0000-1000-8000-00805f9b34fb"

PROTOCOL_VERSION = 0x10
CMD_SETTING = 0x02
CMD_FEATURE = 0x19
KEY_TIME = 0x01
KEY_WEATHER_CURRENT = 0x1D
KEY_WEATHER_7 = 0x23
ACK_HEADER = 0xFD
SUCCESS = 0x01

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "pro10_config_v0_5.json"
LOG_PATH = BASE_DIR / "pro10_bridge_v0_5.log"
BATTERY_CSV = BASE_DIR / "pro10_bateria.csv"

DEFAULT_CONFIG = {
    "device_address": "AA:BB:CC:DD:EE:FF",
    "preferred_device_name": "PRO 10",
    "fallback_device_name": "PRO 10",
    "city": "São Paulo",
    "latitude": -23.5505,
    "longitude": -46.6333,
    "scan_timeout_seconds": 6,
    "scan_pause_seconds": 20,
    "connect_retry_seconds": 15,
    "max_reconnect_seconds": 120,
    "keepalive_seconds": 30,
    "max_consecutive_keepalive_failures": 3,
    "weather_update_minutes": 180,
    "time_sync_minutes": 60,
    "battery_log_minutes": 10,
    "ack_timeout_seconds": 4.0,
}


def setup_logger():
    logger = logging.getLogger("pro10v05")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console)
    return logger


LOG = setup_logger()


def load_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return dict(DEFAULT_CONFIG)
    try:
        config = dict(DEFAULT_CONFIG)
        config.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        return config
    except Exception as exc:
        LOG.warning("Config invalida; usando padrao: %s", exc)
        return dict(DEFAULT_CONFIG)


CFG = load_config()


class SingleInstance:
    def __init__(self, name="Local\\PRO10WindowsBridge"):
        self.name = name
        self.handle = None

    def acquire(self):
        if os.name != "nt":
            return True
        import ctypes

        kernel32 = ctypes.windll.kernel32
        self.handle = kernel32.CreateMutexW(None, False, self.name)
        return bool(self.handle) and kernel32.GetLastError() != 183

    def release(self):
        if os.name == "nt" and self.handle:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


def ensure_battery_csv():
    if BATTERY_CSV.exists():
        return
    with BATTERY_CSV.open("w", newline="", encoding="utf-8-sig") as file:
        csv.writer(file).writerow(
            ["timestamp", "battery_percent", "connected", "event", "session_minutes"]
        )


def append_battery(percent, connected, event, session_minutes=""):
    ensure_battery_csv()
    with BATTERY_CSV.open("a", newline="", encoding="utf-8-sig") as file:
        csv.writer(file).writerow(
            [
                datetime.now().isoformat(timespec="seconds"),
                percent if percent is not None else "",
                1 if connected else 0,
                event,
                session_minutes,
            ]
        )


def b8(number):
    return int(number) & 0xFF


def watch_weather_code(wmo):
    wmo = int(wmo)
    if wmo in (0, 1, 2, 3):
        return wmo
    if wmo in (45, 48):
        return 8
    if wmo in (51, 53, 55, 56, 57, 80, 81, 82):
        return 4
    if wmo in (61, 63, 65, 66, 67):
        return 5
    if wmo in (71, 73, 75, 77, 85, 86):
        return 7
    if wmo in (95, 96, 99):
        return 6
    return 0


def get_weather():
    params = {
        "latitude": CFG["latitude"],
        "longitude": CFG["longitude"],
        "current": "temperature_2m,apparent_temperature,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min",
        "timezone": "auto",
        "forecast_days": 7,
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url, headers={"User-Agent": f"PRO10-Windows-Bridge/{APP_VERSION}"}
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def build_frame(command, key, payload=b""):
    payload = bytes(payload)
    length = len(payload)
    base = bytes(
        [
            0xDF,
            ((length + 5) >> 8) & 0xFF,
            (length + 5) & 0xFF,
            command & 0xFF,
            PROTOCOL_VERSION,
            key & 0xFF,
            (length >> 8) & 0xFF,
            length & 0xFF,
        ]
    ) + payload
    checksum = sum(base) & 0xFF
    return base[:3] + bytes([checksum]) + base[3:]


def time_payload(now):
    year = now.year - 2000
    return bytes(
        [
            ((year << 2) + (now.month >> 2)) & 0xFF,
            (((now.month & 3) << 6) + (now.day << 1) + (now.hour >> 4)) & 0xFF,
            (((now.hour & 15) << 4) + (now.minute >> 2)) & 0xFF,
            (((now.minute & 3) << 6) + now.second) & 0xFF,
        ]
    )


def weather7_payload(weather):
    daily = weather["daily"]
    count = min(7, len(daily["time"]))
    output = bytearray([count])
    for index in range(count):
        day = datetime.strptime(daily["time"][index][:10], "%Y-%m-%d")
        output.extend(
            [
                ((day.year - 2000) >> 8) & 0xFF,
                (day.year - 2000) & 0xFF,
                day.month,
                day.day,
                watch_weather_code(daily["weather_code"][index]),
                b8(math.floor(float(daily["temperature_2m_min"][index]))),
                b8(math.ceil(float(daily["temperature_2m_max"][index]))),
            ]
        )
    return bytes(output)


def current_payload(weather):
    current = weather["current"]
    daily = weather["daily"]
    day = datetime.strptime(daily["time"][0][:10], "%Y-%m-%d")
    city = str(CFG["city"]).encode("utf-8")[:50]
    output = bytearray(
        [
            (day.year >> 8) & 0xFF,
            day.year & 0xFF,
            day.month,
            day.day,
            0,
            watch_weather_code(current["weather_code"]),
            b8(math.floor(float(daily["temperature_2m_min"][0]))),
            b8(math.ceil(float(daily["temperature_2m_max"][0]))),
            len(city),
        ]
    )
    output.extend(city)
    output.append(b8(round(float(current["temperature_2m"]))))
    return bytes(output)


class Bridge:
    def __init__(self):
        self.client = None
        self.ack_waiters = {}
        self.disconnected = asyncio.Event()
        self.last_weather = None
        self.last_time_sync = None
        self.last_battery_log = None
        self.session_started = None
        self.intentional_disconnect = False

    def on_disconnect(self, _client):
        reason = "controlado" if self.intentional_disconnect else "remoto/radio/alcance"
        LOG.info("[BLE] Desconectado (%s).", reason)
        minutes = ""
        if self.session_started:
            minutes = round(
                (datetime.now() - self.session_started).total_seconds() / 60, 1
            )
        append_battery(
            None,
            False,
            "disconnect_controlled" if self.intentional_disconnect else "disconnect",
            minutes,
        )
        self.disconnected.set()

    def on_notify(self, _sender, data):
        packet = bytes(data)
        if len(packet) >= 9 and packet[0] == ACK_HEADER:
            command, key, status = packet[4], packet[5], packet[8]
            waiter = self.ack_waiters.get((command, key))
            if waiter and not waiter.done():
                waiter.set_result(status)

    async def scan_once(self):
        address = str(CFG["device_address"]).upper()
        preferred = str(CFG["preferred_device_name"]).casefold()
        fallback = str(CFG["fallback_device_name"]).casefold()
        LOG.info(
            "[PRESENCA] Scan: MAC %s -> %s -> %s",
            address,
            CFG["preferred_device_name"],
            CFG["fallback_device_name"],
        )
        try:
            devices = await BleakScanner.discover(
                timeout=float(CFG["scan_timeout_seconds"])
            )
        except Exception as exc:
            LOG.warning("[PRESENCA] Scan falhou (%s): %s", type(exc).__name__, exc)
            return None
        for device in devices:
            if str(device.address).upper() == address:
                LOG.info("[PRESENCA] Encontrado por MAC: %s", device)
                return device
        for wanted, source in ((preferred, "nome preferencial"), (fallback, "nome fallback")):
            for device in devices:
                if wanted and wanted in (device.name or "").casefold():
                    LOG.info("[PRESENCA] Encontrado por %s: %s", source, device)
                    return device
        return None

    async def connect(self, device):
        self.disconnected.clear()
        self.intentional_disconnect = False
        self.client = BleakClient(device, disconnected_callback=self.on_disconnect)
        LOG.info("[BLE] Conectando...")
        await self.client.connect(timeout=15.0)
        if not self.client.is_connected:
            return False
        await asyncio.sleep(1.0)
        await self.client.start_notify(UART_NOTIFY, self.on_notify)
        self.session_started = datetime.now()
        LOG.info("[BLE] Conectado; notify ativo.")
        await self.log_battery("connect")
        return True

    async def read_battery_percent(self):
        try:
            value = await self.client.read_gatt_char(BATTERY_UUID)
            return int(value[0]) if value else None
        except Exception as exc:
            LOG.warning("[BATERIA] Leitura falhou: %s", exc)
            return None

    async def log_battery(self, event="sample"):
        if not self.client or not self.client.is_connected:
            return None
        percent = await self.read_battery_percent()
        minutes = round(
            (datetime.now() - self.session_started).total_seconds() / 60, 1
        )
        append_battery(percent, True, event, minutes)
        if percent is not None:
            LOG.info("[BATERIA] %d%% | %s | %.1f min", percent, event, minutes)
        self.last_battery_log = datetime.now()
        return percent

    async def send_ack(self, command, key, packet, label):
        if not self.client or not self.client.is_connected:
            raise RuntimeError("BLE desconectado")
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        self.ack_waiters[(command, key)] = waiter
        try:
            await self.client.write_gatt_char(UART_WRITE, packet, response=False)
            try:
                status = await asyncio.wait_for(
                    waiter, timeout=float(CFG["ack_timeout_seconds"])
                )
                if status == SUCCESS and label != "Keepalive":
                    LOG.info("[OK] %s", label)
                elif status != SUCCESS:
                    LOG.warning("[ACK] %s status=0x%02X", label, status)
                return status
            except asyncio.TimeoutError:
                LOG.warning("[ACK] Timeout: %s", label)
                return None
        finally:
            self.ack_waiters.pop((command, key), None)

    async def handshake(self):
        return (
            await self.send_ack(
                CMD_FEATURE,
                0x00,
                build_frame(CMD_FEATURE, 0x00),
                "Handshake 0x19/0x00",
            )
            == SUCCESS
        )

    async def sync_time(self):
        status = await self.send_ack(
            CMD_SETTING,
            KEY_TIME,
            build_frame(CMD_SETTING, KEY_TIME, time_payload(datetime.now())),
            "Hora",
        )
        if status == SUCCESS:
            self.last_time_sync = datetime.now()

    async def update_weather(self):
        LOG.info("[CLIMA] Consultando %s...", CFG["city"])
        weather = await asyncio.to_thread(get_weather)
        status_7 = await self.send_ack(
            CMD_SETTING,
            KEY_WEATHER_7,
            build_frame(CMD_SETTING, KEY_WEATHER_7, weather7_payload(weather)),
            "Previsao 7 dias",
        )
        await asyncio.sleep(0.2)
        status_current = await self.send_ack(
            CMD_SETTING,
            KEY_WEATHER_CURRENT,
            build_frame(CMD_SETTING, KEY_WEATHER_CURRENT, current_payload(weather)),
            "Clima atual",
        )
        if status_7 == SUCCESS and status_current == SUCCESS:
            self.last_weather = datetime.now()
            LOG.info("[CLIMA] Atualizacao confirmada.")

    async def keepalive(self):
        return (
            await self.send_ack(
                CMD_FEATURE,
                0x00,
                build_frame(CMD_FEATURE, 0x00),
                "Keepalive",
            )
            == SUCCESS
        )

    async def safe_time_sync(self):
        try:
            await self.sync_time()
        except Exception as exc:
            LOG.warning("[HORA] Falha sem derrubar BLE (%s): %s", type(exc).__name__, exc)

    async def safe_weather_update(self):
        try:
            await self.update_weather()
        except Exception as exc:
            LOG.warning("[CLIMA] Falha sem derrubar BLE (%s): %s", type(exc).__name__, exc)

    async def connected_session(self):
        if not await self.handshake():
            raise RuntimeError("handshake nao confirmado")
        await self.safe_time_sync()
        await self.safe_weather_update()
        LOG.info("[MODO] Sessao persistente ativa.")
        keepalive_failures = 0
        while self.client and self.client.is_connected:
            try:
                await asyncio.wait_for(
                    self.disconnected.wait(), timeout=float(CFG["keepalive_seconds"])
                )
                break
            except asyncio.TimeoutError:
                pass
            try:
                if await self.keepalive():
                    keepalive_failures = 0
                else:
                    keepalive_failures += 1
                    LOG.warning(
                        "[BLE] Keepalive sem ACK (%d/%d)",
                        keepalive_failures,
                        int(CFG["max_consecutive_keepalive_failures"]),
                    )
                    if keepalive_failures >= int(
                        CFG["max_consecutive_keepalive_failures"]
                    ):
                        raise RuntimeError("limite de keepalive")
            except Exception as exc:
                LOG.warning("[BLE] Keepalive falhou (%s): %s", type(exc).__name__, exc)
                break
            now = datetime.now()
            if self.last_battery_log is None or now - self.last_battery_log >= timedelta(
                minutes=float(CFG["battery_log_minutes"])
            ):
                await self.log_battery("sample")
            if self.last_time_sync is None or now - self.last_time_sync >= timedelta(
                minutes=float(CFG["time_sync_minutes"])
            ):
                await self.safe_time_sync()
            if self.last_weather is None or now - self.last_weather >= timedelta(
                minutes=float(CFG["weather_update_minutes"])
            ):
                await self.safe_weather_update()

    async def disconnect(self):
        if not self.client:
            return
        try:
            if self.client.is_connected:
                self.intentional_disconnect = True
                try:
                    await self.client.stop_notify(UART_NOTIFY)
                except Exception:
                    pass
                await self.client.disconnect()
        except Exception:
            pass

    async def run_forever(self):
        LOG.info("=" * 60)
        LOG.info("PRO10 v0.5 - estabilidade + clima/hora + bateria")
        LOG.info("Keepalive: %ss | CSV: %s", CFG["keepalive_seconds"], BATTERY_CSV)
        LOG.info("=" * 60)
        reconnect_delay = float(CFG["connect_retry_seconds"])
        while True:
            target = await self.scan_once()
            if not target:
                await asyncio.sleep(float(CFG["scan_pause_seconds"]))
                continue
            try:
                if not await self.connect(target):
                    raise RuntimeError("conexao nao estabelecida")
                reconnect_delay = float(CFG["connect_retry_seconds"])
                await self.connected_session()
            except Exception as exc:
                LOG.warning("[BLE] Sessao falhou (%s): %s", type(exc).__name__, exc)
            finally:
                await self.disconnect()
            LOG.info("[BLE] Nova tentativa em %.0fs.", reconnect_delay)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(
                reconnect_delay * 2, float(CFG["max_reconnect_seconds"])
            )


async def main():
    await Bridge().run_forever()


if __name__ == "__main__":
    mutex = SingleInstance()
    if not mutex.acquire():
        print("O PRO10 Bridge ja esta em execucao.")
        sys.exit(0)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        LOG.info("Encerrado pelo usuario.")
    finally:
        mutex.release()
