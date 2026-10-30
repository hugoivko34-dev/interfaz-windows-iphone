"""Serial framing and nonblocking input queue for EdgeHop."""
import queue
import struct
import threading
import time

SYNC = 0xA5
MOUSE, KEYS, CONSUMER, RELEASE_ALL, PING, STATUS = 1, 2, 3, 4, 5, 0x81


def frame(message_type, payload=b""):
    if len(payload) > 32:
        raise ValueError("El payload supera los 32 bytes")
    checksum = message_type ^ len(payload)
    for value in payload:
        checksum ^= value
    return bytes((SYNC, message_type, len(payload))) + payload + bytes((checksum,))


def find_port(configured):
    from serial.tools import list_ports
    ports = list_ports.comports()
    if configured.lower() == "auto":
        return next((p.device for p in ports if p.vid in (0x10C4, 0x1A86)), None)
    return next((p.device for p in ports if p.device.lower() == configured.lower()), configured)


class SerialLink:
    def __init__(self, port_name, on_status, on_error):
        import serial
        device = serial.Serial(None, 921600, timeout=0.1, write_timeout=1)
        device.port = port_name
        device.dtr = False
        device.rts = False
        device.open()
        self.device = device
        self.on_status = on_status
        self.on_error = on_error
        self.connected = False
        self.queue = queue.Queue(maxsize=256)
        self.stopping = threading.Event()
        self.writer = threading.Thread(target=self._write_loop, daemon=True)
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.writer.start()
        self.reader.start()
        self.send(PING)

    def send(self, kind, payload=b""):
        if not self.stopping.is_set():
            item = (kind, payload)
            try:
                self.queue.put_nowait(item)
            except queue.Full:
                # Make room by discarding stale movement; state reports remain queued.
                retained = []
                removed = False
                while True:
                    try:
                        old = self.queue.get_nowait()
                    except queue.Empty:
                        break
                    if old[0] == MOUSE and not removed:
                        removed = True
                    else:
                        retained.append(old)
                for old in retained:
                    try:
                        self.queue.put_nowait(old)
                    except queue.Full:
                        break
                try:
                    self.queue.put_nowait(item)
                except queue.Full:
                    # A queue full of state reports is an exceptional overload; fail safe.
                    self.on_error(RuntimeError("Cola serie saturada"))

    def mouse(self, dx, dy, wheel, buttons):
        item = (MOUSE, struct.pack("<hhbB", max(-32768, min(32767, dx)),
                                   max(-32768, min(32767, dy)),
                                   max(-128, min(127, wheel)), buttons))
        try:
            self.queue.put_nowait(item)
        except queue.Full:
            # Consolidate pending movement to preserve responsiveness.
            pending, total_x, total_y, latest = [], 0, 0, buttons
            while True:
                try:
                    old = self.queue.get_nowait()
                except queue.Empty:
                    break
                if old[0] == MOUSE:
                    x, y, _w, latest = struct.unpack("<hhbB", old[1])
                    total_x += x
                    total_y += y
                else:
                    pending.append(old)
            # Retain control/key state messages first, dropping oldest movements if needed.
            for old in pending:
                try:
                    self.queue.put_nowait(old)
                except queue.Full:
                    break
            merged = (MOUSE, struct.pack("<hhbB",
                max(-32768, min(32767, total_x + dx)), max(-32768, min(32767, total_y + dy)), 0, buttons))
            try:
                self.queue.put_nowait(merged)
            except queue.Full:
                pass

    def _write_loop(self):
        last_ping = 0.0
        try:
            while not self.stopping.is_set():
                now = time.monotonic()
                if now - last_ping >= 2:
                    self.device.write(frame(PING))
                    last_ping = now
                try:
                    kind, payload = self.queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                self.device.write(frame(kind, payload))
        except Exception as exc:
            if not self.stopping.is_set():
                self.on_error(exc)

    def _read_loop(self):
        buffer = bytearray()
        try:
            while not self.stopping.is_set():
                buffer.extend(self.device.read(max(1, self.device.in_waiting)))
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
                    if checksum == packet[-1] and packet[1] == STATUS and length == 1:
                        self.connected = packet[3] == 1
                        self.on_status(self.connected)
        except Exception as exc:
            if not self.stopping.is_set():
                self.on_error(exc)

    def reset(self):
        # RTS drives the ESP32 EN pin; DTR (IO0) stays low so it boots normally.
        self.device.rts = True
        time.sleep(0.1)
        self.device.rts = False

    def close(self):
        self.stopping.set()
        try:
            self.device.close()
        except Exception:
            pass
