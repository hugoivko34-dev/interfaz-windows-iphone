#!/usr/bin/env python3
"""Exercise an Interfaz Windows-iPhone board over its serial protocol."""
import argparse
import struct
import sys
import time

import serial
from serial.tools import list_ports

SYNC = 0xA5
PING, MOUSE, KEYS, RELEASE_ALL, STATUS = 0x05, 0x01, 0x02, 0x04, 0x81


def frame(message_type, payload=b""):
    if len(payload) > 32:
        raise ValueError("El payload supera los 32 bytes")
    checksum = message_type ^ len(payload)
    for value in payload:
        checksum ^= value
    return bytes((SYNC, message_type, len(payload))) + payload + bytes((checksum,))


def find_port(explicit_port):
    for port in list_ports.comports():
        if port.vid in (0x10C4, 0x1A86):
            return port.device
    return explicit_port


def read_frames(port, timeout):
    """Yield valid protocol frames using a small resynchronizing byte parser."""
    deadline = time.monotonic() + timeout
    buffer = bytearray()
    while time.monotonic() < deadline:
        data = port.read(max(1, port.in_waiting))
        buffer.extend(data)
        while buffer:
            try:
                start = buffer.index(SYNC)
            except ValueError:
                buffer.clear()
                break
            del buffer[:start]
            if len(buffer) < 3:
                break
            length = buffer[2]
            if length > 32:
                del buffer[0]
                continue
            total = length + 4
            if len(buffer) < total:
                break
            packet = bytes(buffer[:total])
            del buffer[:total]
            checksum = packet[1] ^ packet[2]
            for value in packet[3:-1]:
                checksum ^= value
            if checksum == packet[-1]:
                yield packet[1], packet[3:-1]


def main():
    parser = argparse.ArgumentParser(description="Prueba serial de Interfaz Windows-iPhone")
    parser.add_argument("--port", help="Puerto serie (fallback si no se detecta CP210x/CH340)")
    args = parser.parse_args()
    port_name = find_port(args.port)
    if not port_name:
        parser.error("No se detectó CP210x/CH340; indique --port")

    # DTR/RTS are wired to the ESP32 reset circuit; keep them low so opening the port doesn't reboot it.
    device = serial.Serial(None, 921600, timeout=0.1)
    device.port = port_name
    device.dtr = False
    device.rts = False
    device.open()
    with device:
        print(f"Conectado al puerto {port_name}. Consultando el estado...")
        device.write(frame(PING))
        connected = False
        for message_type, payload in read_frames(device, 30):
            if message_type == STATUS and len(payload) == 1:
                connected = payload[0] == 1
                if connected:
                    print("iPhone conectado.")
                    break
                print("iPhone no conectado. Empareje «Interfaz Windows-iPhone» en Ajustes > Bluetooth del iPhone.")
        if not connected:
            print("Tiempo agotado. Empareje «Interfaz Windows-iPhone» en Ajustes > Bluetooth del iPhone y vuelva a intentar.")
            return 1

        print("Moviendo el puntero en un cuadrado...")
        sides = ((24, 0), (0, 24), (-24, 0), (0, -24))
        for dx, dy in sides:
            for _ in range(20):
                device.write(frame(MOUSE, struct.pack("<hhbB", dx, dy, 0, 0)))
                time.sleep(0.025)

        print("Escribiendo: hola interfaz")
        keycodes = {"h": 0x0B, "o": 0x12, "l": 0x0F, "a": 0x04,
                    "i": 0x0C, "n": 0x11, "t": 0x17, "e": 0x08, "r": 0x15,
                    "f": 0x09, "z": 0x1D, " ": 0x2C}
        for character in "hola interfaz":
            report = bytes((0, keycodes[character], 0, 0, 0, 0, 0))
            device.write(frame(KEYS, report))
            time.sleep(0.06)
            device.write(frame(KEYS, bytes(7)))
            time.sleep(0.06)
        device.write(frame(RELEASE_ALL))
        print("Prueba finalizada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
