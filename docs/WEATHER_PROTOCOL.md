# Protocolo BLE de clima do Olywear

Fonte analisada: `olywear-base.apk`, pacote Android `com.lianhezhuli.olywear`.

Esta documentação deriva exclusivamente da análise estática do APK. Nenhum pacote de clima foi enviado ao relógio.

## Classes e fluxo

- Montagem dos pacotes: `com.lianhezhuli.olywear.ble.SettingIssuedUtils`
  - `setWeather(NetWeatherBean.DayWeatherBean, String)` — clima atual e cidade
  - `sendWeather7(List<NetWeatherBean.DayWeatherBean>)` — previsão de vários dias
- Modelo de dados: `com.lianhezhuli.olywear.network.bean.NetWeatherBean.DayWeatherBean`
- Disparo: `com.lianhezhuli.olywear.function.home.fragment.HomeFragment.sendWeatherRun`
- Enquadramento: `com.lianhezhuli.olywear.ble.IssuedUtil.getSendByte(...)`
- Checksum, fila, fragmentação e ACK: `com.lianhezhuli.olywear.ble.utils.NotifyWriteUtils`
- Transporte: `com.lianhezhuli.olywear.ble.MBleManager`

Quando há suporte à previsão de sete dias, o aplicativo envia primeiro `sendWeather7(...)` e depois `setWeather(...)`. Caso contrário, envia apenas `setWeather(...)`.

## Transporte GATT

- Serviço: `6E400001-B5A3-F393-E0A9-E50E24DCCA9F`
- Escrita: `6E400002-B5A3-F393-E0A9-E50E24DCCA9F`
- Tipo de escrita: `write without response`
- Notificação/ACK: `6E400003-B5A3-F393-E0A9-E50E24DCCA9F`

Pacotes maiores que 20 bytes são divididos pelo aplicativo em blocos consecutivos de 20 bytes, com aproximadamente 20 ms entre os blocos. O relógio reconstitui o pacote usando o comprimento informado no cabeçalho.

## Enquadramento comum

Depois da inserção do checksum, todo comando tem o formato:

```text
Offset  Tamanho  Campo
0       1        Header = DF
1       2        Comprimento externo, big-endian = tamanho_payload + 5
3       1        Checksum
4       1        Command ID = 02 (configurações)
5       1        Versão do protocolo = 01
6       1        Command key
7       2        Comprimento do payload, big-endian
9       P        Payload
```

O comprimento final transmitido é `P + 9` bytes.

### Checksum

O checksum é a soma módulo 256 de todos os bytes do pacote antes da inserção do checksum:

```text
CS = sum(DF, LEN_H, LEN_L, 02, 01, KEY, P_H, P_L, PAYLOAD...) & FF
```

Ele é inserido no offset 3. Não há footer nem CRC adicional.

## Clima atual e cidade — `setWeather(...)`

Chave realmente usada: `0x1D` (29 decimal).

Existe a constante antiga `KEY_SETTING_SET_WEATHER = 0x13`, mas ela não é usada por esse método. O byte efetivamente montado pelo APK é `0x1D`.

Formato do pacote:

```text
DF LEN_H LEN_L CS 02 01 1D P_H P_L PAYLOAD
```

O payload tem `10 + C` bytes, onde `C` é o número de bytes UTF-8 da cidade:

```text
Payload  Tamanho  Campo
0        2        Ano absoluto, big-endian (ex.: 2026 = 07 EA)
2        1        Mês (1–12)
3        1        Dia (1–31)
4        1        Reservado, sempre 00 no APK
5        1        Código meteorológico (0–8)
6        1        Temperatura mínima: floor(temp_min), int8 com sinal
7        1        Temperatura máxima: ceil(temp_max), int8 com sinal
8        1        Comprimento C da cidade em bytes UTF-8
9        C        Cidade em UTF-8
9+C      1        Temperatura atual, int8 com sinal
```

A temperatura atual vem do campo textual `temp`. Se houver ponto decimal, o APK conserva somente a parte anterior ao ponto. Isso trunca em direção a zero (`25.9 -> 25`, `-3.5 -> -3`).

Temperaturas negativas são transmitidas como complemento de dois de 8 bits. Exemplo: `-3 = FD`.

### Cidade e limite observado

O aplicativo calcula `C` usando o comprimento UTF-8 completo. Na cópia, porém, usa `min(C, 50)`. Para cidades de até 50 bytes, o pacote é consistente. Para nomes acima de 50 bytes, o APK mantém `C` e o tamanho total originais, copia apenas 50 bytes, deixa bytes intermediários zerados e grava a temperatura atual no final. Isso aparenta ser um defeito do aplicativo. Uma futura implementação deve limitar a cidade a no máximo 50 bytes sem cortar uma sequência UTF-8 no meio.

## Previsão — `sendWeather7(...)`

Chave: `0x23` (35 decimal), constante `KEY_SETTING_NEW_WEATHER_7`.

Formato do pacote:

```text
DF LEN_H LEN_L CS 02 01 23 P_H P_L PAYLOAD
```

Payload:

```text
Payload  Tamanho  Campo
0        1        Quantidade N de registros
1        7*N      Registros meteorológicos
```

Cada registro ocupa sete bytes:

```text
Registro  Tamanho  Campo
0         1        Byte alto de (ano - 2000); o APK efetivamente produz 00
1         1        Byte baixo de (ano - 2000)
2         1        Mês
3         1        Dia
4         1        Código meteorológico (0–8)
5         1        Temperatura mínima: floor(temp_min), int8 com sinal
6         1        Temperatura máxima: ceil(temp_max), int8 com sinal
```

O código contém `(byte) (0xFF00 & (ano - 2000))` sem deslocamento à direita. Portanto, para anos normais, o primeiro byte é sempre `00`; o segundo contém o deslocamento desde 2000. Isso parece ser uma implementação imperfeita de um inteiro big-endian, mas é o comportamento exato do APK.

A previsão não inclui cidade, temperatura atual, umidade, descrição textual, vento, chuva, visibilidade ou qualidade do ar.

## Códigos meteorológicos

O código é derivado do campo `icon` da resposta meteorológica:

```text
Código  Ícones       Condição aproximada
0       01d, 01n     Céu limpo; também é o padrão para ícone desconhecido
1       02d, 02n     Poucas nuvens
2       03d, 03n     Nuvens dispersas
3       04d, 04n     Nublado
4       09d, 09n     Pancadas de chuva
5       10d, 10n     Chuva
6       11d, 11n     Tempestade
7       13d, 13n     Neve
8       50d, 50n     Névoa
```

Os campos textuais `weather` e `description` não são enviados ao relógio.

## Umidade

`DayWeatherBean` possui o campo `humidity` e métodos `getHumidity()`/`setHumidity()`, mas não há uso de `getHumidity()` nas rotinas BLE do aplicativo. A umidade não faz parte dos pacotes `0x1D` ou `0x23`.

## ACK

O ACK chega pela característica de notificação `6E400003...` e tem nove bytes:

```text
Offset  Campo
0       FD
1       00
2       05
3       Checksum do ACK
4       Command ID = 02
5       Command key: 1D para clima atual ou 23 para previsão
6       Comprimento/contador alto
7       Comprimento/contador baixo
8       Status: 01 = sucesso
```

Checksum do ACK:

```text
ACK_CS = sum(FD, 00, 05, 02, KEY, BYTE6, BYTE7, STATUS) & FF
```

O código Android decide o resultado usando os offsets 4, 5 e 8. Os offsets 6–7 não são validados pelo aplicativo. No ACK já observado para sincronização de hora, eles reproduziram o comprimento total do pacote transmitido (`00 0D` para 13 bytes); é provável que o relógio faça o mesmo para clima, mas isso ainda precisa ser confirmado por captura real antes de ser tratado como requisito.

O fluxo considera sucesso quando:

```text
ACK[0] == FD
ACK[4] == 02
ACK[5] == 1D ou 23
ACK[8] == 01
```

## Estado da investigação

- Estrutura estática dos dois comandos: confirmada no APK.
- UUIDs e tipo de escrita: confirmados no APK e iguais aos usados na sincronização de hora.
- Checksum: confirmado no código comum já validado pela sincronização de hora.
- ACK de clima atual `0x1D`: capturado e validado com status `0x01`.
- ACK da previsão `0x23`: formato previsto pelo manipulador comum, ainda não capturado do relógio.
- Nenhum dado meteorológico foi enviado durante esta análise.

## Sequência real do Olywear após a conexão

Esta seção registra o fluxo encontrado em `MainActivity`, `MBleManager`, `NotifyWriteUtils` e `HomeFragment`.

### 1. Preparação do canal BLE

1. O aplicativo descobre os serviços.
2. Para um dispositivo normal, ativa notificações em `6E400003`.
3. Quando a notificação fica ativa, lê a revisão de hardware em `00002A27`.
4. A revisão define `IssuedUtil.PROTOCOL_VERSION`.
5. Em seguida, lê a revisão de firmware em `00002A26`.

### 2. Consulta de capacidades

Depois de ler a revisão de firmware, o aplicativo envia:

```text
Command ID = 0x19 (25 decimal)
Command key = 0x00
Método = SettingIssuedUtils.getDeviceFeatures()
```

Esse é um comando geral de consulta das capacidades do relógio, não um comando para ativar o clima.

As capacidades podem chegar como resposta do protocolo `0x19`. Como alternativa/fallback, após o ACK dessa consulta o aplicativo lê `00002A28` no Device Information Service. Nos dois caminhos, os bytes são processados por `NotifyWriteUtils.readFeatures(...)`.

O suporte à previsão é somente um bit de capacidade:

```text
isSupport7Weather = (featureBytes[18] & 0x08) != 0
```

Não foi encontrado nenhum `weatherEnable`, `weatherSwitch`, comando liga/desliga ou tela de configuração que habilite o clima. A constante antiga `KEY_SETTING_SET_WEATHER = 0x13` não tem chamada; ela não participa do fluxo atual.

### 3. Aquisição e agendamento dos dados

O `HomeFragment` obtém localização e cidade, consulta a API e guarda:

- clima atual em `KEY_LAST_WEATHER`, dentro de um `WeatherBean` com `timeMillis`;
- previsão em `SHAREDPREFERENCES_KEY_7_DAYS_WEATHER`.

Os disparos encontrados são:

- após conexão normal: agenda `sendWeatherRun` para 15 segundos depois;
- após clima ser exibido/carregado: cancela o agendamento anterior e agenda `sendWeatherRun` para 2 segundos depois;
- se a fila BLE não estiver no estado `GENERAL`: adia o envio por mais 15 segundos;
- após atualização bem-sucedida do clima: agenda nova consulta para aproximadamente 1 hora depois;
- em falha de API: tenta novamente após 1 ou 5 minutos, conforme o caminho.

Antes de montar clima, `sendWeatherRun` faz uma leitura de detecção do serviço DFU 5610. Se essa leitura indicar que o dispositivo está no modo/tipo DFU, o fluxo retorna e não envia clima. Essa leitura não é um comando meteorológico.

### 4. Ordem exata dos comandos meteorológicos

O código de `sendWeatherRun` executa nesta ordem:

```text
se isSupport7Weather == true e há previsão armazenada:
    enfileirar sendWeather7(...)  -> Command ID 0x02, key 0x23

sempre:
    enfileirar setWeather(...)    -> Command ID 0x02, key 0x1D
```

Portanto, o Olywear não chama `sendWeather7(...)` depois de `setWeather(...)`. Ele chama e enfileira `0x23` antes de `0x1D`.

`NotifyWriteUtils` mantém uma fila FIFO. Para pacotes iniciados por `DF`, ela:

1. transmite o primeiro comando;
2. aguarda o ACK correspondente;
3. somente após o ACK remove o comando e inicia o próximo.

Assim, em um relógio com suporte a sete dias, a sequência efetiva é:

```text
0x19/0x00  consultar capacidades (durante a inicialização)
ACK/resposta de capacidades
0x23       enviar previsão
ACK 0x23
0x1D       enviar clima atual e cidade
ACK 0x1D
```

Se o relógio não anuncia suporte a sete dias ou não existe previsão armazenada:

```text
0x1D       enviar clima atual e cidade
ACK 0x1D
```

Não há outro comando meteorológico imediatamente depois do ACK `0x1D`. O próximo envio ocorre por reconexão ou por uma futura atualização agendada do clima.

### 5. Validade e expiração

No aplicativo:

- `WeatherBean.timeMillis` é um timestamp local de cache;
- clima atual com mais de 1 hora pode provocar nova consulta;
- `getWeatherRun` é normalmente reagendado para 1 hora;
- existe ainda um controle local `limitTime` de 12 horas ligado ao uso/cache da API.

Esses tempos pertencem ao aplicativo e não são transmitidos ao relógio.

No protocolo:

- `0x1D` leva apenas ano, mês e dia; não leva hora, epoch, duração ou TTL;
- `0x23` leva uma data em cada registro;
- não há campo explícito de validade ou expiração em nenhum dos dois pacotes.

### 6. Interpretação do desaparecimento observado

O APK prova que, quando o relógio anuncia `isSupport7Weather`, o comportamento normal do Olywear é enviar o par completo `0x23` seguido de `0x1D`. Isso torna plausível que o firmware use a previsão para preencher ou manter a tela meteorológica completa.

Entretanto, a análise do APK não prova que `0x23` seja obrigatório para persistência. O desaparecimento após alguns instantes também pode ser uma decisão interna da interface/firmware do relógio. Não existe no código Android um comando de ativação nem um TTL curto que explique diretamente esse comportamento. A confirmação exige um teste controlado futuro com `0x23 -> ACK -> 0x1D`, sem outros comandos, ou análise do firmware.
