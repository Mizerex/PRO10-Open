import asyncio
from bleak import BleakClient

ADDRESS = "AA:BB:CC:DD:EE:FF"

NOTIFY_UUIDS = [
    "0000ff14-0000-1000-8000-00805f9b34fb",
    "6e400003-b5a3-f393-e0a9-e50e24dcca9f",
    "0000ff01-0000-1000-8000-00805f9b34fb",
]

def callback(sender, data):
    print(f"RECEBIDO de {sender.uuid}: {bytes(data).hex(' ')}")

async def main():
    async with BleakClient(ADDRESS) as client:
        print("Conectado:", client.is_connected)

        for uuid in NOTIFY_UUIDS:
            try:
                await client.start_notify(uuid, callback)
                print("Escutando:", uuid)
            except Exception as e:
                print("Erro:", uuid, e)

        print("\nEscutando o PRO 10 por 20 segundos...")
        await asyncio.sleep(20)

asyncio.run(main())