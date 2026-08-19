# PRO10 Windows Bridge v0.4.1 - Presenca Inteligente + Log de Bateria
#
# Mantem o comportamento da v0.4 e grava a bateria periodicamente em:
#   pro10_bateria.csv
#
# Depois, basta enviar esse CSV para analise.
#
# IMPORTANTE:
# - Nao rode junto com a v0.4 antiga.
# - Esta versao substitui a v0.4 durante o periodo de medicao.
# - Le a bateria pela MESMA conexao BLE ja aberta, evitando conexao extra.

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
from pathlib import Path

from bleak import BleakScanner, BleakClient

APP_VERSION = "0.4.1"

UART_NOTIFY = "6e400003-b5a3-f393-e0a9-e50e24dcca9f"
UART_WRITE  = "6e400002-b5a3-f393-e0a9-e50e24dcca9f"

BATTERY_UUID  = "00002a19-0000-1000-8000-00805f9b34fb"

PROTOCOL_VERSION = 0x10
CMD_SETTING = 0x02
CMD_FEATURE = 0x19
KEY_TIME = 0x01
KEY_WEATHER_CURRENT = 0x1D
KEY_WEATHER_7 = 0x23

ACK_HEADER = 0xFD
SUCCESS = 0x01

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "pro10_config_v0_4_1.json"
LOG_PATH = BASE_DIR / "pro10_bridge_v0_4_1.log"
BATTERY_CSV = BASE_DIR / "pro10_bateria.csv"

DEFAULT_CONFIG = {
    "device_name": "PRO 10",
    "city": "São Paulo",
    "latitude": -23.5505,
    "longitude": -46.6333,

    "scan_timeout_seconds": 6,
    "scan_pause_seconds": 20,
    "connect_retry_seconds": 15,

    "keepalive_seconds": 15,
    "weather_update_minutes": 180,
    "time_sync_minutes": 60,

    # Medicao de bateria para analise posterior.
    "battery_log_minutes": 10,

    "ack_timeout_seconds": 3.0
}


def setup_logger():
    logger = logging.getLogger("pro10v041")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(sh)

    return logger


LOG = setup_logger()


def load_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        return dict(DEFAULT_CONFIG)

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(data)
        return cfg
    except Exception as e:
        LOG.warning("Config invalida; usando padrao: %s", e)
        return dict(DEFAULT_CONFIG)


CFG = load_config()


class SingleInstance:
    def __init__(self, name="Local\\PRO10WindowsBridgeV041"):
        self.name = name
        self.handle = None

    def acquire(self):
        if os.name != "nt":
            return True
        import ctypes
        kernel32 = ctypes.windll.kernel32
        self.handle = kernel32.CreateMutexW(None, False, self.name)
        if not self.handle:
            return False
        return kernel32.GetLastError() != 183

    def release(self):
        if os.name == "nt" and self.handle:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


def ensure_battery_csv():
    if BATTERY_CSV.exists():
        return

    with BATTERY_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([
            "timestamp",
            "battery_percent",
            "connected",
            "event",
            "session_minutes"
        ])


def append_battery(percent, connected, event, session_minutes=""):
    ensure_battery_csv()

    with BATTERY_CSV.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([
            datetime.now().isoformat(timespec="seconds"),
            percent if percent is not None else "",
            1 if connected else 0,
            event,
            session_minutes
        ])


def b8(n):
    return int(n) & 0xFF


def watch_weather_code(wmo):
    wmo = int(wmo)
    if wmo == 0: return 0
    if wmo == 1: return 1
    if wmo == 2: return 2
    if wmo == 3: return 3
    if wmo in (45, 48): return 8
    if wmo in (51, 53, 55, 56, 57, 80, 81, 82): return 4
    if wmo in (61, 63, 65, 66, 67): return 5
    if wmo in (71, 73, 75, 77, 85, 86): return 7
    if wmo in (95, 96, 99): return 6
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
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"PRO10-Windows-Bridge/{APP_VERSION}"}
    )
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode("utf-8"))


def build_frame(cmd, key, payload=b""):
    payload = bytes(payload)
    n = len(payload)

    base = bytes([
        0xDF,
        ((n + 5) >> 8) & 0xFF,
        (n + 5) & 0xFF,
        cmd & 0xFF,
        PROTOCOL_VERSION,
        key & 0xFF,
        (n >> 8) & 0xFF,
        n & 0xFF
    ]) + payload

    checksum = sum(base) & 0xFF
    return base[:3] + bytes([checksum]) + base[3:]


def time_payload(now):
    y = now.year - 2000
    return bytes([
        ((y << 2) + (now.month >> 2)) & 0xFF,
        (((now.month & 3) << 6) + (now.day << 1) + (now.hour >> 4)) & 0xFF,
        (((now.hour & 15) << 4) + (now.minute >> 2)) & 0xFF,
        (((now.minute & 3) << 6) + now.second) & 0xFF,
    ])


def weather7_payload(w):
    d = w["daily"]
    count = min(7, len(d["time"]))
    out = bytearray([count])

    for i in range(count):
        dt = datetime.strptime(d["time"][i][:10], "%Y-%m-%d")
        yo = dt.year - 2000
        tmin = math.floor(float(d["temperature_2m_min"][i]))
        tmax = math.ceil(float(d["temperature_2m_max"][i]))

        out.extend([
            (yo >> 8) & 0xFF, yo & 0xFF,
            dt.month, dt.day,
            watch_weather_code(d["weather_code"][i]),
            b8(tmin), b8(tmax)
        ])

    return bytes(out)


def current_payload(w):
    cur = w["current"]
    d = w["daily"]
    dt = datetime.strptime(d["time"][0][:10], "%Y-%m-%d")

    city = str(CFG["city"]).encode("utf-8")[:50]
    tmin = math.floor(float(d["temperature_2m_min"][0]))
    tmax = math.ceil(float(d["temperature_2m_max"][0]))
    temp = round(float(cur["temperature_2m"]))

    out = bytearray([
        (dt.year >> 8) & 0xFF, dt.year & 0xFF,
        dt.month, dt.day,
        0,
        watch_weather_code(cur["weather_code"]),
        b8(tmin), b8(tmax),
        len(city)
    ])
    out.extend(city)
    out.append(b8(temp))
    return bytes(out)


class Bridge:
    def __init__(self):
        self.client = None
        self.ack_waiters = {}
        self.disconnected = asyncio.Event()
        self.last_weather = None
        self.last_time_sync = None
        self.last_battery_log = None
        self.session_started = None

    def on_disconnect(self, _client):
        LOG.info("[BLE] PRO 10 saiu de alcance ou encerrou a sessao.")
        mins = ""
        if self.session_started:
            mins = round((datetime.now() - self.session_started).total_seconds() / 60, 1)
        append_battery(None, False, "disconnect", mins)
        self.disconnected.set()

    def on_notify(self, _sender, data):
        b = bytes(data)
        if len(b) >= 9 and b[0] == ACK_HEADER:
            cmd, key, status = b[4], b[5], b[8]
            fut = self.ack_waiters.get((cmd, key))
            if fut and not fut.done():
                fut.set_result(status)

    async def scan_once(self):
        LOG.info("[PRESENCA] Procurando PRO 10...")
        try:
            devices = await BleakScanner.discover(
                timeout=float(CFG["scan_timeout_seconds"])
            )
        except Exception as e:
            LOG.warning("[PRESENCA] Falha no scan: %s", e)
            return None

        wanted = str(CFG["device_name"]).lower()
        for d in devices:
            if wanted in (d.name or "").lower():
                LOG.info("[PRESENCA] PRO 10 detectado: %s", d)
                return d
        return None

    async def connect(self, device):
        self.disconnected.clear()
        self.client = BleakClient(
            device,
            disconnected_callback=self.on_disconnect
        )

        LOG.info("[BLE] Conectando ao PRO 10...")
        await self.client.connect()

        if not self.client.is_connected:
            return False

        await asyncio.sleep(1.0)
        await self.client.start_notify(UART_NOTIFY, self.on_notify)
        LOG.info("[BLE] Conectado e notify ativo.")
        self.session_started = datetime.now()

        await self.log_battery("connect")
        return True

    async def read_battery_percent(self):
        try:
            b = await self.client.read_gatt_char(BATTERY_UUID)
            if b:
                return int(b[0])
        except Exception as e:
            LOG.warning("[BATERIA] Falha na leitura: %s", e)
        return None

    async def log_battery(self, event="sample"):
        if not self.client or not self.client.is_connected:
            return None

        percent = await self.read_battery_percent()
        mins = ""
        if self.session_started:
            mins = round(
                (datetime.now() - self.session_started).total_seconds() / 60,
                1
            )

        append_battery(
            percent,
            True,
            event,
            mins
        )

        if percent is not None:
            LOG.info(
                "[BATERIA] %d%% | evento=%s | sessao=%s min",
                percent,
                event,
                mins
            )

        self.last_battery_log = datetime.now()
        return percent

    async def send_ack(self, cmd, key, packet):
        if not self.client or not self.client.is_connected:
            raise RuntimeError("BLE desconectado")

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.ack_waiters[(cmd, key)] = fut

        try:
            await self.client.write_gatt_char(
                UART_WRITE,
                packet,
                response=False
            )

            try:
                return await asyncio.wait_for(
                    fut,
                    timeout=float(CFG["ack_timeout_seconds"])
                )
            except asyncio.TimeoutError:
                return None

        finally:
            self.ack_waiters.pop((cmd, key), None)

    async def handshake(self):
        return await self.send_ack(
            CMD_FEATURE, 0x00,
            build_frame(CMD_FEATURE, 0x00)
        ) == SUCCESS

    async def sync_time(self):
        status = await self.send_ack(
            CMD_SETTING, KEY_TIME,
            build_frame(
                CMD_SETTING,
                KEY_TIME,
                time_payload(datetime.now())
            )
        )
        if status == SUCCESS:
            self.last_time_sync = datetime.now()

    async def update_weather(self):
        weather = await asyncio.to_thread(get_weather)

        s1 = await self.send_ack(
            CMD_SETTING, KEY_WEATHER_7,
            build_frame(
                CMD_SETTING,
                KEY_WEATHER_7,
                weather7_payload(weather)
            )
        )

        await asyncio.sleep(0.20)

        s2 = await self.send_ack(
            CMD_SETTING, KEY_WEATHER_CURRENT,
            build_frame(
                CMD_SETTING,
                KEY_WEATHER_CURRENT,
                current_payload(weather)
            )
        )

        if s1 == SUCCESS and s2 == SUCCESS:
            self.last_weather = datetime.now()
            LOG.info("[CLIMA] Atualizacao aceita pelo relogio.")

    async def keepalive(self):
        return await self.send_ack(
            CMD_FEATURE, 0x00,
            build_frame(CMD_FEATURE, 0x00)
        ) == SUCCESS

    async def connected_session(self):
        if not await self.handshake():
            raise RuntimeError("Handshake falhou")

        await self.sync_time()
        await self.update_weather()

        LOG.info("[MODO] Sessao persistente + monitor de bateria ativos.")

        while self.client and self.client.is_connected:
            try:
                await asyncio.wait_for(
                    self.disconnected.wait(),
                    timeout=float(CFG["keepalive_seconds"])
                )
                break
            except asyncio.TimeoutError:
                pass

            try:
                await self.keepalive()
            except Exception:
                break

            now = datetime.now()

            if (
                self.last_battery_log is None or
                now - self.last_battery_log >= timedelta(
                    minutes=float(CFG["battery_log_minutes"])
                )
            ):
                await self.log_battery("sample")

            if (
                self.last_time_sync is None or
                now - self.last_time_sync >= timedelta(
                    minutes=float(CFG["time_sync_minutes"])
                )
            ):
                await self.sync_time()

            if (
                self.last_weather is None or
                now - self.last_weather >= timedelta(
                    minutes=float(CFG["weather_update_minutes"])
                )
            ):
                await self.update_weather()

    async def disconnect(self):
        if not self.client:
            return
        try:
            if self.client.is_connected:
                try:
                    await self.client.stop_notify(UART_NOTIFY)
                except Exception:
                    pass
                await self.client.disconnect()
        except Exception:
            pass

    async def run_forever(self):
        LOG.info("=" * 60)
        LOG.info("PRO10 v0.4.1 - Presenca + Monitor de Bateria")
        LOG.info("CSV: %s", BATTERY_CSV)
        LOG.info("=" * 60)

        while True:
            target = await self.scan_once()

            if not target:
                await asyncio.sleep(float(CFG["scan_pause_seconds"]))
                continue

            try:
                if not await self.connect(target):
                    raise RuntimeError("Conexao nao estabelecida")

                await self.connected_session()

            except Exception as e:
                LOG.warning("[BLE] Sessao encerrada: %s", e)

            finally:
                await self.disconnect()

            await asyncio.sleep(float(CFG["connect_retry_seconds"]))


async def main():
    bridge = Bridge()
    await bridge.run_forever()


if __name__ == "__main__":
    mutex = SingleInstance()

    if not mutex.acquire():
        print("O PRO10 v0.4.1 ja esta em execucao.")
        sys.exit(0)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        LOG.info("Encerrado pelo usuario.")
    finally:
        mutex.release()
