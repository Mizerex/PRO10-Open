# PRO10 Open

Projeto experimental e open source de engenharia reversa do smartwatch **PRO 10**. O objetivo é permitir comunicação direta entre Windows e relógio por Bluetooth Low Energy (BLE), sem depender do aplicativo oficial.

> Este projeto não é afiliado ao fabricante do relógio. Use por sua conta e risco. Os scripts de escrita podem alterar dados exibidos no dispositivo; leia o código e mantenha backups antes de testar.

## Estado atual

- Modelo exibido: `PRO 10`
- Versão exibida: `V1.2`
- Transporte: Bluetooth Low Energy
- Interface de dados identificada: Nordic UART Service
- Notify: `6E400003-B5A3-F393-E0A9-E50E24DCCA9F`
- Write: `6E400002-B5A3-F393-E0A9-E50E24DCCA9F`
- Sincronização de hora funcional nos testes
- Envio de clima atual funcional nos testes
- Previsão de 7 dias funcional nos testes
- Leitura de bateria disponível
- Leitura de informações de firmware e hardware disponível
- Reconexão automática em desenvolvimento
- Keep-alive em desenvolvimento

Os testes também procuram entender como o relógio controla ícones e funções como WhatsApp, Facebook, X, saúde e esportes.

## Estrutura

```text
PRO10-Open/
├── README.md
├── requirements.txt
├── .gitignore
├── LICENSE
├── src/
│   ├── bridge/       # ponte Windows principal
│   ├── bluetooth/    # scan e diagnóstico BLE
│   ├── weather/      # clima atual e previsão
│   ├── time_sync/    # construção e envio do pacote de hora
│   └── explorer/     # protótipo somente leitura do File Explorer
├── tools/            # ferramentas de análise de APK
├── docs/             # protocolo, auditorias e notas históricas
├── logs/             # gerado localmente e ignorado pelo Git
└── experiments/      # versões históricas para pesquisa
```

## Instalação

Requisitos: Windows 10/11, Python 3.10 ou superior e Bluetooth habilitado.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Os scripts que se conectam diretamente exigem o endereço BLE do seu próprio relógio. Use o scanner, informe o endereço pela opção indicada pelo script ou substitua o valor de exemplo `AA:BB:CC:DD:EE:FF`. Nunca publique o endereço real do dispositivo.

Exemplo de diagnóstico somente leitura:

```powershell
python src/explorer/pro10_file_explorer.py --name "PRO 10" --output pro10_gatt_inventory.json
```

## Documentação técnica

- [Protocolo de clima](docs/WEATHER_PROTOCOL.md)
- [Auditoria dos bytes de clima](docs/WEATHER_BYTE_AUDIT.md)
- [Plano de testes de clima](docs/weather_test_plan.json)
- [Histórico das versões da ponte](experiments/bridge_history/)

Capturas HCI, logs brutos, endereços Bluetooth, coordenadas pessoais, caches e arquivos de sessão não são publicados. As conclusões técnicas relevantes foram preservadas na documentação.

## PRO10 File Explorer

O **PRO10 File Explorer** será uma ferramenta de exploração segura, inicialmente somente leitura, para tentar identificar:

- sistema de arquivos e memória interna;
- arquivos transferidos via Bluetooth;
- watchfaces, imagens, ícones e recursos gráficos;
- bancos de dados e configurações;
- possíveis comandos de leitura e escrita.

O protótipo atual inventaria serviços, características, propriedades e descritores GATT, além de tentar ler apenas características BLE conhecidas como seguras. A etapa seguinte é correlacionar esse inventário com capturas HCI e com o comportamento do aplicativo oficial, sem enviar comandos desconhecidos ao relógio.

## Contribuições

Relatos reproduzíveis, documentação de UUIDs e análises de pacotes são bem-vindos. Remova endereços BLE, dados pessoais, tokens e outros segredos antes de abrir uma issue ou pull request.

## Licença

Distribuído sob a licença MIT. Consulte [LICENSE](LICENSE).
