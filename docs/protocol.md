# Protocolo serie PC → ESP32

USB serie a **921600 baudios**, 8N1. Todo es binario, little-endian.

## Trama

| Campo    | Tamaño | Descripción                                   |
|----------|--------|-----------------------------------------------|
| `SYNC`   | 1      | Siempre `0xA5`                                |
| `TYPE`   | 1      | Tipo de mensaje (ver abajo)                   |
| `LEN`    | 1      | Largo del payload en bytes (0–32)             |
| payload  | `LEN`  |                                               |
| `CHK`    | 1      | XOR de `TYPE`, `LEN` y todos los bytes del payload |

Si el checksum no coincide o `LEN` > 32, la trama se descarta y el receptor vuelve a buscar `SYNC`.

## Mensajes PC → ESP32

| TYPE | Nombre   | Payload | Efecto |
|------|----------|---------|--------|
| `0x01` | MOUSE  | `dx:int16, dy:int16, wheel:int8, buttons:uint8` (6 bytes) | Movimiento relativo. El ESP32 lo parte en reportes HID de ±127. `buttons`: bit0 izq, bit1 der, bit2 medio. |
| `0x02` | KEYS   | `modifiers:uint8, keys:uint8[6]` (7 bytes) | Estado completo del teclado (reporte HID boot). La PC mantiene el estado; el ESP32 lo reenvía tal cual. |
| `0x03` | CONSUMER | `usage:uint16` (2 bytes) | Tecla multimedia (volumen, play, etc.). `0` = soltar. |
| `0x04` | RELEASE_ALL | — | Suelta todas las teclas y botones. |
| `0x05` | PING   | — | El ESP32 responde con STATUS. |

## Mensajes ESP32 → PC

| TYPE | Nombre | Payload |
|------|--------|---------|
| `0x81` | STATUS | `connected:uint8` (1 = iPhone conectado por BLE) |

El ESP32 también envía STATUS cada vez que el iPhone se conecta o desconecta.
