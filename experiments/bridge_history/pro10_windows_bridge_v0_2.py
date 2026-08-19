# PRO10 Windows Bridge v0.2
# PC <-> BLE <-> PRO 10
#
# Esta versao implementa o protocolo extraido do APK Olywear:
# - frame DF
# - checksum inserido no offset 3 = soma dos bytes do frame-base mod 256
# - command id SETTING_ORDER = 0x02
# - protocolo = 0x10 para este PRO 10
# - feature query = 0x19 / 0x00
# - hora = 0x02 / 0x01
# - previsao 7 dias = 0x02 / 0x23
# - clima atual = 0x02 / 0x1D
#
# Requisitos:
#   python -m pip install bleak
#
# Uso:
#   python .\pro10_windows_bridge_v0_2.py
#
# IMPORTANTE:
# - Esta versao NAO fica reconectando em loop.
# - Conecta uma vez, faz o handshake, envia hora + clima e desconecta.
# - Isso evita o ciclo agressivo da v0.1 e reduz o consumo de bateria.

import asyncio
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime
from bleak import BleakScanner, BleakClient

DEVICE_NAME = "PRO 10"

UART_NOTIFY = "6e400003-b5a3-f393-e0a9-e50e24dcca9f"
UART_WRITE  = "6e400002-b5a3-f393-e0a9-e50e24dcca9f"

BATTERY_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
SERIAL_UUID  = "00002a25-0000-1000-8000-00805f9b34fb"
HARDWARE_UUID = "00002a27-0000-1000-8000-00805f9b34fb"
FIRMWARE_UUID = "00002a26-0000-1000-8000-00805f9b34fb"
SOFTWARE_UUID = "00002a28-0000-1000-8000-00805f9b34fb"

# Sao Paulo/SP
LATITUDE = -23.5505
LONGITUDE = -46.6333
CITY_NAME = "São Paulo"

# Extraido/validado para este relogio.
PROTOCOL_VERSION = 0x10
COMMAND_SETTING_ORDER = 0x02
COMMAND_GET_FEATURE = 0x19

KEY_TIME = 0x01
KEY_WEATHER_CURRENT = 0x1D
KEY_WEATHER_7 = 0x23

ACK_HEADER = 0xFD
SUCCESS = 0x01

SCAN_TIMEOUT = 10.0
ACK_TIMEOUT = 2.5


def to_signed_byte(n: int) -> int:
    return n & 0xFF


def weather_code_for_watch(wmo: int) -> int:
    # O firmware/Olywear usa a familia de 9 icones:
    # 0 clear, 1 few clouds, 2 scattered clouds, 3 broken clouds,
    # 4 shower rain, 5 rain, 6 thunderstorm, 7 snow, 8 mist.
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
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "current": "temperature_2m,apparent_temperature,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min",
        "timezone": "auto",
        "forecast_days": 7,
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "PRO10-Windows-Bridge/0.2"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def build_frame(command_id: int, command_key: int, payload: bytes = b"") -> bytes:
    """
    Equivalente a IssuedUtil.getSendByte(...) + NotifyWriteUtils.addSumCheck(...)

    Frame-base extraido do APK:
      [0]  0xDF
      [1]  (payload_len + 5) >> 8
      [2]  (payload_len + 5) & 0xFF
      [3]  command_id
      [4]  protocol_version
      [5]  command_key
      [6]  payload_len >> 8
      [7]  payload_len & 0xFF
      [8:] payload

    Antes da escrita BLE, o Olywear insere em [3] o checksum:
      checksum = soma(frame-base) mod 256

    Portanto, no fio:
      DF lenH lenL checksum commandId protocol key dataLenH dataLenL payload...
    """
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
    return build_frame(COMMAND_GET_FEATURE, 0x00, b"")


def build_time_payload(now: datetime) -> bytes:
    # Compactacao exata de SettingIssuedUtils.settingSysTime().
    year_off = now.year - 2000
    month = now.month
    day = now.day
    hour = now.hour
    minute = now.minute
    second = now.second

    b0 = ((year_off << 2) + (month >> 2)) & 0xFF
    b1 = (((month & 0x03) << 6) + (day << 1) + (hour >> 4)) & 0xFF
    b2 = (((hour & 0x0F) << 4) + (minute >> 2)) & 0xFF
    b3 = (((minute & 0x03) << 6) + second) & 0xFF

    return bytes([b0, b1, b2, b3])


def build_time_frame(now: datetime) -> bytes:
    return build_frame(
        COMMAND_SETTING_ORDER,
        KEY_TIME,
        build_time_payload(now)
    )


def build_weather7_payload(weather: dict) -> bytes:
    daily = weather["daily"]
    count = min(
        7,
        len(daily["time"]),
        len(daily["weather_code"]),
        len(daily["temperature_2m_min"]),
        len(daily["temperature_2m_max"]),
    )

    payload = bytearray()
    payload.append(count & 0xFF)

    for i in range(count):
        d = datetime.strptime(daily["time"][i][:10], "%Y-%m-%d")
        year_off = d.year - 2000
        code = weather_code_for_watch(int(daily["weather_code"][i]))
        tmin = math.floor(float(daily["temperature_2m_min"][i]))
        tmax = math.ceil(float(daily["temperature_2m_max"][i]))

        # Exato de sendWeather7(): 7 bytes por dia
        payload.extend([
            (year_off >> 8) & 0xFF,
            year_off & 0xFF,
            d.month & 0xFF,
            d.day & 0xFF,
            code & 0xFF,
            to_signed_byte(tmin),
            to_signed_byte(tmax),
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

    # O APK usa a data do DayWeatherBean; usamos o dia atual da previsao.
    d = datetime.strptime(daily["time"][0][:10], "%Y-%m-%d")

    code = weather_code_for_watch(int(cur["weather_code"]))
    tmin = math.floor(float(daily["temperature_2m_min"][0]))
    tmax = math.ceil(float(daily["temperature_2m_max"][0]))
    current_temp = int(round(float(cur["temperature_2m"])))

    city = CITY_NAME.encode("utf-8")
    # O APK copia no maximo 50 bytes para o campo cidade.
    city = city[:50]

    payload = bytearray([
        (d.year >> 8) & 0xFF,
        d.year & 0xFF,
        d.month & 0xFF,
        d.day & 0xFF,
        0x00,
        code & 0xFF,
        to_signed_byte(tmin),
        to_signed_byte(tmax),
        len(city) & 0xFF,
    ])
    payload.extend(city)
    payload.append(to_signed_byte(current_temp))

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
        print("[BLE] Relógio desconectou.")
        self.disconnected.set()

    def on_notify(self, _sender, data):
        data = bytes(data)
        print("[RX]", data.hex(" ").upper())

        # Estrutura de ACK observada no APK:
        # [0] = FD
        # [4] = commandId
        # [5] = commandKey
        # [8] = status (1 = sucesso)
        if len(data) >= 9 and data[0] == ACK_HEADER:
            command_id = data[4]
            command_key = data[5]
            status = data[8]
            key = (command_id, command_key)
            fut = self.ack_waiters.get(key)

            print(
                f"[ACK] command=0x{command_id:02X} "
                f"key=0x{command_key:02X} status=0x{status:02X}"
            )

            if fut and not fut.done():
                fut.set_result(status)

    async def find_watch(self):
        print("[BLE] Procurando PRO 10...")
        devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT)

        for d in devices:
            if DEVICE_NAME.lower() in (d.name or "").lower():
                print("[BLE] Encontrado:", d)
                return d

        raise RuntimeError("PRO 10 não encontrado.")

    async def read_optional(self, uuid: str, label: str):
        try:
            value = await self.client.read_gatt_char(uuid)
            if uuid == BATTERY_UUID and value:
                print(f"[INFO] {label}: {int(value[0])}%")
            else:
                txt = bytes(value).decode("utf-8", errors="replace").strip("\x00")
                print(f"[INFO] {label}: {txt}")
            return bytes(value)
        except Exception as e:
            print(f"[INFO] {label}: leitura ignorada ({e})")
            return None

    async def send_and_wait_ack(
        self,
        command_id: int,
        command_key: int,
        frame: bytes,
        label: str,
        timeout: float = ACK_TIMEOUT,
    ):
        if not self.client or not self.client.is_connected:
            raise RuntimeError("Relógio não está conectado.")

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        key = (command_id, command_key)
        self.ack_waiters[key] = fut

        print(f"\n[TX] {label}")
        print("[TX]", frame.hex(" ").upper())

        try:
            await self.client.write_gatt_char(
                UART_WRITE,
                frame,
                response=False
            )

            try:
                status = await asyncio.wait_for(fut, timeout=timeout)
                if status == SUCCESS:
                    print(f"[OK] {label}: ACK de sucesso.")
                    return True

                print(
                    f"[AVISO] {label}: ACK recebido com "
                    f"status 0x{status:02X}."
                )
                return False

            except asyncio.TimeoutError:
                # Alguns firmwares aceitam o comando e nao entregam
                # o ACK a tempo. Nao entramos em loop nem reconectamos.
                print(f"[AVISO] {label}: ACK não chegou em {timeout:.1f}s.")
                return None

        finally:
            self.ack_waiters.pop(key, None)

    async def run(self):
        print("=" * 62)
        print(" PRO10 WINDOWS BRIDGE v0.2")
        print(" Handshake + hora + clima atual + previsão 7 dias")
        print("=" * 62)

        # Consulta o clima antes de abrir BLE, reduzindo tempo de radio ativo.
        print("[CLIMA] Consultando São Paulo...")
        weather = await asyncio.to_thread(get_weather)

        cur = weather["current"]
        print(
            f"[CLIMA] {round(cur['temperature_2m'])} C | "
            f"{weather_text(int(cur['weather_code']))}"
        )

        device = await self.find_watch()

        self.client = BleakClient(
            device,
            disconnected_callback=self.on_disconnect,
        )

        try:
            print("[BLE] Conectando...")
            await self.client.connect()

            if not self.client.is_connected:
                raise RuntimeError("Falha ao conectar.")

            print("[BLE] Conectado.")
            await asyncio.sleep(1.0)

            # Notify primeiro, como no fluxo do Olywear.
            await self.client.start_notify(UART_NOTIFY, self.on_notify)
            print("[BLE] Notify ativo.")

            # Leitura de identificacao usada no handshake.
            await self.read_optional(BATTERY_UUID, "Bateria")
            await self.read_optional(HARDWARE_UUID, "Hardware")
            await self.read_optional(FIRMWARE_UUID, "Firmware")

            # 1) Capacidades: 0x19 / 0x00
            await self.send_and_wait_ack(
                COMMAND_GET_FEATURE,
                0x00,
                build_feature_query(),
                "Consulta de capacidades 0x19/0x00",
            )

            # O fluxo do APK lê 2A28 depois do ACK de features.
            await self.read_optional(SOFTWARE_UUID, "Software")
            await asyncio.sleep(0.25)

            # 2) Hora
            now = datetime.now()
            await self.send_and_wait_ack(
                COMMAND_SETTING_ORDER,
                KEY_TIME,
                build_time_frame(now),
                "Sincronização de hora 0x02/0x01",
            )
            await asyncio.sleep(0.25)

            # 3) Previsao de 7 dias
            await self.send_and_wait_ack(
                COMMAND_SETTING_ORDER,
                KEY_WEATHER_7,
                build_weather7_frame(weather),
                "Previsão de 7 dias 0x02/0x23",
            )
            await asyncio.sleep(0.25)

            # 4) Clima atual
            await self.send_and_wait_ack(
                COMMAND_SETTING_ORDER,
                KEY_WEATHER_CURRENT,
                build_weather_current_frame(weather),
                "Clima atual 0x02/0x1D",
            )

            print("\n" + "=" * 62)
            print(" ENVIO FINALIZADO")
            print(" O programa vai encerrar a conexão BLE para poupar bateria.")
            print("=" * 62)

            await asyncio.sleep(0.75)

        finally:
            try:
                if self.client and self.client.is_connected:
                    try:
                        await self.client.stop_notify(UART_NOTIFY)
                    except Exception:
                        pass
                    await self.client.disconnect()
                    print("[BLE] Desconectado pelo PC.")
            except Exception:
                pass


async def main():
    bridge = Pro10Bridge()
    await bridge.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nEncerrado pelo usuário.")
    except Exception as e:
        print(f"\n[ERRO] {type(e).__name__}: {e}")
