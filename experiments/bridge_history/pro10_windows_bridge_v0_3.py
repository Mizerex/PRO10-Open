# PRO10 Windows Bridge v0.3
# Uso diario: Windows -> BLE -> PRO 10
#
# Esta versao parte do protocolo que JA foi aceito pelo seu PRO 10:
#   0x19/0x00  capacidades
#   0x02/0x01  hora
#   0x02/0x23  previsao 7 dias
#   0x02/0x1D  clima atual
#
# Principais mudancas em relacao a v0.2:
# - varias tentativas de localizar o relogio, mas SEM loop infinito
# - poucas tentativas de conexao
# - uma unica leitura de bateria por execucao
# - consulta do clima somente quando o relogio foi localizado
# - espera curta apos o ultimo ACK para o relogio processar o clima
# - desconexao intencional logo depois
# - log em arquivo para funcionar com pythonw.exe / Agendador do Windows
# - trava contra duas instancias rodando ao mesmo tempo
# - configuracao externa em pro10_config.json
#
# Requisito:
#   python -m pip install bleak

import argparse
import asyncio
import json
import logging
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from bleak import BleakScanner, BleakClient

APP_VERSION = "0.3"
DEVICE_NAME = "PRO 10"

UART_NOTIFY = "6e400003-b5a3-f393-e0a9-e50e24dcca9f"
UART_WRITE  = "6e400002-b5a3-f393-e0a9-e50e24dcca9f"

BATTERY_UUID  = "00002a19-0000-1000-8000-00805f9b34fb"
HARDWARE_UUID = "00002a27-0000-1000-8000-00805f9b34fb"
FIRMWARE_UUID = "00002a26-0000-1000-8000-00805f9b34fb"
SOFTWARE_UUID = "00002a28-0000-1000-8000-00805f9b34fb"

PROTOCOL_VERSION = 0x10
COMMAND_SETTING_ORDER = 0x02
COMMAND_GET_FEATURE = 0x19

KEY_TIME = 0x01
KEY_WEATHER_CURRENT = 0x1D
KEY_WEATHER_7 = 0x23

ACK_HEADER = 0xFD
SUCCESS = 0x01

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "pro10_config.json"
LOG_PATH = BASE_DIR / "pro10_bridge.log"

DEFAULT_CONFIG = {
    "device_name": "PRO 10",
    "city": "São Paulo",
    "latitude": -23.5505,
    "longitude": -46.6333,
    "scan_timeout_seconds": 8,
    "scan_attempts": 5,
    "scan_pause_seconds": 4,
    "connect_attempts": 2,
    "connect_pause_seconds": 3,
    "ack_timeout_seconds": 3.0,
    "post_send_wait_seconds": 2.0,
    "forecast_days": 7
}


def setup_logging(verbose=True):
    logger = logging.getLogger("pro10")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    if verbose:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(sh)

    return logger


LOG = setup_logging(verbose=(sys.stdout is not None))


def load_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        return dict(DEFAULT_CONFIG)

    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg)
        return merged
    except Exception as e:
        LOG.warning("Configuração inválida; usando padrão: %s", e)
        return dict(DEFAULT_CONFIG)


CFG = load_config()


class SingleInstance:
    """Mutex do Windows para impedir duas atualizacoes ao mesmo tempo."""
    def __init__(self, name="Local\\PRO10WindowsBridgeWeather"):
        self.name = name
        self.handle = None
        self.already_exists = False

    def acquire(self):
        if os.name != "nt":
            return True

        import ctypes
        kernel32 = ctypes.windll.kernel32
        ERROR_ALREADY_EXISTS = 183

        self.handle = kernel32.CreateMutexW(None, False, self.name)
        if not self.handle:
            return False

        self.already_exists = kernel32.GetLastError() == ERROR_ALREADY_EXISTS
        return not self.already_exists

    def release(self):
        if os.name == "nt" and self.handle:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


def to_byte(n: int) -> int:
    return int(n) & 0xFF


def weather_code_for_watch(wmo: int) -> int:
    if wmo == 0:
        return 0
    if wmo == 1:
        return 1
    if wmo == 2:
        return 2
    if wmo == 3:
        return 3
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


def weather_text(wmo: int) -> str:
    mapping = {
        0: "Céu limpo",
        1: "Principalmente limpo",
        2: "Parcialmente nublado",
        3: "Nublado",
        45: "Nevoeiro",
        48: "Nevoeiro com geada",
        51: "Garoa fraca",
        53: "Garoa",
        55: "Garoa forte",
        56: "Garoa congelante",
        57: "Garoa congelante forte",
        61: "Chuva fraca",
        63: "Chuva",
        65: "Chuva forte",
        66: "Chuva congelante",
        67: "Chuva congelante forte",
        71: "Neve fraca",
        73: "Neve",
        75: "Neve forte",
        77: "Grãos de neve",
        80: "Pancadas fracas",
        81: "Pancadas",
        82: "Pancadas fortes",
        85: "Pancadas de neve",
        86: "Pancadas fortes de neve",
        95: "Trovoada",
        96: "Trovoada com granizo",
        99: "Trovoada forte com granizo",
    }
    return mapping.get(wmo, f"Código {wmo}")


def get_weather():
    params = {
        "latitude": CFG["latitude"],
        "longitude": CFG["longitude"],
        "current": "temperature_2m,apparent_temperature,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min",
        "timezone": "auto",
        "forecast_days": int(CFG.get("forecast_days", 7)),
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"PRO10-Windows-Bridge/{APP_VERSION}"}
    )
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode("utf-8"))


def build_frame(command_id: int, command_key: int, payload: bytes = b"") -> bytes:
    payload = bytes(payload)
    n = len(payload)

    base = bytes([
        0xDF,
        ((n + 5) >> 8) & 0xFF,
        (n + 5) & 0xFF,
        command_id & 0xFF,
        PROTOCOL_VERSION & 0xFF,
        command_key & 0xFF,
        (n >> 8) & 0xFF,
        n & 0xFF,
    ]) + payload

    checksum = sum(base) & 0xFF
    return base[:3] + bytes([checksum]) + base[3:]


def build_feature_query() -> bytes:
    return build_frame(COMMAND_GET_FEATURE, 0x00)


def build_time_payload(now: datetime) -> bytes:
    year_off = now.year - 2000

    b0 = ((year_off << 2) + (now.month >> 2)) & 0xFF
    b1 = (((now.month & 0x03) << 6) + (now.day << 1) + (now.hour >> 4)) & 0xFF
    b2 = (((now.hour & 0x0F) << 4) + (now.minute >> 2)) & 0xFF
    b3 = (((now.minute & 0x03) << 6) + now.second) & 0xFF

    return bytes([b0, b1, b2, b3])


def build_time_frame(now: datetime) -> bytes:
    return build_frame(COMMAND_SETTING_ORDER, KEY_TIME, build_time_payload(now))


def build_weather7_payload(weather: dict) -> bytes:
    daily = weather["daily"]
    count = min(
        7,
        len(daily["time"]),
        len(daily["weather_code"]),
        len(daily["temperature_2m_min"]),
        len(daily["temperature_2m_max"]),
    )

    payload = bytearray([count & 0xFF])

    for i in range(count):
        d = datetime.strptime(daily["time"][i][:10], "%Y-%m-%d")
        year_off = d.year - 2000
        code = weather_code_for_watch(int(daily["weather_code"][i]))
        tmin = math.floor(float(daily["temperature_2m_min"][i]))
        tmax = math.ceil(float(daily["temperature_2m_max"][i]))

        payload.extend([
            (year_off >> 8) & 0xFF,
            year_off & 0xFF,
            d.month & 0xFF,
            d.day & 0xFF,
            code & 0xFF,
            to_byte(tmin),
            to_byte(tmax),
        ])

    return bytes(payload)


def build_weather7_frame(weather: dict) -> bytes:
    return build_frame(
        COMMAND_SETTING_ORDER,
        KEY_WEATHER_7,
        build_weather7_payload(weather)
    )


def build_weather_current_payload(weather: dict) -> bytes:
    cur = weather["current"]
    daily = weather["daily"]

    d = datetime.strptime(daily["time"][0][:10], "%Y-%m-%d")
    code = weather_code_for_watch(int(cur["weather_code"]))
    tmin = math.floor(float(daily["temperature_2m_min"][0]))
    tmax = math.ceil(float(daily["temperature_2m_max"][0]))
    current_temp = int(round(float(cur["temperature_2m"])))

    city = str(CFG["city"]).encode("utf-8")[:50]

    payload = bytearray([
        (d.year >> 8) & 0xFF,
        d.year & 0xFF,
        d.month & 0xFF,
        d.day & 0xFF,
        0x00,
        code & 0xFF,
        to_byte(tmin),
        to_byte(tmax),
        len(city) & 0xFF,
    ])
    payload.extend(city)
    payload.append(to_byte(current_temp))

    return bytes(payload)


def build_weather_current_frame(weather: dict) -> bytes:
    return build_frame(
        COMMAND_SETTING_ORDER,
        KEY_WEATHER_CURRENT,
        build_weather_current_payload(weather)
    )


class Pro10Bridge:
    def __init__(self):
        self.client = None
        self.ack_waiters = {}
        self.disconnected = asyncio.Event()

    def on_disconnect(self, _client):
        self.disconnected.set()
        LOG.info("[BLE] Relógio desconectou.")

    def on_notify(self, _sender, data):
        data = bytes(data)
        LOG.info("[RX] %s", data.hex(" ").upper())

        if len(data) >= 9 and data[0] == ACK_HEADER:
            command_id = data[4]
            command_key = data[5]
            status = data[8]

            LOG.info(
                "[ACK] command=0x%02X key=0x%02X status=0x%02X",
                command_id, command_key, status
            )

            fut = self.ack_waiters.get((command_id, command_key))
            if fut and not fut.done():
                fut.set_result(status)

    async def find_watch(self):
        attempts = max(1, int(CFG["scan_attempts"]))
        timeout = float(CFG["scan_timeout_seconds"])
        pause = float(CFG["scan_pause_seconds"])
        wanted = str(CFG.get("device_name", DEVICE_NAME)).lower()

        for attempt in range(1, attempts + 1):
            LOG.info("[BLE] Procurando PRO 10 (%d/%d)...", attempt, attempts)

            try:
                devices = await BleakScanner.discover(timeout=timeout)
            except Exception as e:
                LOG.warning("[BLE] Falha no scan: %s", e)
                devices = []

            for d in devices:
                if wanted in (d.name or "").lower():
                    LOG.info("[BLE] Encontrado: %s", d)
                    return d

            if attempt < attempts:
                await asyncio.sleep(pause)

        return None

    async def connect_with_retry(self, device):
        attempts = max(1, int(CFG["connect_attempts"]))
        pause = float(CFG["connect_pause_seconds"])

        for attempt in range(1, attempts + 1):
            LOG.info("[BLE] Conectando (%d/%d)...", attempt, attempts)

            self.disconnected.clear()
            self.client = BleakClient(
                device,
                disconnected_callback=self.on_disconnect
            )

            try:
                await self.client.connect()

                if self.client.is_connected:
                    LOG.info("[BLE] Conectado.")
                    await asyncio.sleep(1.0)
                    return True

            except Exception as e:
                LOG.warning("[BLE] Conexão falhou: %r", e)

            try:
                if self.client and self.client.is_connected:
                    await self.client.disconnect()
            except Exception:
                pass

            if attempt < attempts:
                await asyncio.sleep(pause)

        return False

    async def read_battery(self):
        try:
            value = await self.client.read_gatt_char(BATTERY_UUID)
            if value:
                LOG.info("[INFO] Bateria: %d%%", int(value[0]))
                return int(value[0])
        except Exception as e:
            LOG.warning("[INFO] Bateria indisponível: %s", e)
        return None

    async def read_optional_text(self, uuid, label):
        try:
            value = await self.client.read_gatt_char(uuid)
            txt = bytes(value).decode("utf-8", errors="replace").strip("\x00")
            LOG.info("[INFO] %s: %s", label, txt)
            return bytes(value)
        except Exception as e:
            LOG.info("[INFO] %s: leitura ignorada (%s)", label, e)
            return None

    async def send_and_wait_ack(self, command_id, command_key, frame, label):
        if not self.client or not self.client.is_connected:
            raise RuntimeError("Relógio desconectado antes do envio.")

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        key = (command_id, command_key)
        self.ack_waiters[key] = fut

        LOG.info("[TX] %s", label)
        LOG.info("[TX] %s", frame.hex(" ").upper())

        try:
            await self.client.write_gatt_char(UART_WRITE, frame, response=False)

            try:
                status = await asyncio.wait_for(
                    fut,
                    timeout=float(CFG["ack_timeout_seconds"])
                )
            except asyncio.TimeoutError:
                LOG.warning("[AVISO] %s: ACK não chegou.", label)
                return None

            if status == SUCCESS:
                LOG.info("[OK] %s", label)
                return True

            LOG.warning(
                "[AVISO] %s: status 0x%02X",
                label, status
            )
            return False

        finally:
            self.ack_waiters.pop(key, None)

    async def disconnect_cleanly(self):
        if not self.client:
            return

        try:
            if self.client.is_connected:
                try:
                    await self.client.stop_notify(UART_NOTIFY)
                except Exception:
                    pass

                await self.client.disconnect()
                LOG.info("[BLE] Desconectado pelo Windows.")
        except Exception as e:
            LOG.info("[BLE] Finalização: %s", e)

    async def update_once(self):
        LOG.info("=" * 58)
        LOG.info("PRO10 Windows Bridge v%s", APP_VERSION)
        LOG.info("Atualização única, curta e econômica")
        LOG.info("=" * 58)

        device = await self.find_watch()
        if not device:
            LOG.warning(
                "[FIM] PRO 10 não encontrado após as tentativas. "
                "Nenhum loop infinito será iniciado."
            )
            return 2

        connected = await self.connect_with_retry(device)
        if not connected:
            LOG.warning("[FIM] Não foi possível conectar nesta execução.")
            return 3

        try:
            # Notify e identificacao
            try:
                await self.client.start_notify(UART_NOTIFY, self.on_notify)
                LOG.info("[BLE] Notify ativo.")
            except Exception as e:
                LOG.error("[ERRO] Não foi possível ativar notify: %s", e)
                return 4

            await self.read_battery()
            await self.read_optional_text(HARDWARE_UUID, "Hardware")
            await self.read_optional_text(FIRMWARE_UUID, "Firmware")

            # Handshake
            ok = await self.send_and_wait_ack(
                COMMAND_GET_FEATURE,
                0x00,
                build_feature_query(),
                "Capacidades 0x19/0x00"
            )
            if ok is False:
                return 5

            await self.read_optional_text(SOFTWARE_UUID, "Software")

            # Clima so agora: se o relogio nao conectou, nao gastamos rede.
            LOG.info("[CLIMA] Consultando %s...", CFG["city"])
            try:
                weather = await asyncio.to_thread(get_weather)
            except Exception as e:
                LOG.error("[ERRO] Falha ao obter clima: %s", e)
                return 6

            cur = weather["current"]
            LOG.info(
                "[CLIMA] %s | %d °C | %s",
                CFG["city"],
                round(float(cur["temperature_2m"])),
                weather_text(int(cur["weather_code"]))
            )

            # Hora
            await self.send_and_wait_ack(
                COMMAND_SETTING_ORDER,
                KEY_TIME,
                build_time_frame(datetime.now()),
                "Hora 0x02/0x01"
            )
            await asyncio.sleep(0.20)

            # 7 dias
            await self.send_and_wait_ack(
                COMMAND_SETTING_ORDER,
                KEY_WEATHER_7,
                build_weather7_frame(weather),
                "Previsão 7 dias 0x02/0x23"
            )
            await asyncio.sleep(0.20)

            # Atual
            current_ok = await self.send_and_wait_ack(
                COMMAND_SETTING_ORDER,
                KEY_WEATHER_CURRENT,
                build_weather_current_frame(weather),
                "Clima atual 0x02/0x1D"
            )

            if current_ok is False:
                return 7

            # O relogio pode levar um pequeno tempo para refletir os dados na tela.
            wait_after = float(CFG["post_send_wait_seconds"])
            LOG.info(
                "[BLE] Aguardando %.1fs para o relógio processar o clima...",
                wait_after
            )
            await asyncio.sleep(wait_after)

            LOG.info("[SUCESSO] Hora e clima enviados ao PRO 10.")
            return 0

        finally:
            await self.disconnect_cleanly()


async def async_main():
    bridge = Pro10Bridge()
    return await bridge.update_once()


def main():
    parser = argparse.ArgumentParser(
        description="Atualiza hora e clima do PRO 10 pelo Windows."
    )
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="Modo silencioso para execução pelo Agendador do Windows."
    )
    parser.add_argument(
        "--show-log",
        action="store_true",
        help="Mostra o caminho do log e sai."
    )
    args = parser.parse_args()

    if args.show_log:
        print(LOG_PATH)
        return 0

    # Em modo agendado, mantemos apenas o log em arquivo.
    if args.scheduled:
        global LOG
        LOG = setup_logging(verbose=False)

    mutex = SingleInstance()
    if not mutex.acquire():
        LOG.info("[FIM] Outra instância do PRO10 Bridge já está rodando.")
        return 9

    try:
        return asyncio.run(async_main())
    except KeyboardInterrupt:
        LOG.info("[FIM] Encerrado pelo usuário.")
        return 130
    except Exception as e:
        LOG.exception("[ERRO] Falha inesperada: %s", e)
        return 10
    finally:
        mutex.release()


if __name__ == "__main__":
    sys.exit(main())
