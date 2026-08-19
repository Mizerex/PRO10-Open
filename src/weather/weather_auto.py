from __future__ import annotations

import argparse
import asyncio
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ADDRESS = "AA:BB:CC:DD:EE:FF"
WRITE_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9f"
NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9f"

CITY = "São Paulo"
LOCATION = "São Paulo, SP, Brasil"
LATITUDE = -23.5505
LONGITUDE = -46.6333
TIMEZONE = "America/Sao_Paulo"

HEADER = 0xDF
ACK_HEADER = 0xFD
COMMAND_SETTING = 0x02
PROTOCOL_VERSION = 0x10
KEY_CURRENT_WEATHER = 0x1D
KEY_WEATHER_7 = 0x23
ACK_SUCCESS = 0x01
ACK_TIMEOUT_SECONDS = 10.0
HTTP_TIMEOUT_SECONDS = 15.0
BLE_CHUNK_SIZE = 20
MAX_CITY_BYTES = 50
CONNECTION_STABILIZATION_SECONDS = 15.0

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Conversão explícita por significado meteorológico. Os números WMO não são
# reutilizados como se fossem códigos nativos do relógio.
WMO_TO_PRO10 = {
    0: 0,  # céu limpo
    1: 1,  # predominantemente limpo / poucas nuvens
    2: 2,  # parcialmente nublado / nuvens dispersas
    3: 3,  # encoberto
    45: 8, 48: 8,  # nevoeiro e nevoeiro com geada
    51: 4, 53: 4, 55: 4, 56: 4, 57: 4,  # garoa / precipitação em pancadas leves
    61: 5, 63: 5, 65: 5, 66: 5, 67: 5,  # chuva contínua / chuva congelante
    71: 7, 73: 7, 75: 7, 77: 7,  # neve e grãos de neve
    80: 4, 81: 4, 82: 4,  # pancadas de chuva
    85: 7, 86: 7,  # pancadas de neve
    95: 6, 96: 6, 99: 6,  # trovoada, com ou sem granizo
}

WMO_DESCRIPTIONS = {
    0: "céu limpo", 1: "predominantemente limpo", 2: "parcialmente nublado",
    3: "encoberto", 45: "nevoeiro", 48: "nevoeiro com geada",
    51: "garoa leve", 53: "garoa moderada", 55: "garoa intensa",
    56: "garoa congelante leve", 57: "garoa congelante intensa",
    61: "chuva leve", 63: "chuva moderada", 65: "chuva forte",
    66: "chuva congelante leve", 67: "chuva congelante forte",
    71: "neve leve", 73: "neve moderada", 75: "neve forte",
    77: "grãos de neve", 80: "pancadas de chuva leves",
    81: "pancadas de chuva moderadas", 82: "pancadas de chuva fortes",
    85: "pancadas de neve leves", 86: "pancadas de neve fortes",
    95: "trovoada", 96: "trovoada com granizo leve",
    99: "trovoada com granizo forte",
}

PRO10_DESCRIPTIONS = {
    0: "céu limpo", 1: "poucas nuvens", 2: "nuvens dispersas",
    3: "nublado", 4: "pancadas", 5: "chuva", 6: "tempestade",
    7: "neve", 8: "névoa",
}


@dataclass(frozen=True)
class ForecastDay:
    value_date: date
    minimum: float
    maximum: float
    wmo_code: int
    pro10_code: int


@dataclass(frozen=True)
class WeatherData:
    current_time: datetime
    current_temperature: float
    current_wmo_code: int
    current_pro10_code: int
    forecast: tuple[ForecastDay, ...]


def format_hex(data: bytes) -> str:
    return data.hex(" ").upper()


def checksum(data_without_checksum: bytes) -> int:
    return sum(data_without_checksum) & 0xFF


def signed_byte(value: int, field: str) -> int:
    if not -128 <= value <= 127:
        raise ValueError(f"{field} fora do intervalo int8: {value}")
    return value & 0xFF


def numeric(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} não é numérico")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} não é finito")
    return result


def wmo_value(value: object, field: str) -> int:
    number = numeric(value, field)
    if not number.is_integer():
        raise ValueError(f"{field} não é inteiro: {number}")
    result = int(number)
    if result not in WMO_TO_PRO10:
        raise ValueError(f"{field} não possui mapeamento seguro: WMO {result}")
    return result


def require_list(value: object, field: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{field} não é uma lista")
    return value


def fetch_weather() -> WeatherData:
    query = urlencode(
        {
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "current": "temperature_2m,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min",
            "timezone": TIMEZONE,
            "forecast_days": 7,
        }
    )
    request = Request(
        OPEN_METEO_URL + "?" + query,
        headers={"User-Agent": "PRO10-Open/1.0"},
    )
    with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise RuntimeError(f"Open-Meteo respondeu HTTP {response.status}")
        document = json.loads(response.read().decode("utf-8"))

    if not isinstance(document, dict):
        raise ValueError("Resposta do Open-Meteo não é um objeto JSON")
    if document.get("timezone") != TIMEZONE:
        raise ValueError(f"Timezone inesperado: {document.get('timezone')!r}")

    current = document.get("current")
    daily = document.get("daily")
    if not isinstance(current, dict) or not isinstance(daily, dict):
        raise ValueError("Resposta sem blocos current/daily válidos")

    current_time = datetime.fromisoformat(str(current.get("time")))
    current_temperature = numeric(current.get("temperature_2m"), "temperatura atual")
    current_wmo = wmo_value(current.get("weather_code"), "weather_code atual")

    dates = require_list(daily.get("time"), "daily.time")
    minimums = require_list(daily.get("temperature_2m_min"), "daily.temperature_2m_min")
    maximums = require_list(daily.get("temperature_2m_max"), "daily.temperature_2m_max")
    codes = require_list(daily.get("weather_code"), "daily.weather_code")
    if not (len(dates) == len(minimums) == len(maximums) == len(codes) == 7):
        raise ValueError("A previsão não contém exatamente sete dias completos")

    forecast: list[ForecastDay] = []
    for index in range(7):
        value_date = date.fromisoformat(str(dates[index]))
        if index and value_date != forecast[-1].value_date + timedelta(days=1):
            raise ValueError("As datas da previsão não são consecutivas")
        minimum = numeric(minimums[index], f"mínima do dia {index + 1}")
        maximum = numeric(maximums[index], f"máxima do dia {index + 1}")
        if minimum > maximum:
            raise ValueError(f"Mínima maior que máxima no dia {value_date.isoformat()}")
        wmo = wmo_value(codes[index], f"weather_code do dia {index + 1}")
        forecast.append(
            ForecastDay(value_date, minimum, maximum, wmo, WMO_TO_PRO10[wmo])
        )

    if forecast[0].value_date != current_time.date():
        raise ValueError("O primeiro registro diário não corresponde ao dia atual")

    return WeatherData(
        current_time=current_time,
        current_temperature=current_temperature,
        current_wmo_code=current_wmo,
        current_pro10_code=WMO_TO_PRO10[current_wmo],
        forecast=tuple(forecast),
    )


def build_frame(key: int, payload: bytes) -> bytes:
    payload_length = len(payload)
    outer_length = payload_length + 5
    without_checksum = bytes(
        (
            HEADER,
            (outer_length >> 8) & 0xFF,
            outer_length & 0xFF,
            COMMAND_SETTING,
            PROTOCOL_VERSION,
            key,
            (payload_length >> 8) & 0xFF,
            payload_length & 0xFF,
        )
    ) + payload
    packet = without_checksum[:3] + bytes((checksum(without_checksum),)) + without_checksum[3:]
    validate_tx(packet, key)
    return packet


def build_forecast_packet(days: tuple[ForecastDay, ...]) -> bytes:
    if len(days) != 7:
        raise ValueError("0x23 exige exatamente sete dias")
    payload = bytearray((7,))
    for item in days:
        year_offset = item.value_date.year - 2000
        if not 0 <= year_offset <= 0xFFFF:
            raise ValueError(f"Ano incompatível com 0x23: {item.value_date.year}")
        minimum = math.floor(item.minimum)
        maximum = math.ceil(item.maximum)
        payload.extend(
            (
                (year_offset >> 8) & 0xFF,
                year_offset & 0xFF,
                item.value_date.month,
                item.value_date.day,
                item.pro10_code,
                signed_byte(minimum, "temperatura mínima"),
                signed_byte(maximum, "temperatura máxima"),
            )
        )
    return build_frame(KEY_WEATHER_7, bytes(payload))


def build_current_packet(data: WeatherData) -> bytes:
    city_bytes = CITY.encode("utf-8")
    if not city_bytes or len(city_bytes) > MAX_CITY_BYTES:
        raise ValueError("Cidade vazia ou maior que 50 bytes UTF-8")
    today = data.forecast[0]
    minimum = math.floor(today.minimum)
    maximum = math.ceil(today.maximum)
    current = math.trunc(data.current_temperature)
    value_date = data.current_time.date()
    payload = bytes(
        (
            (value_date.year >> 8) & 0xFF,
            value_date.year & 0xFF,
            value_date.month,
            value_date.day,
            0,
            data.current_pro10_code,
            signed_byte(minimum, "temperatura mínima atual"),
            signed_byte(maximum, "temperatura máxima atual"),
            len(city_bytes),
        )
    ) + city_bytes + bytes((signed_byte(current, "temperatura atual"),))
    return build_frame(KEY_CURRENT_WEATHER, payload)


def checksum_valid(packet: bytes) -> bool:
    return len(packet) >= 4 and packet[3] == checksum(packet[:3] + packet[4:])


def validate_tx(packet: bytes, key: int) -> None:
    if len(packet) < 9 or packet[0] != HEADER:
        raise ValueError(f"TX 0x{key:02X}: cabeçalho inválido")
    if packet[4:7] != bytes((COMMAND_SETTING, PROTOCOL_VERSION, key)):
        raise ValueError(f"TX 0x{key:02X}: comando, versão ou chave inválidos")
    payload_length = int.from_bytes(packet[7:9], "big")
    outer_length = int.from_bytes(packet[1:3], "big")
    if outer_length != payload_length + 5 or len(packet) != payload_length + 9:
        raise ValueError(f"TX 0x{key:02X}: comprimento inválido")
    if not checksum_valid(packet):
        raise ValueError(f"TX 0x{key:02X}: checksum inválido")


def validate_ack(packet: bytes, key: int) -> None:
    if len(packet) != 9 or packet[0] != ACK_HEADER:
        raise ValueError(f"ACK 0x{key:02X}: formato inválido")
    if int.from_bytes(packet[1:3], "big") != 5 or not checksum_valid(packet):
        raise ValueError(f"ACK 0x{key:02X}: comprimento ou checksum inválido")
    if packet[4] != COMMAND_SETTING or packet[5] != key:
        raise ValueError(f"ACK 0x{key:02X}: comando ou chave inválidos")
    if packet[8] != ACK_SUCCESS:
        raise ValueError(f"ACK 0x{key:02X}: status 0x{packet[8]:02X}")


def show_weather(data: WeatherData, packet_23: bytes, packet_1d: bytes) -> None:
    print(f"Cidade: {LOCATION}")
    print(f"Timezone: {TIMEZONE}")
    print(
        "Clima atual: "
        f"{data.current_time.isoformat(timespec='minutes')}; "
        f"{data.current_temperature:.1f} °C; "
        f"WMO {data.current_wmo_code} ({WMO_DESCRIPTIONS[data.current_wmo_code]}) -> "
        f"PRO 10 {data.current_pro10_code} ({PRO10_DESCRIPTIONS[data.current_pro10_code]})"
    )
    print("Previsão recebida:")
    for item in data.forecast:
        print(
            f"  {item.value_date.isoformat()}: mín {item.minimum:.1f} °C; "
            f"máx {item.maximum:.1f} °C; WMO {item.wmo_code} "
            f"({WMO_DESCRIPTIONS[item.wmo_code]}) -> PRO 10 {item.pro10_code} "
            f"({PRO10_DESCRIPTIONS[item.pro10_code]})"
        )
    print("PACOTE 0x23: " + format_hex(packet_23))
    print("PACOTE 0x1D: " + format_hex(packet_1d))


async def send_sequence_once(packet_23: bytes, packet_1d: bytes) -> None:
    from bleak import BleakClient

    expected_key = KEY_WEATHER_7
    ack_event = asyncio.Event()
    ack_packet: bytes | None = None

    def on_notify(_sender, data: bytearray) -> None:
        nonlocal ack_packet
        packet = bytes(data)
        print("RX NOTIFY: " + format_hex(packet), flush=True)
        if (
            len(packet) >= 6
            and packet[0] == ACK_HEADER
            and packet[4] == COMMAND_SETTING
            and packet[5] == expected_key
        ):
            ack_packet = packet
            ack_event.set()

    async with BleakClient(ADDRESS, timeout=20.0) as client:
        await client.start_notify(NOTIFY_UUID, on_notify)
        print("NOTIFY ATIVO: " + NOTIFY_UUID, flush=True)
        print(
            "ESTABILIZAÇÃO BLE: aguardando "
            f"{CONNECTION_STABILIZATION_SECONDS:.0f} segundos antes do clima",
            flush=True,
        )
        await asyncio.sleep(CONNECTION_STABILIZATION_SECONDS)

        async def send_one(packet: bytes, key: int) -> None:
            nonlocal expected_key, ack_packet
            expected_key = key
            ack_packet = None
            ack_event.clear()
            print(f"TX 0x{key:02X}: " + format_hex(packet), flush=True)
            for offset in range(0, len(packet), BLE_CHUNK_SIZE):
                chunk = packet[offset : offset + BLE_CHUNK_SIZE]
                await client.write_gatt_char(WRITE_UUID, chunk, response=False)
                if offset + BLE_CHUNK_SIZE < len(packet):
                    await asyncio.sleep(0.02)
            try:
                await asyncio.wait_for(ack_event.wait(), ACK_TIMEOUT_SECONDS)
            except TimeoutError as exc:
                raise RuntimeError(f"ACK 0x{key:02X} não recebido em 10 segundos") from exc
            assert ack_packet is not None
            validate_ack(ack_packet, key)
            print(f"ACK 0x{key:02X}: SUCESSO 0x01; " + format_hex(ack_packet), flush=True)

        try:
            await send_one(packet_23, KEY_WEATHER_7)
            await send_one(packet_1d, KEY_CURRENT_WEATHER)
        finally:
            await client.stop_notify(NOTIFY_UUID)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sincronização meteorológica do PRO10-Open")
    parser.add_argument("--send", action="store_true", help="envia uma única sequência BLE")
    args = parser.parse_args()

    try:
        data = fetch_weather()
        packet_23 = build_forecast_packet(data.forecast)
        packet_1d = build_current_packet(data)
        show_weather(data, packet_23, packet_1d)
    except Exception as exc:
        print(f"CONSULTA/MONTAGEM: ERRO; nenhum clima enviado; {type(exc).__name__}: {exc}")
        return 2

    if not args.send:
        print("MODO SEGURO: nenhum dado BLE foi enviado.")
        return 0

    try:
        asyncio.run(send_sequence_once(packet_23, packet_1d))
    except Exception as exc:
        print(f"RESULTADO BLE: ERRO; sem repetição; {type(exc).__name__}: {exc}")
        return 1

    print("RESULTADO BLE: SUCESSO; 0x23 -> ACK 0x01 -> 0x1D -> ACK 0x01")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
