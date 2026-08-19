# PRO10 Windows Bridge v0.1
# Windows <-> PRO 10 via BLE, sem depender do Olywear durante o uso normal.
#
# Requisitos:
#   python -m pip install bleak
#
# O que esta versao ja faz:
# - encontra o PRO 10 pelo nome (nao depende de MAC fixo)
# - conecta via BLE
# - ativa notificacoes do canal UART
# - le bateria, serial, hardware e firmware
# - reconecta automaticamente se o relogio derrubar a sessao
# - consulta clima pela internet (Open-Meteo, sem chave)
# - deixa pronto o canal de escrita do clima
#
# O que falta encaixar:
# - o frame binario exato dos comandos 0x23 (7 dias) e 0x1D (clima atual)
#   ja identificados no trabalho anterior. Nao enviamos bytes inventados.

import asyncio
import json
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

# Padrao inicial: Sao Paulo/SP. Pode mudar depois.
LATITUDE = -23.5505
LONGITUDE = -46.6333
CITY_NAME = "Sao Paulo"

RECONNECT_DELAY = 3
SESSION_PING_SECONDS = 8


def text(data: bytes) -> str:
    return bytes(data).decode("utf-8", errors="replace").strip("\x00")


def weather_code_to_text(code: int) -> str:
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
        61: "Chuva fraca",
        63: "Chuva",
        65: "Chuva forte",
        71: "Neve fraca",
        73: "Neve",
        75: "Neve forte",
        80: "Pancadas fracas",
        81: "Pancadas",
        82: "Pancadas fortes",
        95: "Trovoada",
        96: "Trovoada com granizo",
        99: "Trovoada forte com granizo",
    }
    return mapping.get(code, f"Codigo {code}")


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
    req = urllib.request.Request(url, headers={"User-Agent": "PRO10-Windows-Bridge/0.1"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def checksum_sum_mod_256(data: bytes) -> int:
    # Formula que ja foi identificada no projeto PRO 10.
    return sum(data) & 0xFF


class Pro10Bridge:
    def __init__(self):
        self.client = None
        self.device = None
        self.disconnected = asyncio.Event()
        self.last_rx = None

    def on_disconnect(self, client):
        print("\n[BLE] PRO 10 desconectou.")
        self.disconnected.set()

    def on_notify(self, sender, data):
        self.last_rx = bytes(data)
        print("[RX]", bytes(data).hex(" "))

    async def find_watch(self):
        print("[BLE] Procurando PRO 10...")
        devices = await BleakScanner.discover(timeout=8)
        for d in devices:
            if DEVICE_NAME.lower() in (d.name or "").lower():
                print("[BLE] Encontrado:", d)
                return d
        return None

    async def read_info(self):
        try:
            battery = await self.client.read_gatt_char(BATTERY_UUID)
            print(f"[INFO] Bateria: {int(battery[0])}%")
        except Exception as e:
            print("[INFO] Bateria indisponivel:", e)

        for label, uuid in [
            ("Serial", SERIAL_UUID),
            ("Hardware", HARDWARE_UUID),
            ("Firmware", FIRMWARE_UUID),
        ]:
            try:
                value = await self.client.read_gatt_char(uuid)
                print(f"[INFO] {label}: {text(value)}")
            except Exception as e:
                print(f"[INFO] {label} indisponivel:", e)

    async def connect(self):
        self.disconnected.clear()
        self.device = await self.find_watch()
        if not self.device:
            raise RuntimeError("PRO 10 nao encontrado")

        self.client = BleakClient(
            self.device,
            disconnected_callback=self.on_disconnect,
        )

        print("[BLE] Conectando...")
        await self.client.connect()
        print("[BLE] Conectado:", self.client.is_connected)

        await asyncio.sleep(1)

        # Canal que o Olywear tambem habilita.
        await self.client.start_notify(UART_NOTIFY, self.on_notify)
        print("[BLE] Notify ativo:", UART_NOTIFY)

        await self.read_info()

    async def write_raw(self, packet: bytes, response=False):
        if not self.client or not self.client.is_connected:
            raise RuntimeError("Relogio nao conectado")
        print("[TX]", packet.hex(" "))
        await self.client.write_gatt_char(UART_WRITE, packet, response=response)

    async def show_weather_from_windows(self):
        try:
            data = await asyncio.to_thread(get_weather)
            cur = data["current"]
            daily = data["daily"]

            print("\n[CLIMA WINDOWS]")
            print("Cidade:", CITY_NAME)
            print("Temperatura:", round(cur["temperature_2m"]), "C")
            print("Sensacao:", round(cur["apparent_temperature"]), "C")
            print("Condicao:", weather_code_to_text(int(cur["weather_code"])))
            print("Previsao 7 dias:")

            for i, day in enumerate(daily["time"]):
                print(
                    " ",
                    day,
                    weather_code_to_text(int(daily["weather_code"][i])),
                    f"{round(daily['temperature_2m_min'][i])}..{round(daily['temperature_2m_max'][i])} C"
                )

            return data
        except Exception as e:
            print("[CLIMA] Nao foi possivel consultar agora:", e)
            return None

    async def protocol_bootstrap(self):
        """
        Sequencia ja validada no projeto:
          conectar
          -> descobrir servicos
          -> MTU negociado pelo Windows/relógio
          -> ~1 s
          -> notify 6E400003
          -> ler 2A27/2A26
          -> consulta de capacidade 0x19/0x00 + ACK
          -> ler 2A28
          -> sincronizar hora
          -> weather 7 dias 0x23 + ACK
          -> clima atual 0x1D + ACK

        Os IDs e a ordem estao preservados aqui.
        O frame binario completo sera encaixado sem repetir a descoberta BLE.
        """
        await self.show_weather_from_windows()

    async def hold_session(self):
        # O relógio mostrou tendencia a encerrar sessoes BLE ociosas.
        # Enquanto ainda nao inserimos o keep-alive proprietario exato,
        # fazemos leitura de bateria em intervalo curto para manter trafego GATT.
        while self.client and self.client.is_connected:
            try:
                battery = await self.client.read_gatt_char(BATTERY_UUID)
                print(
                    f"[KEEP] {datetime.now().strftime('%H:%M:%S')} "
                    f"BLE ativo | bateria {int(battery[0])}%"
                )
            except Exception as e:
                print("[KEEP] falhou:", e)
                break

            try:
                await asyncio.wait_for(
                    self.disconnected.wait(),
                    timeout=SESSION_PING_SECONDS
                )
                break
            except asyncio.TimeoutError:
                pass

    async def run_once(self):
        await self.connect()
        await self.protocol_bootstrap()
        await self.hold_session()

    async def run_forever(self):
        print("=" * 56)
        print(" PRO10 WINDOWS BRIDGE v0.1")
        print(" PC <-> BLE <-> PRO 10")
        print("=" * 56)

        while True:
            try:
                await self.run_once()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print("[ERRO]", e)
            finally:
                try:
                    if self.client and self.client.is_connected:
                        await self.client.disconnect()
                except Exception:
                    pass

            print(f"[BLE] Nova tentativa em {RECONNECT_DELAY}s...")
            await asyncio.sleep(RECONNECT_DELAY)


async def main():
    bridge = Pro10Bridge()
    await bridge.run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nPRO10 Bridge encerrado.")
