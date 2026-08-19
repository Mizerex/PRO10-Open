# Auditoria byte a byte dos pacotes meteorológicos

Fonte: análise estática de `olywear-base.apk`, com `SettingIssuedUtils` decompilada novamente nos modos normal e `simple` do JADX.

Nenhuma operação BLE foi realizada durante esta auditoria.

## Resultado principal

Os payloads meteorológicos que montamos correspondem byte por byte aos algoritmos Java `sendWeather7()` e `setWeather()`. A divergência encontrada está no enquadramento comum:

```text
IssuedUtil.PROTOCOL_VERSION
```

O Olywear começa com `0x01`, mas, antes de enviar configurações, lê `00002A27` e executa:

```java
IssuedUtil.PROTOCOL_VERSION = (byte) Integer.valueOf(valorLido).intValue();
```

O valor já observado no PRO 10 foi `10000`. Se esses forem exatamente os cinco caracteres ASCII lidos pelo aplicativo:

```text
10000 decimal = 0x2710
cast para byte = 0x10
```

Nossos pacotes usaram `0x01`. Com a versão dinâmica `0x10`, também muda o checksum:

```text
0x23: versão 01/checksum 1F -> versão 10/checksum 2E
0x1D: versão 01/checksum 56 -> versão 10/checksum 65
```

O ACK genérico confirma enquadramento/recepção, comando e chave, mas não contém a versão do protocolo. Portanto, ACK `0x01` não prova que o firmware aceitou semanticamente o conteúdo meteorológico.

## Objeto que alimenta `sendWeather7()`

O endpoint diário retorna `NetWeatherBean`:

```text
GET https://repellent.maswear.net/api/weather/get_daily_weather
```

Estrutura relevante:

```text
NetWeatherBean
├── city_name
├── country
├── id
└── list: List<DayWeatherBean>
    ├── data_time
    ├── icon
    ├── temp
    ├── temp_min
    ├── temp_max
    ├── humidity
    ├── weather
    ├── description
    ├── wind_speed / wind_deg
    ├── rain_pop
    ├── visibility
    └── air_pollution
```

Somente `data_time`, `icon`, `temp_min` e `temp_max` alimentam `sendWeather7()`.

O aplicativo salva o objeto inteiro em `SHAREDPREFERENCES_KEY_7_DAYS_WEATHER` e passa diretamente `netWeatherBean.getList()` para `sendWeather7()` sem reordenar, filtrar ou remover o primeiro item.

## Primeiro registro: hoje ou amanhã

O APK trata `list.get(0)` como clima atual:

```java
new WeatherBean(..., netWeatherBean.getList().get(0))
showWeatherInfo(netWeatherBean.getList().get(0))
```

Depois transmite a mesma lista inteira em `0x23`. Logo, a suposição operacional do Olywear é:

```text
registro 0 = hoje / condição atual do dia
registros seguintes = datas posteriores
```

Não há `subList(1, ...)`, incremento de data nem lógica que comece amanhã. Nossos registros de 13 a 19/08/2026 seguem essa expectativa.

## Ordem exata de cada registro `0x23`

Cada registro tem sete bytes:

```text
[0] (byte) (0xFF00 & (ano - 2000))
[1] (byte) ((ano - 2000) & 0xFF)
[2] mês
[3] dia
[4] código meteorológico
[5] floor(temperatura mínima)
[6] ceil(temperatura máxima)
```

A ausência de `>> 8` no primeiro byte do ano foi confirmada em duas decompilações independentes. Para `2026`, `ano - 2000 = 26 = 0x001A`, portanto os bytes exatos são `00 1A`.

Os bytes `00` no início dos registros não são separadores nem marcadores de ausência: são o primeiro byte do ano deslocado desde 2000, produzido pelo algoritmo do APK. Outros `00` nos offsets de condição significam código meteorológico zero, isto é, céu limpo.

## Códigos meteorológicos

`DayWeatherBean.getWeatherCode()` usa o campo `icon`, não `weather` ou `description`:

| Código | Ícones da API | Significado usado pelo Olywear |
|---:|---|---|
| 0 | `01d`, `01n`, desconhecido | céu limpo/padrão |
| 1 | `02d`, `02n` | poucas nuvens |
| 2 | `03d`, `03n` | nuvens dispersas |
| 3 | `04d`, `04n` | nublado |
| 4 | `09d`, `09n` | pancadas de chuva |
| 5 | `10d`, `10n` | chuva |
| 6 | `11d`, `11n` | tempestade |
| 7 | `13d`, `13n` | neve |
| 8 | `50d`, `50n` | névoa |

Assim, nosso código `5` corresponde exatamente a `10d/10n`, classificado pelo aplicativo como chuva. O APK não contém outra tradução entre esse código e o firmware.

## Temperaturas

Nos dois comandos, a ordem confirmada é:

```text
temperatura mínima primeiro: floor(temp_min)
temperatura máxima depois:   ceil(temp_max)
```

No `0x1D`, a temperatura atual aparece no final do payload e é truncada em direção a zero a partir do texto `temp`.

Não existe offset `+128`, escala decimal, unidade explícita ou marcador especial. O cast Java para `byte` produz um inteiro de 8 bits em complemento de dois.

## Comparação byte a byte — pacote `0x23`

Nosso pacote enviado:

```text
DF 00 37 1F 02 01 23 00 32 07
00 1A 08 0D 05 14 19
00 1A 08 0E 04 14 18
00 1A 08 0F 03 13 18
00 1A 08 10 01 13 19
00 1A 08 11 00 14 1A
00 1A 08 12 00 15 1B
00 1A 08 13 01 15 1B
```

Valor esperado abaixo considera o estado dinâmico provável `PROTOCOL_VERSION = 0x10` para o valor `2A27 = "10000"`.

| Offset | Nosso | Esperado | Significado | Resultado |
|---:|---:|---:|---|---|
| 0 | DF | DF | header | correto |
| 1 | 00 | 00 | comprimento externo alto | correto |
| 2 | 37 | 37 | comprimento externo = 55 | correto |
| 3 | 1F | 2E | checksum | **incorreto se a versão dinâmica é 10** |
| 4 | 02 | 02 | comando configurações | correto |
| 5 | 01 | 10 | versão dinâmica do protocolo | **provavelmente incorreto** |
| 6 | 23 | 23 | chave previsão | correto |
| 7 | 00 | 00 | payload alto | correto |
| 8 | 32 | 32 | payload = 50 bytes | correto |
| 9 | 07 | 07 | quantidade de registros | correto |
| 10 | 00 | 00 | registro 0: ano alto pelo algoritmo APK | correto |
| 11 | 1A | 1A | registro 0: 2026 - 2000 | correto |
| 12 | 08 | 08 | registro 0: mês 8 | correto |
| 13 | 0D | 0D | registro 0: dia 13, hoje | correto |
| 14 | 05 | 05 | registro 0: chuva `10d/10n` | correto |
| 15 | 14 | 14 | registro 0: mínima 20 °C | correto |
| 16 | 19 | 19 | registro 0: máxima 25 °C | correto |
| 17 | 00 | 00 | registro 1: ano alto | correto |
| 18 | 1A | 1A | registro 1: ano 2026 | correto |
| 19 | 08 | 08 | registro 1: mês 8 | correto |
| 20 | 0E | 0E | registro 1: dia 14 | correto |
| 21 | 04 | 04 | registro 1: pancadas de chuva | correto |
| 22 | 14 | 14 | registro 1: mínima 20 °C | correto |
| 23 | 18 | 18 | registro 1: máxima 24 °C | correto |
| 24 | 00 | 00 | registro 2: ano alto | correto |
| 25 | 1A | 1A | registro 2: ano 2026 | correto |
| 26 | 08 | 08 | registro 2: mês 8 | correto |
| 27 | 0F | 0F | registro 2: dia 15 | correto |
| 28 | 03 | 03 | registro 2: nublado | correto |
| 29 | 13 | 13 | registro 2: mínima 19 °C | correto |
| 30 | 18 | 18 | registro 2: máxima 24 °C | correto |
| 31 | 00 | 00 | registro 3: ano alto | correto |
| 32 | 1A | 1A | registro 3: ano 2026 | correto |
| 33 | 08 | 08 | registro 3: mês 8 | correto |
| 34 | 10 | 10 | registro 3: dia 16 | correto |
| 35 | 01 | 01 | registro 3: poucas nuvens | correto |
| 36 | 13 | 13 | registro 3: mínima 19 °C | correto |
| 37 | 19 | 19 | registro 3: máxima 25 °C | correto |
| 38 | 00 | 00 | registro 4: ano alto | correto |
| 39 | 1A | 1A | registro 4: ano 2026 | correto |
| 40 | 08 | 08 | registro 4: mês 8 | correto |
| 41 | 11 | 11 | registro 4: dia 17 | correto |
| 42 | 00 | 00 | registro 4: céu limpo | correto; não é ausência |
| 43 | 14 | 14 | registro 4: mínima 20 °C | correto |
| 44 | 1A | 1A | registro 4: máxima 26 °C | correto |
| 45 | 00 | 00 | registro 5: ano alto | correto |
| 46 | 1A | 1A | registro 5: ano 2026 | correto |
| 47 | 08 | 08 | registro 5: mês 8 | correto |
| 48 | 12 | 12 | registro 5: dia 18 | correto |
| 49 | 00 | 00 | registro 5: céu limpo | correto; não é ausência |
| 50 | 15 | 15 | registro 5: mínima 21 °C | correto |
| 51 | 1B | 1B | registro 5: máxima 27 °C | correto |
| 52 | 00 | 00 | registro 6: ano alto | correto |
| 53 | 1A | 1A | registro 6: ano 2026 | correto |
| 54 | 08 | 08 | registro 6: mês 8 | correto |
| 55 | 13 | 13 | registro 6: dia 19 | correto |
| 56 | 01 | 01 | registro 6: poucas nuvens | correto |
| 57 | 15 | 15 | registro 6: mínima 21 °C | correto |
| 58 | 1B | 1B | registro 6: máxima 27 °C | correto |

Pacote recalculado com versão `0x10`:

```text
DF 00 37 2E 02 10 23 00 32 07 00 1A 08 0D 05 14 19 00 1A 08 0E 04 14 18 00 1A 08 0F 03 13 18 00 1A 08 10 01 13 19 00 1A 08 11 00 14 1A 00 1A 08 12 00 15 1B 00 1A 08 13 01 15 1B
```

## Comparação byte a byte — pacote `0x1D`

| Offset | Nosso | Esperado | Significado | Resultado |
|---:|---:|---:|---|---|
| 0 | DF | DF | header | correto |
| 1 | 00 | 00 | comprimento externo alto | correto |
| 2 | 17 | 17 | comprimento externo = 23 | correto |
| 3 | 56 | 65 | checksum | **incorreto se a versão dinâmica é 10** |
| 4 | 02 | 02 | comando configurações | correto |
| 5 | 01 | 10 | versão dinâmica do protocolo | **provavelmente incorreto** |
| 6 | 1D | 1D | chave clima atual | correto |
| 7 | 00 | 00 | payload alto | correto |
| 8 | 12 | 12 | payload = 18 bytes | correto |
| 9 | 07 | 07 | ano absoluto alto | correto |
| 10 | EA | EA | ano absoluto baixo: 2026 | correto |
| 11 | 08 | 08 | mês 8 | correto |
| 12 | 0D | 0D | dia 13 | correto |
| 13 | 00 | 00 | reservado, constante no APK | correto; não é ausência |
| 14 | 05 | 05 | chuva `10d/10n` | correto |
| 15 | 18 | 18 | mínima 24 °C | correto |
| 16 | 19 | 19 | máxima 25 °C | correto |
| 17 | 08 | 08 | comprimento UTF-8 da cidade | correto |
| 18 | 47 | 47 | `G` | correto |
| 19 | 75 | 75 | `u` | correto |
| 20 | 61 | 61 | `a` | correto |
| 21 | 72 | 72 | `r` | correto |
| 22 | 75 | 75 | `u` | correto |
| 23 | 6A | 6A | `j` | correto |
| 24 | C3 | C3 | primeiro byte UTF-8 de `á` | correto |
| 25 | A1 | A1 | segundo byte UTF-8 de `á` | correto |
| 26 | 18 | 18 | temperatura atual 24 °C | correto |

Pacote recalculado com versão `0x10`:

```text
DF 00 17 65 02 10 1D 00 12 07 EA 08 0D 00 05 18 19 08 47 75 61 72 75 6A C3 A1 18
```

## Valores de dados inválidos ou ausentes

Não foi encontrado um sentinel meteorológico explícito como `FF`, `7F`, `80` ou uma flag de validade.

Comportamentos encontrados:

- quantidade `0` no início de `0x23` representa uma lista vazia e naturalmente não contém previsão;
- data com mês/dia `0` só aparece se uma posição do array permanecer zerada por falha de parsing; é uma data inválida, não um código documentado de ausência;
- ícone desconhecido vira código `0`, que também significa céu limpo; não é ausência;
- cidade `null` vira string vazia em `0x1D`; o método permite isso, mas não marca validade;
- o byte reservado de `0x1D` é sempre `00` e não indica ausência;
- temperaturas são bytes brutos com sinal, sem sentinel no APK.

## Diagnóstico mais provável

1. **Payloads meteorológicos:** correspondem ao Java do APK.
2. **Datas e ordem dos registros:** correspondem ao fluxo presumido pelo aplicativo.
3. **Código 5 e mín./máx.:** corretos.
4. **Zeros internos:** explicados pelo algoritmo; não são registros vazios.
5. **Diferença concreta encontrada:** versão do protocolo no offset 5 e, por consequência, checksum no offset 3.

Essa é atualmente a explicação mais forte para “ACK aceito, mas nenhum conteúdo”: nossos pacotes foram recebidos pelo transporte, porém não reproduziram o estado dinâmico de `IssuedUtil.PROTOCOL_VERSION` que o Olywear estabelece antes do envio.

Antes de qualquer novo teste, o valor bruto de `2A27` deve ser confirmado em leitura controlada. Se for exatamente `10000`, o próximo pacote candidato deve usar versão `0x10` e os checksums recalculados acima. Nenhum desses pacotes corrigidos foi transmitido nesta auditoria.
