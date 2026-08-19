from __future__ import annotations

from datetime import datetime


HEADER = 0xDF
COMMAND_SETTING = 0x02
PROTOCOL_VERSION = 0x01
KEY_SYSTEM_TIME = 0x01
PAYLOAD_LENGTH = 0x04


def encode_datetime(value: datetime) -> bytes:
    """Compacta a data/hora no formato de quatro bytes usado pelo Olywear."""
    if not 2000 <= value.year <= 2063:
        raise ValueError("O protocolo aceita anos entre 2000 e 2063.")

    year = value.year - 2000
    month = value.month
    day = value.day
    hour = value.hour
    minute = value.minute
    second = value.second

    return bytes(
        (
            (year << 2) | (month >> 2),
            ((month & 0x03) << 6) | (day << 1) | (hour >> 4),
            ((hour & 0x0F) << 4) | (minute >> 2),
            ((minute & 0x03) << 6) | second,
        )
    )


def calculate_checksum(packet_without_checksum: bytes) -> int:
    """Reproduz a soma de 8 bits aplicada pelo Olywear."""
    return sum(packet_without_checksum) & 0xFF


def build_time_packet(value: datetime) -> bytes:
    payload = encode_datetime(value)
    packet_without_checksum = bytes(
        (
            HEADER,
            0x00,
            len(payload) + 5,
            COMMAND_SETTING,
            PROTOCOL_VERSION,
            KEY_SYSTEM_TIME,
            0x00,
            len(payload),
        )
    ) + payload

    checksum = calculate_checksum(packet_without_checksum)
    return packet_without_checksum[:3] + bytes((checksum,)) + packet_without_checksum[3:]


def decode_time_packet(packet: bytes) -> datetime:
    """Decodifica o pacote para permitir validação local antes de qualquer envio."""
    if len(packet) != 13:
        raise ValueError(f"Tamanho inválido: {len(packet)} bytes; esperado: 13.")
    if packet[0] != HEADER:
        raise ValueError("Header inválido.")
    if packet[4:9] != bytes(
        (COMMAND_SETTING, PROTOCOL_VERSION, KEY_SYSTEM_TIME, 0x00, PAYLOAD_LENGTH)
    ):
        raise ValueError("Cabeçalho do comando de data/hora inválido.")

    expected_checksum = calculate_checksum(packet[:3] + packet[4:])
    if packet[3] != expected_checksum:
        raise ValueError(
            f"Checksum inválido: {packet[3]:02X}; esperado: {expected_checksum:02X}."
        )

    d0, d1, d2, d3 = packet[9:13]
    year = 2000 + (d0 >> 2)
    month = ((d0 & 0x03) << 2) | (d1 >> 6)
    day = (d1 >> 1) & 0x1F
    hour = ((d1 & 0x01) << 4) | (d2 >> 4)
    minute = ((d2 & 0x0F) << 2) | (d3 >> 6)
    second = d3 & 0x3F
    return datetime(year, month, day, hour, minute, second)


def format_hex(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)


def main() -> None:
    current_time = datetime.now().replace(microsecond=0)
    packet = build_time_packet(current_time)
    decoded_time = decode_time_packet(packet)

    if decoded_time != current_time:
        raise RuntimeError("A validação local do pacote falhou.")

    print("Data/hora local usada para gerar o pacote:")
    print(current_time.strftime("%d/%m/%Y %H:%M:%S"))
    print()
    print("Pacote PRO 10 validado localmente:")
    print(format_hex(packet))
    print()
    print("Checksum:", f"{packet[3]:02X}")
    print("Decodificação:", decoded_time.strftime("%d/%m/%Y %H:%M:%S"))
    print("Modo seguro: nenhum dado foi enviado por Bluetooth.")


if __name__ == "__main__":
    main()
