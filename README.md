# Interfaz Windows-iPhone

Usá el mouse y el teclado de tu PC con Windows en tu iPhone, como si el celular fuera un monitor extendido, y compartí el portapapeles entre los dos.

- **Mouse y teclado:** llevás el cursor al borde de la pantalla donde está el celular y el control pasa al iPhone. El teclado escribe en el iPhone solo mientras el cursor está ahí. Volvés por el borde y todo vuelve a la PC.
- **Portapapeles en ambos sentidos:** copiás texto o una imagen en el iPhone y lo pegás en Windows, o al revés. Por ejemplo, de un chat de WhatsApp a un documento de Word.

En el iPhone no se instala ninguna app nueva.

## Cómo funciona

```
Mouse y teclado:  [PC Windows] --USB--> [ESP32] --Bluetooth--> [iPhone]
Portapapeles:     [PC Windows] <---- Wi-Fi local ----> [iPhone, app Atajos]
```

- **Programa de Windows** (`desktop/`): detecta cuando el cursor cruza el borde, captura el mouse y el teclado y los envía por USB. También atiende los pedidos del portapapeles.
- **ESP32** (`firmware/`): se presenta ante el iPhone como un mouse y un teclado Bluetooth.
- **iPhone:** usa el soporte de mouse de AssistiveTouch, el de teclados Bluetooth y la app Atajos.

El protocolo entre la PC y la placa está en [docs/protocol.md](docs/protocol.md).

## Qué necesitás

- PC con Windows 10 y Python 3.11 o superior.
- Placa ESP32 DevKit (WROOM-32). Las ESP32-S2 no sirven porque no tienen Bluetooth.
- Cable USB que transmita datos.
- PlatformIO para cargar el firmware: `python -m pip install platformio`.
- Para el portapapeles, la PC y el iPhone en la misma red Wi-Fi.

Probado con Windows 10, Python 3.13 y una placa ESP32-WROOM-32D con chip CP2102.

## Instalación

### 1. Cargar la placa

Si Windows no reconoce la placa, instalá el driver de su chip USB (CP210x o CH340). Después, desde la carpeta del proyecto:

```
python -m platformio run -d firmware -t upload
```

Si aparece `Wrong boot mode`, mantené apretado el botón **BOOT** de la placa durante la carga.

### 2. Emparejar el iPhone

1. Activá Ajustes → Accesibilidad → Tocar → **AssistiveTouch**.
2. En Ajustes → Bluetooth, tocá **Interfaz Windows-iPhone**.

Para comprobar la conexión podés correr `python tools/test_device.py`: el puntero dibuja un cuadrado y escribe un texto de prueba.

### 3. Arrancar el programa

```
powershell -ExecutionPolicy Bypass -File run.ps1
```

Para cambiar el borde, el puerto o la velocidad, copiá `desktop/config.example.toml` como `desktop/config.local.toml` y editá los valores. Por defecto el iPhone va a la derecha de la pantalla.

## Uso

- **Pasar al iPhone:** empujá el cursor contra el borde configurado.
- **Volver a la PC:** empujá el puntero contra el borde del iPhone que da hacia la PC.
- **`Ctrl + Alt + Espacio`:** alterna entre la PC y el iPhone en cualquier momento.
- En el iPhone, la tecla **Windows** funciona como **Cmd** y **Alt** como **Option**. Si los símbolos salen cambiados, elegí tu distribución en Ajustes → General → Teclado → Teclado físico.

## Portapapeles en ambos sentidos

### Preparar la PC

Al arrancar, el programa muestra la dirección y la clave:

```
Portapapeles en http://<IP-de-tu-PC>:8765/clip  (clave: <tu-clave>)
```

La clave se genera en tu equipo la primera vez y queda en `desktop/clip_token.txt`, fuera del repositorio. **Es personal: no la publiques.**

Windows tiene que dejar entrar la conexión. Marcá tu Wi-Fi como red **privada** y, en una PowerShell abierta como administrador, ejecutá:

```
New-NetFirewallRule -DisplayName "Interfaz Windows-iPhone portapapeles" -Direction Inbound -Protocol TCP -LocalPort 8765 -Profile Private -Action Allow
```

### Crear los dos atajos

Los dos usan la **misma clave**. En la app Atajos, cada uno lleva la acción **Obtener contenido de URL** con la dirección que muestra el programa y este encabezado:

| Clave | Valor |
|---|---|
| `Authorization` | `Bearer <tu-clave>` |

**Enviar a PC** (del iPhone a Windows):

1. Acción **Obtener portapapeles**.
2. Acción **Obtener contenido de URL**, con método **POST** y cuerpo **Archivo** = Portapapeles.

**Traer de PC** (de Windows al iPhone):

1. Acción **Obtener contenido de URL**, con método **GET**.
2. Acción **Copiar al portapapeles**.

### Tenerlos a mano

- **Con el mouse:** en Ajustes → Accesibilidad → Tocar → AssistiveTouch → Dispositivos, asigná un atajo a un botón. En las acciones personalizadas de AssistiveTouch podés asignar otro al doble toque.
- **Sin mouse:** en Ajustes → Accesibilidad → Tocar → Tocar atrás, asignalos a un doble y un triple toque en la parte trasera del celular.

## Limitaciones

- La PC no sabe dónde está el puntero del iPhone: lo estima. Si no vuelve donde esperabas, empujá un poco más contra el borde o usá `Ctrl + Alt + Espacio`.
- `Ctrl + Alt + Supr` siempre lo atiende Windows.
- El portapapeles acepta texto e imágenes PNG, JPEG y GIF. HEIC no está soportado.
- Si la IP de tu PC cambia, hay que actualizarla en los atajos.

## Seguridad

- El portapapeles viaja por tu red local **sin cifrar**, por HTTP. La clave impide que otros lo lean o lo cambien sin ella, pero alguien que espíe la red podría ver el contenido y la clave. Usalo solo en redes de confianza.
- El programa de Windows captura el teclado y el mouse mientras el control está en el iPhone. No guarda lo que escribís: lo reenvía a la placa.

## Proyectos relacionados

Otros proyectos resuelven parte del mismo problema con enfoques parecidos, y vale la pena mirarlos:

- [esp-hid](https://github.com/akilaid/esp-hid)
- [DShare-HID](https://github.com/lockekk/dshare-hid)
- [esp32-kvm](https://github.com/KMChris/esp32-kvm)

## Licencia

[MIT](LICENSE).

Proyecto independiente, sin relación con Apple ni Microsoft. iPhone y Windows son marcas de sus respectivos dueños.
