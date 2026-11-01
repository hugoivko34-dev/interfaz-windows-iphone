"""Shares the Windows clipboard with the iPhone over the local network.

The iPhone's Shortcuts app talks to this server:
  POST /clip  body = text or an image  -> put it on the Windows clipboard
  GET  /clip                           -> current Windows clipboard (text or PNG)
Every request must carry "Authorization: Bearer <token>".
"""
import ctypes
import hmac
import os
import secrets
import socket
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FOLDER = Path(__file__).resolve().parent
TOKEN_FILE = FOLDER / "clip_token.txt"
IMAGE_SCRIPT = FOLDER / "clip_image.ps1"
MAX_BODY = 50 * 1024 * 1024

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.OpenClipboard.argtypes = [ctypes.c_void_p]
user32.GetClipboardData.argtypes = [ctypes.c_uint]
user32.GetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]

CF_UNICODETEXT, CF_DIB = 13, 8
GMEM_MOVEABLE = 0x0002

# Image signatures .NET can decode; anything else is treated as UTF-8 text.
IMAGE_MAGIC = (b"\x89PNG\r\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")


def load_token():
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(12)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    return token


def local_ip():
    # Connecting a UDP socket sends nothing; it just picks the LAN interface.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.168.0.1", 9))
            return probe.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def open_clipboard():
    for _ in range(20):
        if user32.OpenClipboard(None):
            return
        threading.Event().wait(0.05)
    raise OSError("El portapapeles de Windows está ocupado")


def set_text(text):
    data = (text.replace("\r\n", "\n").replace("\n", "\r\n") + "\0").encode("utf-16-le")
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    ctypes.memmove(kernel32.GlobalLock(handle), data, len(data))
    kernel32.GlobalUnlock(handle)
    open_clipboard()
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        user32.CloseClipboard()


def get_text():
    open_clipboard()
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        text = ctypes.wstring_at(kernel32.GlobalLock(handle))
        kernel32.GlobalUnlock(handle)
        return text
    finally:
        user32.CloseClipboard()


def run_image_script(mode, path):
    return subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
         "-File", str(IMAGE_SCRIPT), mode, path],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=30).returncode


def set_image(data):
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "clip.img")
        with open(path, "wb") as stream:
            stream.write(data)
        if run_image_script("set", path) != 0:
            raise OSError("Windows no pudo leer la imagen")


def get_image():
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "clip.png")
        if run_image_script("get", path) != 0:
            return None
        with open(path, "rb") as stream:
            return stream.read()


class Handler(BaseHTTPRequestHandler):
    token = ""

    def log_message(self, *_):
        pass

    def reply(self, status, body=b"", kind="text/plain; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        if self.path.split("?")[0] != "/clip":
            self.reply(404, b"No encontrado")
            return False
        sent = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(sent, self.token):
            self.reply(401, "Clave incorrecta".encode())
            return False
        return True

    def do_POST(self):
        if not self.authorized():
            return
        length = int(self.headers.get("Content-Length", 0))
        if not 0 < length <= MAX_BODY:
            self.reply(400, "Contenido vacío o demasiado grande".encode())
            return
        data = self.rfile.read(length)
        try:
            if data.startswith(IMAGE_MAGIC):
                set_image(data)
                print("Portapapeles: imagen recibida del iPhone.", flush=True)
            else:
                set_text(data.decode("utf-8", errors="replace"))
                print("Portapapeles: texto recibido del iPhone.", flush=True)
        except Exception as exc:
            self.reply(500, str(exc).encode())
            return
        self.reply(200, b"OK")

    def do_GET(self):
        if not self.authorized():
            return
        try:
            if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                self.reply(200, get_text().encode("utf-8"))
            elif user32.IsClipboardFormatAvailable(CF_DIB) and (png := get_image()):
                self.reply(200, png, "image/png")
            else:
                self.reply(204)
                return
        except Exception as exc:
            self.reply(500, str(exc).encode())
            return
        print("Portapapeles: enviado al iPhone.", flush=True)


def start(port):
    Handler.token = load_token()
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, name="Clipboard", daemon=True).start()
    print(f"Portapapeles en http://{local_ip()}:{port}/clip  (clave: {Handler.token})", flush=True)
    return server
