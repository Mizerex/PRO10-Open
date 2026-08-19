from __future__ import annotations

import argparse
import asyncio
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_DOWN


ADDRESS = "AA:BB:CC:DD:EE:FF"
WRITE_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9f"
NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9f"

HEADER = 0xDF
COMMAND_SETTING = 0x02
PROTOCOL_VERSION = 0x01
KEY_CURRENT_WEATHER = 0x1D
ACK_HEADER = 0xFD
ACK_SUCCESS = 0x01
ACK_TIMEOUT_SECONDS = 10.0
MAX_CITY_BYTES = 50
BLE_CHUNK_SIZE = 20


ICON_CODES = {
    "01d": 0,
    "01n": 0,
    "02d": 1,
    "02n": 1,
    "03d": 2,
    "03n": 2,
    "04d": 3,
    "04n": 3,
    "09d": 4,
    "09n": 4,
    "10d": 5,
    "10n": 5,
    "11d": 6,
    "11n": 6,
    "13d": 7,
    "13n": 7,
    "50d": 8,
    "50n": 8,
}

CONDITION_CODES = {
    "ceu limpo": 0,
    "limpo": 0,
    "sol": 0,
    "ensolarado": 0,
    "clear": 0,
    "poucas nuvens": 1,
    "parcialmente nublado": 1,
    "partly cloudy": 1,
    "nuvens dispersas": 2,
    "scattered clouds": 2,
    "nublado": 3,
    "cloudy": 3,
    "pancadas de chuva": 4,
    "chuva forte": 4,
    "showers": 4,
    "chuva": 5,
    "rain": 5,
    "tempestade": 6,
    "trovoada": 6,
    "thunderstorm": 6,
    "neve": 7,
    "snow": 7,
    "nevoa": 8,
    "neblina": 8,
    "fog": 8,
    "mist": 8,
}


def format_hex(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def parse_condition(value: str) -> int:
    raw = value.strip().lower()
    if raw in ICON_CODES:
        return ICON_CODES[raw]

    try:
        code = int(raw, 0)
    except ValueError:
        code = CONDITION_CODES.get(normalize_text(value), -1)

    if not 0 <= code <= 8:
        raise ValueError(
            "Condição inválida. Use um código de 0 a 8, um ícone como 10d, "
            "ou um nome como limpo, nublado, chuva, tempestade, neve ou névoa."
        )
    return code


def parse_temperature(value: str) -> Decimal:
    try:
        temperature = Decimal(value.replace(",", "."))
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("Temperatura inválida.") from error

    if not temperature.is_finite():
        raise argparse.ArgumentTypeError("A temperatura deve ser um número finito.")
    return temperature


def require_signed_int8(value: int, field: str) -> int:
    if not -128 <= value <= 127:
        raise ValueError(f"{field} fora do intervalo int8 com sinal: {value}.")
    return value & 0xFF


def signed_int8(value: int) -> int:
    return value - 256 if value >= 128 else value


def calculate_checksum(packet_without_checksum: bytes) -> int:
    return sum(packet_without_checksum) & 0xFF


def temperature_fields(temperature: Decimal) -> tuple[int, int, int]:
    minimum = int(temperature.to_integral_value(rounding=ROUND_FLOOR))
    maximum = int(temperature.to_integral_value(rounding=ROUND_CEILING))
    current = int(temperature.to_integral_value(rounding=ROUND_DOWN))

    require_signed_int8(minimum, "Temperatura mínima")
    require_signed_int8(maximum, "Temperatura máxima")
    require_signed_int8(current, "Temperatura atual")
    return minimum, maximum, current


def build_weather_packet(
    city: str,
    temperature: Decimal,
    condition_code: int,
    weather_date: date | None = None,
) -> bytes:
    if not 0 <= condition_code <= 8:
        raise ValueError("O código meteorológico deve estar entre 0 e 8.")

    city_bytes = city.encode("utf-8")
    if len(city_bytes) > MAX_CITY_BYTES:
        raise ValueError(
            f"A cidade ocupa {len(city_bytes)} bytes UTF-8; o limite seguro é 50."
        )

    minimum, maximum, current = temperature_fields(temperature)
    value_date = weather_date or date.today()

    payload = bytes(
        (
            (value_date.year >> 8) & 0xFF,
            value_date.year & 0xFF,
            value_date.month,
            value_date.day,
            0x00,
            condition_code,
            require_signed_int8(minimum, "Temperatura mínima"),
            require_signed_int8(maximum, "Temperatura máxima"),
            len(city_bytes),
        )
    ) + city_bytes + bytes((require_signed_int8(current, "Temperatura atual"),))

    payload_length = len(payload)
    outer_length = payload_length + 5
    packet_without_checksum = bytes(
        (
            HEADER,
            (outer_length >> 8) & 0xFF,
            outer_length & 0xFF,
            COMMAND_SETTING,
            PROTOCOL_VERSION,
            KEY_CURRENT_WEATHER,
            (payload_length >> 8) & 0xFF,
            payload_length & 0xFF,
        )
    ) + payload

    checksum = calculate_checksum(packet_without_checksum)
    packet = packet_without_checksum[:3] + bytes((checksum,)) + packet_without_checksum[3:]
    validate_weather_packet(packet)
    return packet


def validate_weather_packet(packet: bytes) -> None:
    if len(packet) < 19:
        raise ValueError("Pacote de clima incompleto.")
    if packet[0] != HEADER:
        raise ValueError("Header inválido.")

    outer_length = int.from_bytes(packet[1:3], "big")
    payload_length = int.from_bytes(packet[7:9], "big")
    if outer_length != payload_length + 5:
        raise ValueError("Comprimento externo incompatível com o payload.")
    if len(packet) != payload_length + 9:
        raise ValueError("Tamanho real incompatível com o cabeçalho.")
    if packet[4:7] != bytes((COMMAND_SETTING, PROTOCOL_VERSION, KEY_CURRENT_WEATHER)):
        raise ValueError("Comando de clima atual inválido.")

    expected_checksum = calculate_checksum(packet[:3] + packet[4:])
    if packet[3] != expected_checksum:
        raise ValueError(
            f"Checksum inválido: {packet[3]:02X}; esperado: {expected_checksum:02X}."
        )

    payload = packet[9:]
    city_length = payload[8]
    if city_length > MAX_CITY_BYTES:
        raise ValueError("Comprimento da cidade excede o limite seguro.")
    if payload_length != city_length + 10:
        raise ValueError("Comprimento da cidade incompatível com o payload.")

    year = int.from_bytes(payload[0:2], "big")
    date(year, payload[2], payload[3])
    if payload[4] != 0:
        raise ValueError("Byte reservado do clima deve ser zero.")
    if not 0 <= payload[5] <= 8:
        raise ValueError("Código meteorológico inválido no pacote.")
    payload[9 : 9 + city_length].decode("utf-8")

    for offset, field in ((6, "mínima"), (7, "máxima"), (9 + city_length, "atual")):
        value = signed_int8(payload[offset])
        if not -128 <= value <= 127:
            raise ValueError(f"Temperatura {field} inválida.")


def ack_checksum_is_valid(data: bytes) -> bool:
    return len(data) == 9 and data[3] == calculate_checksum(data[:3] + data[4:])


def is_success_weather_ack(data: bytes) -> bool:
    return (
        len(data) == 9
        and data[0] == ACK_HEADER
        and ack_checksum_is_valid(data)
        and data[4] == COMMAND_SETTING
        and data[5] == KEY_CURRENT_WEATHER
        and data[8] == ACK_SUCCESS
    )


async def send_weather(packet: bytes) -> None:
    from bleak import BleakClient

    ack_event = asyncio.Event()
    ack_packet: bytes | None = None

    def notification_handler(_sender, data: bytearray) -> None:
        nonlocal ack_packet
        received = bytes(data)
        print("Notificação recebida:", format_hex(received))
        if len(received) >= 6 and received[0] == ACK_HEADER:
            if received[4] == COMMAND_SETTING and received[5] == KEY_CURRENT_WEATHER:
                ack_packet = received
                ack_event.set()

    async with BleakClient(ADDRESS) as client:
        if not client.is_connected:
            raise RuntimeError("Não foi possível conectar ao PRO 10.")

        await client.start_notify(NOTIFY_UUID, notification_handler)
        try:
            for offset in range(0, len(packet), BLE_CHUNK_SIZE):
                chunk = packet[offset : offset + BLE_CHUNK_SIZE]
                await client.write_gatt_char(WRITE_UUID, chunk, response=False)
                if offset + BLE_CHUNK_SIZE < len(packet):
                    await asyncio.sleep(0.02)

            print("Pacote enviado; aguardando ACK...")
            await asyncio.wait_for(ack_event.wait(), timeout=ACK_TIMEOUT_SECONDS)
        except TimeoutError as error:
            raise RuntimeError("O PRO 10 não respondeu com ACK em 10 segundos.") from error
        finally:
            await client.stop_notify(NOTIFY_UUID)

    if ack_packet is None or not is_success_weather_ack(ack_packet):
        received = "ausente" if ack_packet is None else format_hex(ack_packet)
        raise RuntimeError(f"ACK de clima inválido ou sem sucesso: {received}.")

    print("ACK de clima confirmado: comando 0x02, chave 0x1D, status 0x01.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monta o pacote de clima atual do PRO 10 em modo seguro."
    )
    parser.add_argument("--city", required=True, help="cidade enviada em UTF-8")
    parser.add_argument(
        "--temperature",
        required=True,
        type=parse_temperature,
        help="temperatura atual em graus Celsius",
    )
    parser.add_argument(
        "--condition",
        required=True,
        help="condição, código 0–8 ou ícone meteorológico como 10d",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="autoriza conectar e transmitir o pacote ao relógio",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        condition_code = parse_condition(args.condition)
        packet = build_weather_packet(args.city, args.temperature, condition_code)
    except ValueError as error:
        raise SystemExit(f"Erro: {error}") from error

    minimum, maximum, current = temperature_fields(args.temperature)
    print("Cidade:", args.city)
    print("Temperatura informada:", args.temperature)
    print(f"Temperaturas codificadas: mínima={minimum}, máxima={maximum}, atual={current}")
    print(f"Condição: {args.condition} (código {condition_code})")
    print("Pacote:", format_hex(packet))
    print("Checksum:", f"{packet[3]:02X}")
    print("Tamanho:", len(packet), "bytes")

    if not args.send:
        print("Modo seguro: nenhum dado foi enviado por Bluetooth.")
        return

    asyncio.run(send_weather(packet))


if __name__ == "__main__":
    main()
