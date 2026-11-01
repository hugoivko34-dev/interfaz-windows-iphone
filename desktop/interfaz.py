"""Windows desktop client for passing input to an iPhone through the ESP32 bridge."""
import atexit
import ctypes
import sys
import threading
import time
import tomllib
from pathlib import Path

import clipboard
from hid_keymap import CONSUMER_VK, MODIFIER_VK, SCAN_TO_HID
from link import CONSUMER, KEYS, MOUSE, RELEASE_ALL, SerialLink, find_port

if sys.platform != "win32":
    raise SystemExit("Interfaz Windows-iPhone requiere Windows 10.")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
# Work in physical pixels: low-level hook points are physical, so with display scaling
# (e.g. 150%) everything else must be too.
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2

LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x020A, 0x020E
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
LLKHF_EXTENDED, LLKHF_INJECTED = 0x01, 0x10
LLMHF_INJECTED = 0x01
SPI_SETCURSORS = 0x0057
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
SM_CXSCREEN, SM_CYSCREEN = 0, 1


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", ctypes.c_uint32), ("scanCode", ctypes.c_uint32),
                ("flags", ctypes.c_uint32), ("time", ctypes.c_uint32),
                ("dwExtraInfo", ctypes.c_size_t)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", ctypes.c_long * 2), ("mouseData", ctypes.c_uint32),
                ("flags", ctypes.c_uint32), ("time", ctypes.c_uint32),
                ("dwExtraInfo", ctypes.c_size_t)]


user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, WPARAM, LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, ctypes.c_uint32]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.GetMessageW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
user32.GetMessageW.restype = ctypes.c_int
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.CreateCursor.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
user32.CreateCursor.restype = ctypes.c_void_p
user32.CopyIcon.argtypes = [ctypes.c_void_p]
user32.CopyIcon.restype = ctypes.c_void_p
user32.SetSystemCursor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
user32.SystemParametersInfoW.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]

_restored = False
_active_app = None


def restore_cursors(*_):
    global _restored
    if not _restored:
        _restored = True
        user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)


def console_control(_event):
    if _active_app is not None and hasattr(_active_app, "hook_thread_id"):
        user32.PostThreadMessageW(_active_app.hook_thread_id, 0x0012, 0, 0)
    restore_cursors()
    return True


atexit.register(restore_cursors)


def load_config():
    folder = Path(__file__).resolve().parent
    local = folder / "config.local.toml"
    source = local if local.exists() else folder / "config.example.toml"
    with source.open("rb") as stream:
        config = tomllib.load(stream)
    config.setdefault("port", "auto")
    config.setdefault("edge", "right")
    config.setdefault("phone_width", 400)
    config.setdefault("phone_height", 870)
    config.setdefault("speed", 1.0)
    config.setdefault("return_overshoot", 40)
    config.setdefault("clipboard_port", 8765)
    if config["edge"] not in ("right", "left", "top", "bottom"):
        raise ValueError("edge debe ser right, left, top o bottom")
    return config


class App:
    def __init__(self, config):
        self.cfg = config
        self.link = None
        self.lock = threading.RLock()
        self.phone = False
        self.shutting_down = False
        self.entry = (0, 0)
        self.x = self.y = 0.0
        self.rem_x = self.rem_y = 0.0
        self.buttons = 0
        self.overshoot = 0.0
        self.modifiers = 0
        self.pressed = set()
        self.consumer_down = set()
        self.hotkey_down = False
        self.pc_seen_modifiers = set()
        self.physical_modifiers = set()
        self.center = (0, 0)
        self.vscreen = (0, 0, 0, 0)
        self.got_status = False
        self.thread = threading.Thread(target=self.hook_loop, name="Win32Hooks", daemon=True)

    def status(self, connected):
        self.got_status = True
        print("iPhone conectado." if connected else "iPhone desconectado.", flush=True)
        if not connected:
            with self.lock:
                if self.phone:
                    self.exit_phone()

    def error(self, exc):
        print(f"Error de serie: {exc}", flush=True)
        with self.lock:
            self.exit_phone()
        self.shutting_down = True
        user32.PostThreadMessageW(self.hook_thread_id, 0x0012, 0, 0)

    def start(self):
        port = find_port(self.cfg["port"])
        if not port:
            raise RuntimeError("No se detectó CP210x/CH340; indique el puerto en config.local.toml")
        print(f"Puerto encontrado: {port}", flush=True)
        if self.cfg["clipboard_port"]:
            clipboard.start(self.cfg["clipboard_port"])
        self.link = SerialLink(port, self.status, self.error)
        # A board that was just plugged in sometimes sits hung until reset; if it
        # doesn't answer the startup PING, reboot it through the EN line.
        deadline = time.monotonic() + 1.5
        while not self.got_status and time.monotonic() < deadline:
            time.sleep(0.05)
        if not self.got_status:
            print("La placa no responde; reiniciándola...", flush=True)
            self.link.reset()
        self.thread.start()
        self.thread.join()
        self.shutting_down = True
        with self.lock:
            self.exit_phone()
        if self.link:
            self.link.close()

    def stop(self):
        self.shutting_down = True
        with self.lock:
            self.exit_phone()
        if hasattr(self, "hook_thread_id"):
            user32.PostThreadMessageW(self.hook_thread_id, 0x0012, 0, 0)
        if self.link:
            self.link.close()

    def bounds(self):
        x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        return x, y, w, h

    def transparent_cursor(self):
        # One-bit AND mask set to 1 and XOR mask set to 0 makes a transparent cursor.
        size = 32 * 32 // 8
        and_mask = (ctypes.c_ubyte * size)(*([0xFF] * size))
        xor_mask = (ctypes.c_ubyte * size)()
        cursor = user32.CreateCursor(None, 0, 0, 32, 32, and_mask, xor_mask)
        if not cursor:
            return
        for cursor_id in (32512, 32513, 32514, 32515, 32516, 32640, 32641, 32642,
                          32643, 32644, 32645, 32646, 32648, 32649, 32650, 32651):
            copy = user32.CopyIcon(cursor)
            if copy:
                user32.SetSystemCursor(copy, cursor_id)

    def enter_phone(self, point=None, fraction=0.5):
        global _restored
        if self.phone or not self.link or not self.link.connected:
            return
        vx, vy, vw, vh = self.vscreen
        cx = user32.GetSystemMetrics(SM_CXSCREEN) // 2
        cy = user32.GetSystemMetrics(SM_CYSCREEN) // 2
        self.center = (cx, cy)
        self.entry = point or (vx, vy)
        self.phone = True
        _restored = False
        edge, w, h = self.cfg["edge"], self.cfg["phone_width"], self.cfg["phone_height"]
        # Calibrate: slam the phone pointer into the top/left corner of its PC-facing edge,
        # then slide it along that edge to the entry fraction.
        corner_x = 4000 if edge == "left" else -4000
        corner_y = 4000 if edge == "top" else -4000
        self.x = w if edge == "left" else 0.0
        self.y = h if edge == "top" else 0.0
        self.overshoot = 0
        self.rem_x = self.rem_y = 0
        user32.SetCursorPos(cx, cy)
        self.transparent_cursor()
        for _ in range(32):
            self.link.mouse(corner_x // 32, corner_y // 32, 0, 0)
        if edge in ("left", "right"):
            self.y = fraction * h
            self.link.mouse(0, round(self.y), 0, 0)
        else:
            self.x = fraction * w
            self.link.mouse(round(self.x), 0, 0, 0)
        print("-> iPhone", flush=True)

    def exit_phone(self):
        if not self.phone:
            restore_cursors()
            return
        self.phone = False
        if self.link:
            self.link.send(RELEASE_ALL)
        self.buttons = self.modifiers = 0
        self.pressed.clear()
        self.consumer_down.clear()
        restore_cursors()
        vx, vy, vw, vh = self.vscreen
        edge = self.cfg["edge"]
        if edge in ("left", "right"):
            yy = vy + int((self.y / self.cfg["phone_height"]) * max(1, vh - 1))
            xx = vx + 1 if edge == "left" else vx + vw - 2
        else:
            xx = vx + int((self.x / self.cfg["phone_width"]) * max(1, vw - 1))
            yy = vy + 1 if edge == "top" else vy + vh - 2
        user32.SetCursorPos(xx, yy)
        print("<- PC", flush=True)

    def mouse_proc(self, code, msg, data):
        if code < 0:
            return user32.CallNextHookEx(None, code, msg, data)
        event = ctypes.cast(data, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        if event.flags & LLMHF_INJECTED:
            return user32.CallNextHookEx(None, code, msg, data)
        px, py = event.pt[0], event.pt[1]
        with self.lock:
            if not self.phone:
                vx, vy, vw, vh = self.vscreen
                edge = self.cfg["edge"]
                lx, ly = self.last_mouse
                # Hook points are clipped to the screen, so pushing against the edge shows up
                # either as arriving at it or as a move that stays pinned there.
                crossing = msg == WM_MOUSEMOVE and (
                    (edge == "right" and px >= vx + vw - 1 and (px > lx or (lx >= vx + vw - 1 and py == ly))) or
                    (edge == "left" and px <= vx and (px < lx or (lx <= vx and py == ly))) or
                    (edge == "top" and py <= vy and (py < ly or (ly <= vy and px == lx))) or
                    (edge == "bottom" and py >= vy + vh - 1 and (py > ly or (ly >= vy + vh - 1 and px == lx))))
                if crossing and self.link and self.link.connected:
                    fraction = ((py - vy) / max(1, vh - 1)) if edge in ("left", "right") else ((px - vx) / max(1, vw - 1))
                    self.enter_phone((px, py), max(0.0, min(1.0, fraction)))
                    self.last_mouse = (px, py)
                    return 1
                self.last_mouse = (px, py)
                return user32.CallNextHookEx(None, code, msg, data)
            if msg == WM_MOUSEMOVE:
                dx, dy = px - self.center[0], py - self.center[1]
                self.handle_phone_move(dx, dy)
                return 1
            if msg in (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MBUTTONDOWN, WM_MBUTTONUP):
                bit = {WM_LBUTTONDOWN: 1, WM_LBUTTONUP: 1, WM_RBUTTONDOWN: 2, WM_RBUTTONUP: 2,
                       WM_MBUTTONDOWN: 4, WM_MBUTTONUP: 4}[msg]
                self.buttons = self.buttons | bit if msg in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN) else self.buttons & ~bit
                self.link.mouse(0, 0, 0, self.buttons)
                return 1
            if msg == WM_MOUSEWHEEL:
                delta = ctypes.c_short((event.mouseData >> 16) & 0xFFFF).value
                self.link.mouse(0, 0, int(delta / 120), self.buttons)
                return 1
            if msg == WM_MOUSEHWHEEL:
                return 1
        return user32.CallNextHookEx(None, code, msg, data)

    def handle_phone_move(self, dx, dy):
        edge = self.cfg["edge"]
        # The cursor recenter causes a synthetic move at the center; only actual displacement is applied.
        if abs(dx) > 3000 or abs(dy) > 3000:
            user32.SetCursorPos(*self.center)
            return
        fx = dx * self.cfg["speed"] + self.rem_x
        fy = dy * self.cfg["speed"] + self.rem_y
        send_x, send_y = int(fx), int(fy)
        self.rem_x, self.rem_y = fx - send_x, fy - send_y
        self.link.mouse(send_x, send_y, 0, self.buttons)
        self.x = min(self.cfg["phone_width"], max(0, self.x + send_x))
        self.y = min(self.cfg["phone_height"], max(0, self.y + send_y))
        towards_pc = ((edge == "right" and dx < 0 and self.x <= 0) or
                      (edge == "left" and dx > 0 and self.x >= self.cfg["phone_width"]) or
                      (edge == "top" and dy > 0 and self.y >= self.cfg["phone_height"]) or
                      (edge == "bottom" and dy < 0 and self.y <= 0))
        self.overshoot = self.overshoot + abs(dx or dy) if towards_pc else 0
        user32.SetCursorPos(*self.center)
        if self.overshoot > self.cfg["return_overshoot"]:
            self.exit_phone()

    def keyboard_proc(self, code, msg, data):
        if code < 0:
            return user32.CallNextHookEx(None, code, msg, data)
        key = ctypes.cast(data, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        if key.flags & LLKHF_INJECTED:
            return user32.CallNextHookEx(None, code, msg, data)
        down = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
        if msg not in (WM_KEYDOWN, WM_SYSKEYDOWN, WM_KEYUP, WM_SYSKEYUP):
            return user32.CallNextHookEx(None, code, msg, data)
        vk = key.vkCode
        with self.lock:
            if vk in MODIFIER_VK:
                if down:
                    self.physical_modifiers.add(vk)
                else:
                    self.physical_modifiers.discard(vk)
            ctrl = any(v in (0x11, 0xA2, 0xA3) for v in self.physical_modifiers)
            alt = any(v in (0x12, 0xA4, 0xA5) for v in self.physical_modifiers)
            is_space = vk == 0x20
            if ctrl and alt and is_space:
                if down and not self.hotkey_down:
                    self.hotkey_down = True
                    if self.phone:
                        self.exit_phone()
                    else:
                        self.enter_phone(fraction=0.5)
                if not down:
                    self.hotkey_down = False
                return 1
            if not self.phone:
                if vk in MODIFIER_VK:
                    if down:
                        self.pc_seen_modifiers.add(vk)
                    elif vk in self.pc_seen_modifiers:
                        self.pc_seen_modifiers.discard(vk)
                return user32.CallNextHookEx(None, code, msg, data)
            if not down and vk in self.pc_seen_modifiers:
                self.pc_seen_modifiers.discard(vk)
                return user32.CallNextHookEx(None, code, msg, data)
            if vk in MODIFIER_VK:
                bit = MODIFIER_VK[vk]
                self.modifiers = self.modifiers | bit if down else self.modifiers & ~bit
                self.send_keys()
                return 1
            if vk in CONSUMER_VK:
                usage = CONSUMER_VK[vk]
                if down and vk not in self.consumer_down:
                    self.consumer_down.add(vk)
                    self.link.send(CONSUMER, usage.to_bytes(2, "little"))
                elif not down and vk in self.consumer_down:
                    self.consumer_down.discard(vk)
                    self.link.send(CONSUMER, b"\0\0")
                return 1
            scan = (key.scanCode, bool(key.flags & LLKHF_EXTENDED))
            usage = SCAN_TO_HID.get(scan)
            if usage:
                if down:
                    if usage not in self.pressed and len(self.pressed) < 6:
                        self.pressed.add(usage)
                else:
                    self.pressed.discard(usage)
                self.send_keys()
            return 1

    def send_keys(self):
        keys = sorted(self.pressed)[:6]
        self.link.send(KEYS, bytes((self.modifiers, *keys, *([0] * (6 - len(keys))))))

    def hook_loop(self):
        self.hook_thread_id = kernel32.GetCurrentThreadId()
        self.vscreen = self.bounds()
        self.last_mouse = (self.vscreen[0] + self.vscreen[2] // 2, self.vscreen[1] + self.vscreen[3] // 2)
        mouse_cb, key_cb = HOOKPROC(self.mouse_proc), HOOKPROC(self.keyboard_proc)
        mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, mouse_cb, None, 0)
        key_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, key_cb, None, 0)
        if not mouse_hook or not key_hook:
            self.error(ctypes.WinError(ctypes.get_last_error()))
            return
        msg = ctypes.create_string_buffer(48)
        try:
            while user32.GetMessageW(msg, None, 0, 0) > 0:
                user32.TranslateMessage(msg)
                user32.DispatchMessageW(msg)
        finally:
            user32.UnhookWindowsHookEx(mouse_hook)
            user32.UnhookWindowsHookEx(key_hook)
            with self.lock:
                self.exit_phone()
            restore_cursors()


def main():
    global _active_app, _console_handler
    app = None
    try:
        config = load_config()
        app = App(config)
        _active_app = app
        _console_handler = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)(console_control)
        kernel32.SetConsoleCtrlHandler(_console_handler, True)
        app.start()
    except KeyboardInterrupt:
        if app:
            app.stop()
        restore_cursors()
    except Exception as exc:
        if app:
            app.stop()
        restore_cursors()
        print(f"Error: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
