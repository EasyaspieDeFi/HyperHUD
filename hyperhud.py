"""
HyperHUD - a tiny, click-through, always-on-top Hyperliquid ticker for Windows.

Run with:  pythonw hyperhud.py      (no console window)
Hotkeys:   Ctrl+Alt+H  open/close (hidden = no polling, ~0 CPU)
           Ctrl+Alt+Q  fully quit (until next login or relaunch)
Config:    config.json next to this file (created on first run)

Works over games running in *borderless windowed* or windowed mode.
Exclusive fullscreen games draw over every other window, including this one.
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import queue
import threading
import time
import tkinter as tk
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
API = "https://api.hyperliquid.xyz/info"

DEFAULT_CONFIG = {
    "coins": ["BTC", "ETH", "HYPE"],
    # Your Hyperliquid address (0x...). Public, read-only: used only to show positions.
    "wallet": "",
    # top-left | top-right | bottom-left | bottom-right
    "corner": "top-left",
    "offset_x": 12,
    "offset_y": 12,
    "font": "Consolas",
    "font_size": 12,
    "bold": True,
    "opacity": 1.0,
    # Translucent dark plate behind the text (0 = off). Makes it readable on bright scenes.
    "backdrop_opacity": 0.5,
    "price_poll_seconds": 2,
    "positions_poll_seconds": 3,
    "show_24h_change": True,
    "show_positions": True,
}

FG = "#ffffff"
DIM = "#cfcfcf"
UP = "#3dff7a"
DOWN = "#ff5c5c"
KEY = "#010101"  # transparent color key; near-black so text edges read as an outline


# --------------------------------------------------------------------------- config

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:  # keep running on a bad config
            print("config error:", e)
    else:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
    return cfg


# --------------------------------------------------------------------------- data

def post(body, timeout=6):
    req = urllib.request.Request(
        API,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


class Feed:
    """Polls Hyperliquid on background threads; the UI reads `snapshot()`."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.mids = {}
        self.prev_day = {}
        self.positions = []
        self.account_value = None
        self.last_price_ok = 0.0
        self.last_pos_ok = 0.0
        self.error = None
        self.active = threading.Event()  # cleared while hidden -> no network calls
        self.active.set()

    def start(self):
        threading.Thread(target=self._price_loop, daemon=True).start()
        threading.Thread(target=self._prevday_loop, daemon=True).start()
        if self.cfg.get("wallet") and self.cfg.get("show_positions"):
            threading.Thread(target=self._pos_loop, daemon=True).start()

    def _price_loop(self):
        while True:
            self.active.wait()
            try:
                mids = post({"type": "allMids"})
                with self.lock:
                    self.mids = {k: float(v) for k, v in mids.items() if not k.startswith("@")}
                    self.last_price_ok = time.time()
                    self.error = None
            except Exception as e:
                with self.lock:
                    self.error = str(e)
            time.sleep(max(1, self.cfg["price_poll_seconds"]))

    def _prevday_loop(self):
        while True:
            self.active.wait()
            try:
                meta, ctxs = post({"type": "metaAndAssetCtxs"}, timeout=10)
                prev = {}
                for asset, ctx in zip(meta["universe"], ctxs):
                    if ctx.get("prevDayPx"):
                        prev[asset["name"]] = float(ctx["prevDayPx"])
                with self.lock:
                    self.prev_day = prev
                time.sleep(60)
            except Exception:
                time.sleep(10)

    def _pos_loop(self):
        while True:
            self.active.wait()
            try:
                st = post({"type": "clearinghouseState", "user": self.cfg["wallet"]})
                positions = []
                for ap in st.get("assetPositions", []):
                    p = ap.get("position", {})
                    szi = float(p.get("szi", 0))
                    if szi == 0:
                        continue
                    positions.append({
                        "coin": p.get("coin"),
                        "size": szi,
                        "entry": float(p.get("entryPx") or 0),
                        "upnl": float(p.get("unrealizedPnl") or 0),
                        "roe": float(p.get("returnOnEquity") or 0),
                        "liq": float(p["liquidationPx"]) if p.get("liquidationPx") else None,
                    })
                positions.sort(key=lambda x: -abs(x["upnl"]))
                with self.lock:
                    self.positions = positions
                    self.account_value = float(st.get("marginSummary", {}).get("accountValue", 0))
                    self.last_pos_ok = time.time()
            except Exception:
                pass
            time.sleep(max(1, self.cfg["positions_poll_seconds"]))

    def snapshot(self):
        with self.lock:
            return {
                "mids": dict(self.mids),
                "prev_day": dict(self.prev_day),
                "positions": list(self.positions),
                "account_value": self.account_value,
                "last_price_ok": self.last_price_ok,
                "last_pos_ok": self.last_pos_ok,
                "error": self.error,
            }


# --------------------------------------------------------------------------- formatting

def fmt_px(v):
    if v >= 1000:
        return f"{v:,.0f}"
    if v >= 10:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:,.3f}"
    return f"{v:.5f}"


def fmt_usd(v, signed=True):
    s = f"${abs(v):,.2f}" if abs(v) < 10000 else f"${abs(v):,.0f}"
    if not signed:
        return s
    return ("+" if v >= 0 else "-") + s


def fmt_size(v):
    a = abs(v)
    if a >= 1000:
        return f"{a:,.0f}"
    if a >= 1:
        return f"{a:,.2f}".rstrip("0").rstrip(".")
    return f"{a:.4f}".rstrip("0").rstrip(".")


# --------------------------------------------------------------------------- win32

user32 = ctypes.windll.user32
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_APPWINDOW = 0x00040000

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x1, 0x2, 0x4000
WM_HOTKEY = 0x0312
HOTKEYS = {1: ("toggle", ord("H")), 2: ("quit", ord("Q"))}


def make_click_through(root):
    hwnd = user32.GetParent(root.winfo_id())
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style = (style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)


def hotkey_thread(events: "queue.Queue[str]"):
    for hid, (_, vk) in HOTKEYS.items():
        if not user32.RegisterHotKey(None, hid, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, vk):
            print(f"could not register hotkey Ctrl+Alt+{chr(vk)} (in use by another app?)")
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == WM_HOTKEY and msg.wParam in HOTKEYS:
            events.put(HOTKEYS[msg.wParam][0])


# --------------------------------------------------------------------------- UI

class Overlay:
    def __init__(self, cfg, feed):
        self.cfg = cfg
        self.feed = feed
        self.events = queue.Queue()
        self.visible = True

        self.root = tk.Tk()
        self.root.title("HyperHUD")

        # Optional translucent backdrop: a separate window under the text so the
        # text itself can stay fully opaque.
        self.backdrop = None
        bd_alpha = float(cfg.get("backdrop_opacity", 0) or 0)
        if bd_alpha > 0:
            self.backdrop = tk.Toplevel(self.root)
            self.backdrop.overrideredirect(True)
            self.backdrop.attributes("-topmost", True)
            self.backdrop.attributes("-alpha", min(bd_alpha, 1.0))
            self.backdrop.configure(bg="#000000")
            self.backdrop.geometry("1x1+0+0")

        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", float(cfg["opacity"]))
        self.root.configure(bg=KEY)
        self.root.wm_attributes("-transparentcolor", KEY)

        self.font = (cfg["font"], int(cfg["font_size"])) + (("bold",) if cfg.get("bold", True) else ())
        self.canvas = tk.Canvas(self.root, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack()

        self.root.update_idletasks()
        make_click_through(self.root)
        if self.backdrop:
            make_click_through(self.backdrop)
        threading.Thread(target=hotkey_thread, args=(self.events,), daemon=True).start()

        self._tick_topmost()
        self._tick_render()

    # Each row is a list of (text, color) segments; columns line up because the font is monospace.
    def build_rows(self):
        s = self.feed.snapshot()
        now = time.time()
        stale = now - s["last_price_ok"] > 10
        rows = []

        coins = self.cfg["coins"]
        w = max(len(c) for c in coins)
        cells = []
        for c in coins:
            px = s["mids"].get(c)
            ptxt = fmt_px(px) if px else "--"
            chg = None
            if self.cfg["show_24h_change"] and px and s["prev_day"].get(c):
                chg = (px / s["prev_day"][c] - 1) * 100
            cells.append((c, ptxt, chg))
        pw = max(len(p) for _, p, _ in cells)
        for c, ptxt, chg in cells:
            row = [(c.ljust(w) + " ", DIM), (ptxt.rjust(pw), DIM if stale else FG)]
            if chg is not None:
                row.append((f" {chg:+5.1f}%", DIM if stale else (UP if chg >= 0 else DOWN)))
            rows.append(row)

        if self.cfg.get("wallet") and self.cfg.get("show_positions") and s["last_pos_ok"]:
            pos_stale = now - s["last_pos_ok"] > 15
            positions = s["positions"]
            if positions:
                rows.append([("", FG)])
                cw = max(len(p["coin"]) for p in positions)
                for p in positions[:6]:
                    side = "L" if p["size"] > 0 else "S"
                    col = UP if p["upnl"] >= 0 else DOWN
                    rows.append([
                        (p["coin"].ljust(cw) + " ", DIM),
                        (f"{side} {fmt_size(p['size'])}", UP if side == "L" else DOWN),
                        (f"  {fmt_usd(p['upnl'])}", DIM if pos_stale else col),
                        (f" {p['roe'] * 100:+.1f}%", DIM),
                    ])
            total = sum(p["upnl"] for p in positions)
            if s["account_value"] is not None:
                row = [("acct ", DIM), (fmt_usd(s["account_value"], signed=False), DIM if pos_stale else FG)]
                if positions:
                    row.append((f"  {fmt_usd(total)}", DIM if pos_stale else (UP if total >= 0 else DOWN)))
                rows.append(row)

        if stale:
            rows.append([("offline" if not s["last_price_ok"] else "stale", DOWN)])
        return rows

    def render(self, rows):
        c = self.canvas
        c.delete("all")
        pad = 8 if self.backdrop else 4
        line_h = int(self.cfg["font_size"] * 1.75)
        max_w = 0
        y = pad
        for row in rows:
            x = pad
            for text, color in row:
                if text:
                    # Black outline keeps it readable on bright game scenes.
                    # Drawn as #000001, not pure black, so it isn't keyed out with KEY's fringe.
                    for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1), (2, 2)):
                        c.create_text(x + dx, y + dy, text=text, fill="#000001", font=self.font, anchor="nw")
                    item = c.create_text(x, y, text=text, fill=color, font=self.font, anchor="nw")
                    x2 = c.bbox(item)[2]
                    x = x2
            max_w = max(max_w, x)
            y += line_h
        w, h = max_w + pad + 1, y + pad
        c.configure(width=w, height=h)
        self.place(w, h)

    def place(self, w, h):
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        ox, oy = int(self.cfg["offset_x"]), int(self.cfg["offset_y"])
        corner = self.cfg["corner"]
        x = sw - w - ox if "right" in corner else ox
        y = sh - h - oy if "bottom" in corner else oy
        geo = f"{w}x{h}+{x}+{y}"
        if self.backdrop and self.backdrop.geometry() != geo:
            self.backdrop.geometry(geo)
        if self.root.geometry() != geo:
            self.root.geometry(geo)

    def _tick_render(self):
        while not self.events.empty():
            ev = self.events.get()
            if ev == "quit":
                self.root.destroy()
                os._exit(0)
            if ev == "toggle":
                self.visible = not self.visible
                if self.visible:
                    self.feed.active.set()
                    if self.backdrop:
                        self.backdrop.deiconify()
                        make_click_through(self.backdrop)
                    self.root.deiconify()
                    make_click_through(self.root)
                else:
                    self.feed.active.clear()
                    self.root.withdraw()
                    if self.backdrop:
                        self.backdrop.withdraw()
        if self.visible:
            try:
                self.render(self.build_rows())
            except Exception as e:
                print("render error:", e)
        self.root.after(500, self._tick_render)

    def _tick_topmost(self):
        # Games sometimes push themselves above topmost windows; re-assert periodically.
        if self.visible:
            if self.backdrop:
                self.backdrop.attributes("-topmost", True)
                self.backdrop.lift()
            self.root.attributes("-topmost", True)
            self.root.lift()
        self.root.after(3000, self._tick_topmost)

    def run(self):
        self.root.mainloop()


def main():
    # Single instance: if one is already running, do nothing (use Ctrl+Alt+H to show it).
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\HyperHUDSingleInstance")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # crisp text on high-DPI displays
    except Exception:
        pass
    cfg = load_config()
    feed = Feed(cfg)
    feed.start()
    Overlay(cfg, feed).run()


if __name__ == "__main__":
    main()
