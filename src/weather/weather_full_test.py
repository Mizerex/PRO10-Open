from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from logging.handlers import RotatingFileHandler
from pathlib import Path

from weather_sync import (
    ACK_HEADER,
    ACK_SUCCESS,
    ADDRESS,
    COMMAND_SETTING,
    HEADER,
    KEY_CURRENT_WEATHER,
    NOTIFY_UUID,
    PROTOCOL_VERSION,
    WRITE_UUID,
    build_weather_packet,
    calculate_checksum,
    format_hex,
    require_signed_int8,
)


COMMAND_GET_FEATURES = 0x19
KEY_GET_FEATURES = 0x00
KEY_WEATHER_7 = 0x23
FEATURE_WEATHER_7_OFFSET = 18
FEATURE_WEATHER_7_MASK = 0x08
ACK_TIMEOUT_SECONDS = 10.0
FEATURE_TIMEOUT_SECONDS = 3.0
BLE_CHUNK_SIZE = 20

PROJECT_DIR = Path(__file__).resolve().parent
LOG_FILE = PROJECT_DIR / "weather_full_test.log"
PLAN_FILE = PROJECT_DIR / "weather_test_plan.json"


logger = logging.getLogger("PRO10-Weather-Full-Test")
logger.setLevel(logging.INFO)
handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=512_000,
    backupCount=2,
    encoding="utf-8",
)
handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
logger.addHandler(handler)


@dataclass(frozen=True)
class ForecastDay:
    value_date: date
    condition_code: int
    minimum: int
    maximum: int


def log_and_print(message: str) -> None:
    print(message)
    logger.info(message)


def build_frame(command_id: int, key: int, payload: bytes = b"") -> bytes:
    payload_length = len(payload)
    outer_length = payload_length + 5
    packet_without_checksum = bytes(
        (
            HEADER,
            (outer_length >> 8) & 0xFF,
            outer_length & 0xFF,
            command_id,
            PROTOCOL_VERSION,
            key,
            (payload_length >> 8) & 0xFF,
            payload_length & 0xFF,
        )
    ) + payload
    checksum = calculate_checksum(packet_without_checksum)
    return packet_without_checksum[:3] + bytes((checksum,)) + packet_without_checksum[3:]


def validate_frame(packet: bytes, command_id: int, key: int) -> bytes:
    if len(packet) < 9 or packet[0] != HEADER:
        raise ValueError("Pacote enquadrado inválido.")
    payload_length = int.from_bytes(packet[7:9], "big")
    outer_length = int.from_bytes(packet[1:3], "big")
    if outer_length != payload_length + 5 or len(packet) != payload_length + 9:
        raise ValueError("Comprimento do pacote inválido.")
    if packet[4] != command_id or packet[5] != PROTOCOL_VERSION or packet[6] != key:
        raise ValueError("Comando, versão ou chave inválidos.")
    if packet[3] != calculate_checksum(packet[:3] + packet[4:]):
        raise ValueError("Checksum do pacote inválido.")
    return packet[9:]


def build_feature_query() -> bytes:
    packet = build_frame(COMMAND_GET_FEATURES, KEY_GET_FEATURES)
    validate_frame(packet, COMMAND_GET_FEATURES, KEY_GET_FEATURES)
    return packet


def build_test_forecast(base_date: date) -> list[ForecastDay]:
    # Dados controlados e coerentes para um cenário costeiro de São Paulo.
    values = (
        (5, 20, 25),
        (4, 20, 24),
        (3, 19, 24),
        (1, 19, 25),
        (0, 20, 26),
        (0, 21, 27),
        (1, 21, 27),
    )
    return [
        ForecastDay(base_date + timedelta(days=index), condition, minimum, maximum)
        for index, (condition, minimum, maximum) in enumerate(values)
    ]


def build_weather_7_packet(days: list[ForecastDay]) -> bytes:
    if not days or len(days) > 255:
        raise ValueError("A previsão deve conter entre 1 e 255 registros.")

    payload = bytearray((len(days),))
    for item in days:
        if not 0 <= item.condition_code <= 8:
            raise ValueError("Código meteorológico fora do intervalo 0–8.")
        year_offset = item.value_date.year - 2000
        if not 0 <= year_offset <= 255:
            raise ValueError("Ano fora do intervalo aceito pelo pacote 0x23.")

        # Reprodução literal do APK: o byte alto não é deslocado e resulta em 00.
        year_high_apk = (0xFF00 & year_offset) & 0xFF
        payload.extend(
            (
                year_high_apk,
                year_offset & 0xFF,
                item.value_date.month,
                item.value_date.day,
                item.condition_code,
                require_signed_int8(item.minimum, "Temperatura mínima"),
                require_signed_int8(item.maximum, "Temperatura máxima"),
            )
        )

    packet = build_frame(COMMAND_SETTING, KEY_WEATHER_7, bytes(payload))
    parsed_payload = validate_frame(packet, COMMAND_SETTING, KEY_WEATHER_7)
    if len(parsed_payload) != 1 + (7 * parsed_payload[0]):
        raise ValueError("Quantidade de registros incompatível com o payload 0x23.")
    return packet


def ack_is_valid(data: bytes, command_id: int, key: int) -> bool:
    return (
        len(data) == 9
        and data[0] == ACK_HEADER
        and data[3] == calculate_checksum(data[:3] + data[4:])
        and data[4] == command_id
        and data[5] == key
        and data[8] == ACK_SUCCESS
    )


def build_ack_to_device(command_id: int, key: int, status: int = ACK_SUCCESS) -> bytes:
    packet_without_checksum = bytes(
        (ACK_HEADER, 0x00, 0x05, command_id, key, 0x00, 0x00, status)
    )
    checksum = calculate_checksum(packet_without_checksum)
    packet = packet_without_checksum[:3] + bytes((checksum,)) + packet_without_checksum[3:]
    if not ack_is_valid(packet, command_id, key):
        raise ValueError("Falha ao montar ACK para o relógio.")
    return packet


class NotificationCollector:
    def __init__(self) -> None:
        self.feature_ack_event = asyncio.Event()
        self.weather_7_ack_event = asyncio.Event()
        self.current_weather_ack_event = asyncio.Event()
        self.feature_event = asyncio.Event()
        self.feature_bytes: bytes | None = None
        self.feature_ack: bytes | None = None
        self.weather_7_ack: bytes | None = None
        self.current_weather_ack: bytes | None = None
        self._frame_buffer = bytearray()
        self._expected_frame_length: int | None = None

    def callback(self, _sender, data: bytearray) -> None:
        received = bytes(data)
        log_and_print("RX NOTIFY: " + format_hex(received))

        if received and received[0] == ACK_HEADER:
            if len(received) >= 6 and received[4] == COMMAND_GET_FEATURES:
                if received[5] == KEY_GET_FEATURES:
                    self.feature_ack = received
                    self.feature_ack_event.set()
            elif len(received) >= 6 and received[4] == COMMAND_SETTING:
                if received[5] == KEY_WEATHER_7:
                    self.weather_7_ack = received
                    self.weather_7_ack_event.set()
                elif received[5] == KEY_CURRENT_WEATHER:
                    self.current_weather_ack = received
                    self.current_weather_ack_event.set()
            return

        self._consume_protocol_frame(received)

    def _consume_protocol_frame(self, chunk: bytes) -> None:
        if not chunk:
            return
        if not self._frame_buffer:
            if chunk[0] != HEADER or len(chunk) < 3:
                return
            self._expected_frame_length = int.from_bytes(chunk[1:3], "big") + 4
        self._frame_buffer.extend(chunk)

        if self._expected_frame_length is None or len(self._frame_buffer) < self._expected_frame_length:
            return
        if len(self._frame_buffer) != self._expected_frame_length:
            logger.error("Resposta enquadrada com tamanho inesperado: %s", format_hex(self._frame_buffer))
            self._frame_buffer.clear()
            self._expected_frame_length = None
            return

        frame = bytes(self._frame_buffer)
        self._frame_buffer.clear()
        self._expected_frame_length = None
        try:
            payload = validate_frame(frame, COMMAND_GET_FEATURES, KEY_GET_FEATURES)
        except ValueError as error:
            logger.error("Resposta de capacidades inválida: %s", error)
            return
        self.feature_bytes = payload
        self.feature_event.set()


async def write_once(client, packet: bytes, label: str) -> None:
    log_and_print(f"TX {label}: {format_hex(packet)}")
    for offset in range(0, len(packet), BLE_CHUNK_SIZE):
        chunk = packet[offset : offset + BLE_CHUNK_SIZE]
        await client.write_gatt_char(WRITE_UUID, chunk, response=False)
        if offset + BLE_CHUNK_SIZE < len(packet):
            await asyncio.sleep(0.02)


def validate_feature_bytes(feature_bytes: bytes) -> bool:
    if len(feature_bytes) <= FEATURE_WEATHER_7_OFFSET:
        raise RuntimeError(
            f"Resposta de capacidades curta: {len(feature_bytes)} bytes; necessários pelo menos 19."
        )
    if feature_bytes[0] != 0xAA:
        raise RuntimeError("Resposta de capacidades não começa com 0xAA.")
    return bool(feature_bytes[FEATURE_WEATHER_7_OFFSET] & FEATURE_WEATHER_7_MASK)


async def query_capabilities() -> tuple[bytes, bytes, bool]:
    from bleak import BleakClient

    collector = NotificationCollector()
    query = build_feature_query()

    async with BleakClient(ADDRESS) as client:
        if not client.is_connected:
            raise RuntimeError("Não foi possível conectar ao PRO 10.")
        await client.start_notify(NOTIFY_UUID, collector.callback)
        log_and_print("NOTIFY ATIVO: " + NOTIFY_UUID)
        try:
            await write_once(client, query, "CAPABILITIES 0x19/0x00")
            await asyncio.wait_for(collector.feature_ack_event.wait(), ACK_TIMEOUT_SECONDS)
            if collector.feature_ack is None or not ack_is_valid(
                collector.feature_ack, COMMAND_GET_FEATURES, KEY_GET_FEATURES
            ):
                ack = "ausente" if collector.feature_ack is None else format_hex(collector.feature_ack)
                raise RuntimeError("ACK da consulta de capacidades inválido: " + ack)
            log_and_print("ACK CAPABILITIES VÁLIDO: " + format_hex(collector.feature_ack))

            try:
                await asyncio.wait_for(collector.feature_event.wait(), FEATURE_TIMEOUT_SECONDS)
            except TimeoutError:
                feature_bytes = bytes(
                    await client.read_gatt_char("00002a28-0000-1000-8000-00805f9b34fb")
                )
                log_and_print("RX READ 2A28: " + format_hex(feature_bytes))
            else:
                if collector.feature_bytes is None:
                    raise RuntimeError("Evento de capacidades sem payload.")
                feature_bytes = collector.feature_bytes
                log_and_print("FEATURE PAYLOAD 0x19: " + format_hex(feature_bytes))
                await write_once(
                    client,
                    build_ack_to_device(COMMAND_GET_FEATURES, KEY_GET_FEATURES),
                    "ACK DA RESPOSTA DE CAPACIDADES",
                )
        finally:
            await client.stop_notify(NOTIFY_UUID)

    supported = validate_feature_bytes(feature_bytes)
    return query, feature_bytes, supported


def save_plan(feature_bytes: bytes, forecast_packet: bytes, current_packet: bytes, days: list[ForecastDay]) -> None:
    plan = {
        "city": "São Paulo",
        "supports_7_day": True,
        "feature_bytes_hex": format_hex(feature_bytes),
        "feature_offset_18_hex": f"{feature_bytes[18]:02X}",
        "feature_mask_hex": "08",
        "forecast_packet_hex": format_hex(forecast_packet),
        "current_packet_hex": format_hex(current_packet),
        "forecast": [
            {
                "date": item.value_date.isoformat(),
                "condition_code": item.condition_code,
                "minimum": item.minimum,
                "maximum": item.maximum,
            }
            for item in days
        ],
    }
    PLAN_FILE.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Plano salvo em %s", PLAN_FILE)


def load_plan() -> tuple[bytes, bytes]:
    if not PLAN_FILE.exists():
        raise RuntimeError("Plano confirmado inexistente. Execute primeiro --query-capabilities.")
    plan = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
    if plan.get("supports_7_day") is not True:
        raise RuntimeError("O plano não confirma suporte à previsão de sete dias.")
    feature_bytes = bytes.fromhex(plan["feature_bytes_hex"])
    if not validate_feature_bytes(feature_bytes):
        raise RuntimeError("O bit de previsão não está ativo no resultado salvo.")

    forecast_packet = bytes.fromhex(plan["forecast_packet_hex"])
    current_packet = bytes.fromhex(plan["current_packet_hex"])
    validate_frame(forecast_packet, COMMAND_SETTING, KEY_WEATHER_7)
    validate_frame(current_packet, COMMAND_SETTING, KEY_CURRENT_WEATHER)
    return forecast_packet, current_packet


async def send_confirmed_weather() -> None:
    from bleak import BleakClient

    forecast_packet, current_packet = load_plan()
    collector = NotificationCollector()

    async with BleakClient(ADDRESS) as client:
        if not client.is_connected:
            raise RuntimeError("Não foi possível conectar ao PRO 10.")
        await client.start_notify(NOTIFY_UUID, collector.callback)
        log_and_print("NOTIFY ATIVO: " + NOTIFY_UUID)
        try:
            await write_once(client, forecast_packet, "WEATHER 0x23")
            await asyncio.wait_for(collector.weather_7_ack_event.wait(), ACK_TIMEOUT_SECONDS)
            if collector.weather_7_ack is None or not ack_is_valid(
                collector.weather_7_ack, COMMAND_SETTING, KEY_WEATHER_7
            ):
                ack = (
                    "ausente"
                    if collector.weather_7_ack is None
                    else format_hex(collector.weather_7_ack)
                )
                raise RuntimeError("ACK 0x23 inválido: " + ack)
            log_and_print("ACK 0x23 VÁLIDO: " + format_hex(collector.weather_7_ack))

            await write_once(client, current_packet, "WEATHER 0x1D")
            await asyncio.wait_for(collector.current_weather_ack_event.wait(), ACK_TIMEOUT_SECONDS)
            if collector.current_weather_ack is None or not ack_is_valid(
                collector.current_weather_ack, COMMAND_SETTING, KEY_CURRENT_WEATHER
            ):
                ack = (
                    "ausente"
                    if collector.current_weather_ack is None
                    else format_hex(collector.current_weather_ack)
                )
                raise RuntimeError("ACK 0x1D inválido: " + ack)
            log_and_print("ACK 0x1D VÁLIDO: " + format_hex(collector.current_weather_ack))
        finally:
            await client.stop_notify(NOTIFY_UUID)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Teste controlado do fluxo completo de clima.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--query-capabilities",
        action="store_true",
        help="consulta capacidades e prepara os pacotes sem enviar clima",
    )
    action.add_argument(
        "--send-weather",
        action="store_true",
        help="envia uma vez o plano previamente confirmado: 0x23 e depois 0x1D",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logger.info("=== INÍCIO DO TESTE CONTROLADO ===")
    try:
        if args.query_capabilities:
            query, feature_bytes, supported = asyncio.run(query_capabilities())
            log_and_print("CONSULTA ENVIADA: " + format_hex(query))
            log_and_print("FEATURE BYTES: " + format_hex(feature_bytes))
            log_and_print(
                f"featureBytes[18] = 0x{feature_bytes[18]:02X}; "
                f"featureBytes[18] & 0x08 = 0x{feature_bytes[18] & 0x08:02X}"
            )
            log_and_print("SUPORTE A 7 DIAS: " + ("SIM" if supported else "NÃO"))
            if not supported:
                log_and_print("Nenhum pacote de clima foi montado ou enviado.")
                return

            base_date = date.today()
            days = build_test_forecast(base_date)
            forecast_packet = build_weather_7_packet(days)
            current_packet = build_weather_packet(
                "São Paulo", Decimal("24.6"), 5, weather_date=base_date
            )
            save_plan(feature_bytes, forecast_packet, current_packet, days)
            log_and_print("PACOTE 0x23 PREPARADO, NÃO ENVIADO: " + format_hex(forecast_packet))
            log_and_print("TAMANHO 0x23: " + str(len(forecast_packet)) + " bytes")
            log_and_print("Clima não enviado. Aguarde aprovação explícita para --send-weather.")
        else:
            asyncio.run(send_confirmed_weather())
            log_and_print("FLUXO COMPLETO CONCLUÍDO: 0x23 -> ACK -> 0x1D -> ACK")
    except Exception as error:
        logger.exception("TESTE INTERROMPIDO SEM REPETIÇÃO: %s", error)
        raise SystemExit(f"Erro: {error}") from error
    finally:
        logger.info("=== FIM DO TESTE CONTROLADO ===")


if __name__ == "__main__":
    main()
