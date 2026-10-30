# EdgeHop

Usá el mouse y el teclado de tu PC con Windows en tu iPhone, como si el celular fuera un segundo monitor extendido.

Llevás el cursor al borde de la pantalla donde tenés el celular y el control pasa al iPhone: el mouse mueve el puntero del celu y el teclado escribe ahí. Volvés al borde y todo vuelve a la PC.

> 🚧 En desarrollo.

## Cómo funciona

```
[PC Windows] --USB--> [ESP32] --Bluetooth--> [iPhone]
```

- **Programa en Windows**: detecta cuando el cursor cruza el borde, captura mouse y teclado y los envía por USB.
- **ESP32**: se presenta ante el iPhone como un mouse y teclado Bluetooth y reenvía la entrada.
- **iPhone**: usa el soporte nativo de mouse (AssistiveTouch) y teclado Bluetooth de iOS. No requiere instalar ninguna app.

## Hardware

- Placa ESP32 DevKit (WROOM-32). Las ESP32-S2 no sirven porque no tienen Bluetooth.
- Cable USB que transmita datos.

## Estructura

- `firmware/`: código del ESP32
- `desktop/`: programa de Windows

## Licencia

MIT

## Uso

En Windows 10 con Python 3.13, instalá `pyserial` (`python -m pip install pyserial`) y ejecutá `python desktop/edgehop.py` con el ESP32 conectado por USB. El programa busca automáticamente los adaptadores CP210x y CH340. Para elegir un puerto o cambiar el borde y el tamaño estimado del iPhone, copiá `desktop/config.example.toml` como `desktop/config.local.toml` y editá sus valores. Con el control en el iPhone, usá `Ctrl+Alt+Space` para volver a la PC; el mismo atajo alterna al iPhone cuando está conectado.
