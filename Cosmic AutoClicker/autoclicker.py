import json
import os
import threading
import time
from dataclasses import dataclass, asdict
from typing import List, Optional

import customtkinter as ctk
from pynput import mouse, keyboard
from pynput.mouse import Button as MouseButton
from pynput.keyboard import Key, KeyCode

try:
    from PIL import Image, ImageDraw
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

# ----------------------------------------------------------------------
# Config / persistence
# ----------------------------------------------------------------------

APP_DIR = os.path.join(os.path.expanduser("~"), ".fast_autoclicker")
MACROS_FILE = os.path.join(APP_DIR, "macros.json")
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
ICON_FILE = os.path.join(APP_DIR, "purple_planet.ico")
os.makedirs(APP_DIR, exist_ok=True)

DEFAULT_HOTKEYS = {
    "clicker": "<f6>",
    "record": "<f8>",
    "play": "<f9>",
}

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ---- Refened Galaxy Palette --------------------------------------------
BG = "#0b0c16"              
PANEL = "#121324"           
PANEL_ALT = "#1a1b35"       
BORDER = "#252746"          

TEXT_MUTED = "#9194b6"      
TEXT_FAINT = "#4d5075"      

ACCENT = "#7c3aed"          
ACCENT_HOVER = "#a78bfa"    

BTN_GREEN = "#0f766e"       
BTN_GREEN_HOVER = "#2dd4bf" 

BTN_RED = "#991b1b"         
BTN_RED_HOVER = "#f43f5e"   

BTN_BLUE = "#3730a3"        
BTN_BLUE_HOVER = "#6366f1"  

FONT_FAMILY = "Segoe UI"


def generate_purple_planet_icon():
    if os.path.exists(ICON_FILE):
        return
    if HAS_PILLOW:
        try:
            img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.arc([10, 100, 246, 170], start=180, end=360, fill="#a78bfa", width=16)
            draw.ellipse([48, 48, 208, 208], fill="#6d28d9", outline="#7c3aed", width=4)
            draw.chord([60, 100, 196, 140], start=160, end=340, fill="#8b5cf6")
            draw.arc([10, 100, 246, 170], start=0, end=180, fill="#a78bfa", width=16)
            img.save(ICON_FILE, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
            return
        except Exception:
            pass
    try:
        raw_ico_data = (
            b'\x00\x00\x01\x00\x01\x00\x10\x10\x00\x00\x01\x00\x18\x00\x30\x01'
            b'\x00\x00\x16\x00\x00\x00\x28\x00\x00\x00\x10\x00\x00\x00\x20\x00'
            b'\x00\x00\x01\x00\x18\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00'
            b'\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
            + (b'\x8b\x5c\xf6' * 256) + (b'\x00' * 64)
        )
        with open(ICON_FILE, "wb") as f:
            f.write(raw_ico_data)
    except Exception:
        pass


def precise_sleep(duration: float) -> None:
    if duration <= 0:
        return
    target = time.perf_counter() + duration
    coarse = duration - 0.0015
    if coarse > 0:
        time.sleep(coarse)
    while time.perf_counter() < target:
        pass


class AutoClicker:
    BUTTON_MAP = {
        "Left": MouseButton.left,
        "Right": MouseButton.right,
        "Middle": MouseButton.middle,
    }

    def __init__(self):
        self.mouse_ctl = mouse.Controller()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.on_state_change = None

    @property
    def running(self) -> bool:
        return self._running

    def start(self, cps: float, button: str, click_type: str,
              fixed_pos: Optional[tuple], max_clicks: int = 0):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            args=(cps, button, click_type, fixed_pos, max_clicks),
            daemon=True,
        )
        self._thread.start()
        if self.on_state_change:
            self.on_state_change(True)

    def stop(self):
        self._running = False
        if self.on_state_change:
            self.on_state_change(False)

    def toggle(self, cps: float, button: str, click_type: str,
               fixed_pos: Optional[tuple], max_clicks: int = 0):
        if self._running:
            self.stop()
        else:
            self.start(cps, button, click_type, fixed_pos, max_clicks)

    def _loop(self, cps, button_name, click_type, fixed_pos, max_clicks):
        btn = self.BUTTON_MAP.get(button_name, MouseButton.left)
        interval = 1.0 / max(cps, 0.001)
        clicks_done = 0

        if fixed_pos:
            self.mouse_ctl.position = fixed_pos

        while self._running:
            start = time.perf_counter()
            if click_type == "Double":
                self.mouse_ctl.click(btn, 2)
            else:
                self.mouse_ctl.click(btn, 1)
            clicks_done += 1
            if max_clicks and clicks_done >= max_clicks:
                self._running = False
                if self.on_state_change:
                    self.on_state_change(False)
                break
            elapsed = time.perf_counter() - start
            precise_sleep(interval - elapsed)


@dataclass
class MacroEvent:
    t: float
    kind: str
    x: Optional[int] = None
    y: Optional[int] = None
    button: Optional[str] = None
    pressed: Optional[bool] = None
    dx: Optional[int] = None
    dy: Optional[int] = None
    key: Optional[str] = None


class MacroRecorder:
    def __init__(self):
        self._events: List[MacroEvent] = []
        self._start_time = 0.0
        self._recording = False
        self._mouse_listener: Optional[mouse.Listener] = None
        self._kb_listener: Optional[keyboard.Listener] = None
        self._last_move_t = 0.0
        self.move_throttle = 0.02

    @property
    def recording(self) -> bool:
        return self._recording

    def start(self):
        self._events = []
        self._start_time = time.perf_counter()
        self._recording = True
        self._last_move_t = 0.0
        self._mouse_listener = mouse.Listener(
            on_move=self._on_move, on_click=self._on_click, on_scroll=self._on_scroll
        )
        self._kb_listener = keyboard.Listener(
            on_press=self._on_key_press, on_release=self._on_key_release
        )
        self._mouse_listener.start()
        self._kb_listener.start()

    def stop(self) -> List[MacroEvent]:
        self._recording = False
        if self._mouse_listener:
            self._mouse_listener.stop()
        if self._kb_listener:
            self._kb_listener.stop()
        return self._events

    def _now(self) -> float:
        return time.perf_counter() - self._start_time

    def _on_move(self, x, y):
        t = self._now()
        if t - self._last_move_t >= self.move_throttle:
            self._last_move_t = t
            self._events.append(MacroEvent(t=t, kind="move", x=int(x), y=int(y)))

    def _on_click(self, x, y, button, pressed):
        self._events.append(MacroEvent(
            t=self._now(), kind="click", x=int(x), y=int(y),
            button=button.name, pressed=pressed
        ))

    def _on_scroll(self, x, y, dx, dy):
        self._events.append(MacroEvent(
            t=self._now(), kind="scroll", x=int(x), y=int(y), dx=int(dx), dy=int(dy)
        ))

    def _on_key_press(self, key):
        self._events.append(MacroEvent(t=self._now(), kind="key_down", key=_key_to_str(key)))

    def _on_key_release(self, key):
        self._events.append(MacroEvent(t=self._now(), kind="key_up", key=_key_to_str(key)))


def _key_to_str(key) -> str:
    if isinstance(key, KeyCode):
        return f"char:{key.char}"
    return f"special:{key.name}"


def _str_to_key(s: str):
    if s.startswith("char:"):
        return KeyCode.from_char(s[5:])
    name = s.split(":", 1)[1]
    return getattr(Key, name)


class MacroPlayer:
    def __init__(self):
        self.mouse_ctl = mouse.Controller()
        self.kb_ctl = keyboard.Controller()
        self._stop_flag = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def play(self, events: List[MacroEvent], repeat: int = 1, on_done=None):
        self._stop_flag.clear()
        self._thread = threading.Thread(
            target=self._run, args=(events, repeat, on_done), daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop_flag.set()

    def _run(self, events, repeat, on_done):
        loops = 0
        while (repeat == 0 or loops < repeat) and not self._stop_flag.is_set():
            last_t = 0.0
            for ev in events:
                if self._stop_flag.is_set():
                    break
                precise_sleep(ev.t - last_t)
                last_t = ev.t
                self._execute(ev)
            loops += 1
        if on_done:
            on_done()

    def _execute(self, ev: MacroEvent):
        if ev.kind == "move":
            self.mouse_ctl.position = (ev.x, ev.y)
        elif ev.kind == "click":
            self.mouse_ctl.position = (ev.x, ev.y)
            btn = getattr(MouseButton, ev.button, MouseButton.left)
            if ev.pressed:
                self.mouse_ctl.press(btn)
            else:
                self.mouse_ctl.release(btn)
        elif ev.kind == "scroll":
            self.mouse_ctl.scroll(ev.dx, ev.dy)
        elif ev.kind == "key_down":
            self.kb_ctl.press(_str_to_key(ev.key))
        elif ev.kind == "key_up":
            self.kb_ctl.release(_str_to_key(ev.key))


def load_macros() -> dict:
    if os.path.exists(MACROS_FILE):
        try:
            with open(MACROS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_macros(data: dict):
    with open(MACROS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
                hotkeys = dict(DEFAULT_HOTKEYS)
                hotkeys.update(cfg.get("hotkeys", {}))
                cfg["hotkeys"] = hotkeys
                return cfg
        except Exception:
            pass
    return {"hotkeys": dict(DEFAULT_HOTKEYS)}


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def hotkey_to_display(hotkey_str: str) -> str:
    if hotkey_str.startswith("<") and hotkey_str.endswith(">"):
        return hotkey_str[1:-1].upper()
    return hotkey_str.upper()


def key_to_hotkey_str(key) -> Optional[str]:
    modifier_keys = {
        Key.shift, Key.shift_l, Key.shift_r,
        Key.ctrl, Key.ctrl_l, Key.ctrl_r,
        Key.alt, Key.alt_l, Key.alt_r,
        Key.cmd, Key.cmd_l, Key.cmd_r,
    }
    if key in modifier_keys:
        return None
    if isinstance(key, KeyCode):
        if key.char is None:
            return None
        return key.char.lower()
    return f"<{key.name}>"


def section_label(parent, text, **grid_kwargs):
    lbl = ctk.CTkLabel(
        parent, text=text, anchor="w",
        font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
        text_color=TEXT_MUTED,
    )
    lbl.grid(**grid_kwargs)
    return lbl


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Cosmic Auto Clicker")
        self.geometry("580x740")
        self.resizable(False, False)
        self.configure(fg_color=BG)

        generate_purple_planet_icon()
        if os.path.exists(ICON_FILE):
            try:
                self.iconbitmap(ICON_FILE)
            except Exception:
                pass

        self.config_data = load_config()
        self.hotkeys = self.config_data["hotkeys"]

        self.clicker = AutoClicker()
        self.clicker.on_state_change = self._on_clicker_state

        self.recorder = MacroRecorder()
        self.player = MacroPlayer()

        self.macros = load_macros()
        self.recorded_events: List[MacroEvent] = []

        self.fixed_pos: Optional[tuple] = None
        self._picking_pos = False
        self._capturing_hotkey_action: Optional[str] = None
        self._capture_listener: Optional[keyboard.Listener] = None

        self._build_ui()
        self._start_hotkeys()

    # ---------------- UI construction ----------------

    def _build_ui(self):
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=24, pady=(24, 4))

        ctk.CTkLabel(
            header_frame, text="🪐 Cosmic Auto Clicker",
            font=ctk.CTkFont(family=FONT_FAMILY, size=23, weight="bold"),
            text_color="#b39ddb"  
        ).pack(anchor="w")

        self.status_label = ctk.CTkLabel(
            header_frame, text="●  Orbiting Idle",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=TEXT_FAINT,
        )
        self.status_label.pack(anchor="w", pady=(4, 0))

        self.tabs = ctk.CTkTabview(
            self,
            fg_color=PANEL,
            segmented_button_fg_color=BG,
            segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_HOVER,
            segmented_button_unselected_color=PANEL,
            text_color=TEXT_MUTED,
            corner_radius=14,
        )
        self.tabs.pack(fill="both", expand=True, padx=24, pady=14)
        self.tabs.add("Clicker")
        self.tabs.add("Macros")
        self.tabs.add("Settings")

        self._build_clicker_tab(self.tabs.tab("Clicker"))
        self._build_macro_tab(self.tabs.tab("Macros"))
        self._build_settings_tab(self.tabs.tab("Settings"))

        self.footer_label = ctk.CTkLabel(
            self, text=self._footer_text(),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=TEXT_FAINT,
        )
        self.footer_label.pack(pady=(0, 2))

        credit_label = ctk.CTkLabel(
            self, text="Made by @purelyiris",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=TEXT_FAINT,
        )
        credit_label.pack(pady=(0, 14))

    def _footer_text(self):
        c = hotkey_to_display(self.hotkeys["clicker"])
        r = hotkey_to_display(self.hotkeys["record"])
        p = hotkey_to_display(self.hotkeys["play"])
        return f"{c}  Warp Clicker    {r}  Record Cosmic Macro    {p}  Play Macro"

    def _refresh_footer(self):
        self.footer_label.configure(text=self._footer_text())
        if self.clicker.running:
            self.start_stop_btn.configure(
                text=f"Stop Clicking ({hotkey_to_display(self.hotkeys['clicker'])})",
                fg_color=BTN_RED, hover_color=BTN_RED_HOVER
            )
        else:
            self.start_stop_btn.configure(
                text=f"Start Clicking ({hotkey_to_display(self.hotkeys['clicker'])})",
                fg_color=BTN_GREEN, hover_color=BTN_GREEN_HOVER
            )

        if self.recorder.recording:
            self.record_btn.configure(
                text=f"■ Stop ({hotkey_to_display(self.hotkeys['record'])})",
                fg_color=BTN_GREEN, hover_color=BTN_GREEN_HOVER
            )
        else:
            self.record_btn.configure(
                text=f"● Record ({hotkey_to_display(self.hotkeys['record'])})",
                fg_color=BTN_RED, hover_color=BTN_RED_HOVER
            )

        self.play_btn.configure(
            text=f"▶ Play Selected ({hotkey_to_display(self.hotkeys['play'])})"
        )

    def _build_clicker_tab(self, tab):
        tab.grid_columnconfigure((0, 1), weight=1)

        card = ctk.CTkFrame(tab, fg_color=PANEL_ALT, corner_radius=12, border_color=BORDER, border_width=1)
        card.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(14, 10))
        card.grid_columnconfigure((0, 1), weight=1)

        section_label(card, "CLICKS PER SECOND", row=0, column=0, columnspan=2,
                      sticky="w", padx=16, pady=(16, 4))
        self.cps_var = ctk.StringVar(value="10")
        self.cps_entry = ctk.CTkEntry(card, textvariable=self.cps_var, height=36,
                                       corner_radius=8, border_color=BORDER, fg_color=PANEL)
        self.cps_entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=16)

        section_label(card, "MOUSE BUTTON", row=2, column=0, sticky="w", padx=16, pady=(16, 4))
        self.button_var = ctk.StringVar(value="Left")
        btn_menu = ctk.CTkOptionMenu(
            card, values=["Left", "Right", "Middle"], variable=self.button_var,
            height=36, corner_radius=8, fg_color=PANEL, button_color=BORDER,
            button_hover_color=ACCENT,
        )
        btn_menu.grid(row=3, column=0, sticky="ew", padx=(16, 8))
        btn_menu._arrow_image = None  # Removes the low-res dot canvas arrow indicators

        section_label(card, "CLICK TYPE", row=2, column=1, sticky="w", padx=16, pady=(16, 4))
        self.click_type_var = ctk.StringVar(value="Single")
        type_menu = ctk.CTkOptionMenu(
            card, values=["Single", "Double"], variable=self.click_type_var,
            height=36, corner_radius=8, fg_color=PANEL, button_color=BORDER,
            button_hover_color=ACCENT,
        )
        type_menu.grid(row=3, column=1, sticky="ew", padx=(8, 16))
        type_menu._arrow_image = None  # Removes the low-res dot canvas arrow indicators

        section_label(card, "CLICK POSITION", row=4, column=0, columnspan=2,
                      sticky="w", padx=16, pady=(16, 4))
        pos_frame = ctk.CTkFrame(card, fg_color="transparent")
        pos_frame.grid(row=5, column=0, columnspan=2, sticky="ew", padx=16)
        pos_frame.grid_columnconfigure((0, 1), weight=1)

        self.pos_mode_var = ctk.StringVar(value="Current cursor position")
        self.pos_menu = ctk.CTkOptionMenu(
            pos_frame, values=["Current cursor position", "Fixed position"],
            variable=self.pos_mode_var, command=self._on_pos_mode_change,
            height=36, corner_radius=8, fg_color=PANEL, button_color=BORDER,
            button_hover_color=ACCENT,
        )
        self.pos_menu.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.pos_menu._arrow_image = None  # Removes the low-res dot canvas arrow indicators

        self.pick_pos_btn = ctk.CTkButton(
            pos_frame, text="Pick position (hover + Enter)", command=self._start_pick_pos,
            state="disabled", height=36, corner_radius=8,
            fg_color=BORDER, hover_color=ACCENT,
        )
        self.pick_pos_btn.grid(row=0, column=1, sticky="ew")

        self.pos_label = ctk.CTkLabel(card, text="No fixed quadrant targeted", text_color=TEXT_FAINT)
        self.pos_label.grid(row=6, column=0, columnspan=2, sticky="w", padx=16, pady=(6, 0))

        section_label(card, "MAX CLICKS  (0 = infinite cosmic loop)", row=7, column=0, columnspan=2,
                      sticky="w", padx=16, pady=(16, 4))
        self.max_clicks_var = ctk.StringVar(value="0")
        ctk.CTkEntry(card, textvariable=self.max_clicks_var, height=36,
                     corner_radius=8, border_color=BORDER, fg_color=PANEL).grid(
            row=8, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 16)
        )

        clicker_keybind_label = hotkey_to_display(self.hotkeys["clicker"])
        self.start_stop_btn = ctk.CTkButton(
            tab, text=f"Start Clicking ({clicker_keybind_label})", 
            fg_color=BTN_GREEN, hover_color=BTN_GREEN_HOVER,
            text_color="white", font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"), height=48,
            corner_radius=10, command=self._toggle_clicker
        )
        self.start_stop_btn.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(4, 8))

    def _build_macro_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)

        rec_frame = ctk.CTkFrame(tab, fg_color="transparent")
        rec_frame.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))
        rec_frame.grid_columnconfigure((0, 1), weight=1)

        record_keybind_label = hotkey_to_display(self.hotkeys["record"])
        self.record_btn = ctk.CTkButton(
            rec_frame, text=f"● Record ({record_keybind_label})", 
            fg_color=BTN_RED, hover_color=BTN_RED_HOVER,
            height=42, corner_radius=10, font=ctk.CTkFont(family=FONT_FAMILY, weight="bold"),
            command=self._toggle_recording
        )
        self.record_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        play_keybind_label = hotkey_to_display(self.hotkeys["play"])
        self.play_btn = ctk.CTkButton(
            rec_frame, text=f"▶ Play Selected ({play_keybind_label})", 
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            height=42, corner_radius=10, font=ctk.CTkFont(family=FONT_FAMILY, weight="bold"),
            command=self._play_selected_macro
        )
        self.play_btn.grid(row=0, column=1, sticky="ew")

        card = ctk.CTkFrame(tab, fg_color=PANEL_ALT, corner_radius=12, border_color=BORDER, border_width=1)
        card.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))
        card.grid_columnconfigure(0, weight=1)

        section_label(card, "REPEAT COUNT  (0 = loop through eternity)", row=0, column=0,
                      sticky="w", padx=16, pady=(14, 4))
        self.repeat_var = ctk.StringVar(value="1")
        ctk.CTkEntry(card, textvariable=self.repeat_var, height=36,
                     corner_radius=8, border_color=BORDER, fg_color=PANEL).grid(
            row=1, column=0, sticky="ew", padx=16, pady=(0, 14)
        )

        section_label(tab, "SAVED NEBULA MACROS", row=2, column=0, sticky="w", padx=16, pady=(6, 4))

        self.macro_list_frame = ctk.CTkScrollableFrame(
            tab, fg_color=PANEL_ALT, corner_radius=12, height=220, border_color=BORDER, border_width=1
        )
        self.macro_list_frame.grid(row=3, column=0, sticky="nsew", padx=14, pady=(0, 10))
        tab.grid_rowconfigure(3, weight=1)

        self.selected_macro_name: Optional[str] = None
        self._refresh_macro_list()

        save_frame = ctk.CTkFrame(tab, fg_color="transparent")
        save_frame.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 8))
        save_frame.grid_columnconfigure(0, weight=1)

        self.macro_name_var = ctk.StringVar(value="")
        ctk.CTkEntry(save_frame, textvariable=self.macro_name_var, height=36,
                     corner_radius=8, border_color=BORDER, fg_color=PANEL,
                     placeholder_text="Name the last cosmic event...").grid(
            row=0, column=0, sticky="ew", padx=(0, 8)
        )
        ctk.CTkButton(save_frame, text="Save Recording", height=36, corner_radius=8,
                      fg_color=BORDER, hover_color=ACCENT_HOVER,
                      command=self._save_recording).grid(row=0, column=1)

        self.macro_status = ctk.CTkLabel(tab, text="", text_color=TEXT_FAINT)
        self.macro_status.grid(row=5, column=0, sticky="w", padx=16)

    def _build_settings_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)

        card = ctk.CTkFrame(tab, fg_color=PANEL_ALT, corner_radius=12, border_color=BORDER, border_width=1)
        card.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 10))
        card.grid_columnconfigure(0, weight=1)
        card.grid_columnconfigure(1, weight=0)

        section_label(card, "KEYBOARD SHORTCUTS", row=0, column=0, columnspan=2,
                      sticky="w", padx=16, pady=(16, 8))

        self.hotkey_rows = {}
        actions = [
            ("clicker", "Start / Stop cosmic clicker"),
            ("record", "Start / Stop nebula recording"),
            ("play", "Play selected macro"),
        ]
        for i, (action, label) in enumerate(actions):
            row_idx = i + 1
            ctk.CTkLabel(card, text=label, anchor="w", text_color=TEXT_MUTED).grid(
                row=row_idx, column=0, sticky="w", padx=16, pady=8
            )
            btn = ctk.CTkButton(
                card, text=hotkey_to_display(self.hotkeys[action]), width=110,
                height=32, corner_radius=8, fg_color=PANEL, hover_color=ACCENT_HOVER,
                border_width=1, border_color=BORDER,
                command=lambda a=action: self._start_capture_hotkey(a),
            )
            btn.grid(row=row_idx, column=1, sticky="e", padx=16, pady=8)
            self.hotkey_rows[action] = btn

        ctk.CTkLabel(
            card, text="Click a shortcut, then press any key to rebind it.",
            text_color=TEXT_FAINT, font=ctk.CTkFont(size=11)
        ).grid(row=len(actions) + 1, column=0, columnspan=2, sticky="w", padx=16, pady=(4, 16))

        reset_btn = ctk.CTkButton(
            tab, text="Reset to defaults", fg_color=BORDER,
            hover_color=ACCENT_HOVER, height=36, corner_radius=8,
            command=self._reset_hotkeys,
        )
        reset_btn.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))

        self.settings_status = ctk.CTkLabel(tab, text="", text_color=TEXT_FAINT)
        self.settings_status.grid(row=2, column=0, sticky="w", padx=16)


    def _on_pos_mode_change(self, value):
        self.pick_pos_btn.configure(state="normal" if value == "Fixed position" else "disabled")

    def _start_pick_pos(self):
        self.pick_pos_btn.configure(text="Move mouse, then press Enter...")
        self._picking_pos = True

        def on_press(key):
            if key == Key.enter and self._picking_pos:
                ctl = mouse.Controller()
                self.fixed_pos = ctl.position
                self.after(0, lambda: self.pos_label.configure(
                    text=f"Fixed Quadrant: {self.fixed_pos[0]}, {self.fixed_pos[1]}"
                ))
                self.after(0, lambda: self.pick_pos_btn.configure(text="Pick position (hover + Enter)"))
                self._picking_pos = False
                return False

        listener = keyboard.Listener(on_press=on_press)
        listener.start()

    def _toggle_clicker(self):
        try:
            cps = float(self.cps_var.get())
            if cps <= 0:
                raise ValueError
        except ValueError:
            self._flash_status("Enter a valid CPS frequency > 0", error=True)
            return

        try:
            max_clicks = int(self.max_clicks_var.get())
        except ValueError:
            max_clicks = 0

        pos = None
        if self.pos_mode_var.get() == "Fixed position":
            if not self.fixed_pos:
                self._flash_status("Map a fixed coordinate first", error=True)
                return
            pos = self.fixed_pos

        self.clicker.toggle(
            cps=cps,
            button=self.button_var.get(),
            click_type=self.click_type_var.get(),
            fixed_pos=pos,
            max_clicks=max_clicks,
        )

    def _on_clicker_state(self, running: bool):
        def update():
            key_label = hotkey_to_display(self.hotkeys["clicker"])
            if running:
                self.start_stop_btn.configure(
                    text=f"Stop Clicking ({key_label})", fg_color=BTN_RED, hover_color=BTN_RED_HOVER, text_color="white"
                )
                self.status_label.configure(text="●  Beaming Clicks...", text_color=BTN_GREEN_HOVER)
            else:
                self.start_stop_btn.configure(
                    text=f"Start Clicking ({key_label})", fg_color=BTN_GREEN, hover_color=BTN_GREEN_HOVER, text_color="white"
                )
                self.status_label.configure(text="●  Orbiting Idle", text_color=TEXT_FAINT)
        self.after(0, update)

    def _flash_status(self, text, error=False):
        color = BTN_RED_HOVER if error else TEXT_FAINT
        self.status_label.configure(text=text, text_color=color)


    def _toggle_recording(self):
        key_label = hotkey_to_display(self.hotkeys["record"])
        if self.recorder.recording:
            self.recorded_events = self.recorder.stop()
            self.record_btn.configure(text=f"● Record ({key_label})", fg_color=BTN_RED, hover_color=BTN_RED_HOVER)
            self.status_label.configure(text="●  Orbiting Idle", text_color=TEXT_FAINT)
            self.macro_status.configure(
                text=f"Captured {len(self.recorded_events)} events. Log it in the archive.",
                text_color=TEXT_FAINT,
            )
        else:
            self.recorder.start()
            self.record_btn.configure(text=f"■ Stop ({key_label})", fg_color=BTN_GREEN, hover_color=BTN_GREEN_HOVER)
            self.status_label.configure(text="●  Recording Constellation...", text_color=BTN_RED_HOVER)
            self.macro_status.configure(text="", text_color=TEXT_FAINT)

    def _save_recording(self):
        name = self.macro_name_var.get().strip()
        if not name:
            self.macro_status.configure(text="Identify this galaxy stream with a name.", text_color=BTN_RED_HOVER)
            return
        if not self.recorded_events:
            self.macro_status.configure(text="No stellar anomalies captured yet.", text_color=BTN_RED_HOVER)
            return
        self.macros[name] = [asdict(e) for e in self.recorded_events]
        save_macros(self.macros)
        self.macro_name_var.set("")
        self.macro_status.configure(text=f"Archived macro '{name}'.", text_color=BTN_GREEN_HOVER)
        self._refresh_macro_list()

    def _refresh_macro_list(self):
        for widget in self.macro_list_frame.winfo_children():
            widget.destroy()

        if not self.macros:
            ctk.CTkLabel(self.macro_list_frame, text="Empty Space. No macros mapped yet.", text_color=TEXT_FAINT).pack(
                pady=10
            )
            return

        for name, events in self.macros.items():
            row = ctk.CTkFrame(self.macro_list_frame, fg_color=PANEL, corner_radius=8)
            row.pack(fill="x", pady=4, padx=4)
            row.grid_columnconfigure(0, weight=1)

            is_selected = name == self.selected_macro_name
            label = ctk.CTkLabel(
                row, text=f"{'🪐 ' if is_selected else ''}{name}  ({len(events)} particles)",
                anchor="w", text_color=ACCENT_HOVER if is_selected else TEXT_MUTED,
            )
            label.grid(row=0, column=0, sticky="ew", padx=10, pady=8)
            label.bind("<Button-1>", lambda e, n=name: self._select_macro(n))
            row.bind("<Button-1>", lambda e, n=name: self._select_macro(n))

            del_btn = ctk.CTkButton(
                row, text="Vaporize", width=60, height=28, corner_radius=6,
                fg_color=BTN_RED, hover_color=BTN_RED_HOVER,
                command=lambda n=name: self._delete_macro(n)
            )
            del_btn.grid(row=0, column=1, padx=8, pady=6)

    def _select_macro(self, name):
        self.selected_macro_name = name
        self._refresh_macro_list()

    def _delete_macro(self, name):
        self.macros.pop(name, None)
        if self.selected_macro_name == name:
            self.selected_macro_name = None
        save_macros(self.macros)
        self._refresh_macro_list()

    def _play_selected_macro(self):
        name = self.selected_macro_name
        if not name or name not in self.macros:
            self.macro_status.configure(text="Target a recorded constellation from the archive.", text_color=BTN_RED_HOVER)
            return
        try:
            repeat = int(self.repeat_var.get())
        except ValueError:
            repeat = 1

        events = [MacroEvent(**e) for e in self.macros[name]]
        self.status_label.configure(text=f"●  Simulating '{name}' stream...", text_color=BTN_BLUE_HOVER)

        def on_done():
            self.after(0, lambda: self.status_label.configure(text="●  Orbiting Idle", text_color=TEXT_FAINT))

        self.player.play(events, repeat=repeat, on_done=on_done)


    def _start_capture_hotkey(self, action: str):
        if self._capturing_hotkey_action is not None:
            return
        self._capturing_hotkey_action = action
        self.hotkey_rows[action].configure(text="Press a key...", fg_color=ACCENT)
        self.settings_status.configure(
            text="Press any key to map it. Press Esc to fall out of warp.", text_color=TEXT_FAINT
        )

        def on_press(key):
            if key == Key.esc:
                self.after(0, self._cancel_capture_hotkey)
                return False

            new_str = key_to_hotkey_str(key)
            if new_str is None:
                return

            self.after(0, lambda: self._finish_capture_hotkey(action, new_str))
            return False

        self._capture_listener = keyboard.Listener(on_press=on_press)
        self._capture_listener.start()

    def _cancel_capture_hotkey(self):
        action = self._capturing_hotkey_action
        if action:
            self.hotkey_rows[action].configure(
                text=hotkey_to_display(self.hotkeys[action]), fg_color=PANEL
            )
        self._capturing_hotkey_action = None
        self.settings_status.configure(text="Aborted transmissions.", text_color=TEXT_FAINT)

    def _finish_capture_hotkey(self, action: str, new_str: str):
        for other_action, other_key in self.hotkeys.items():
            if other_action != action and other_key == new_str:
                self.hotkey_rows[action].configure(
                    text=hotkey_to_display(self.hotkeys[action]), fg_color=PANEL
                )
                self._capturing_hotkey_action = None
                self.settings_status.configure(
                    text=f"'{hotkey_to_display(new_str)}' is already assigned in this sector.",
                    text_color=BTN_RED_HOVER,
                )
                return

        self.hotkeys[action] = new_str
        self.config_data["hotkeys"] = self.hotkeys
        save_config(self.config_data)

        self.hotkey_rows[action].configure(text=hotkey_to_display(new_str), fg_color=PANEL)
        self._capturing_hotkey_action = None
        self.settings_status.configure(
            text=f"Coordinates saved. Relay: {hotkey_to_display(new_str)}", text_color=BTN_GREEN_HOVER
        )

        self._refresh_footer()
        self._restart_hotkeys()

    def _reset_hotkeys(self):
        self.hotkeys = dict(DEFAULT_HOTKEYS)
        self.config_data["hotkeys"] = self.hotkeys
        save_config(self.config_data)
        for action, btn in self.hotkey_rows.items():
            btn.configure(text=hotkey_to_display(self.hotkeys[action]))
        self.settings_status.configure(text="Matrix reset to default sectors.", text_color=BTN_GREEN_HOVER)
        self._refresh_footer()
        self._restart_hotkeys()


    def _start_hotkeys(self):
        def on_clicker():
            self.after(0, self._toggle_clicker)

        def on_record():
            self.after(0, self._toggle_recording)

        def on_play():
            self.after(0, self._play_selected_macro)

        mapping = {
            self.hotkeys["clicker"]: on_clicker,
            self.hotkeys["record"]: on_record,
            self.hotkeys["play"]: on_play,
        }
        self.hotkey_listener = keyboard.GlobalHotKeys(mapping)
        self.hotkey_listener.start()

    def _restart_hotkeys(self):
        if hasattr(self, "hotkey_listener") and self.hotkey_listener:
            self.hotkey_listener.stop()
        self._start_hotkeys()

    def on_close(self):
        self.clicker.stop()
        self.player.stop()
        if self.recorder.recording:
            self.recorder.stop()
        if self._capture_listener:
            self._capture_listener.stop()
        if hasattr(self, "hotkey_listener") and self.hotkey_listener:
            self.hotkey_listener.stop()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()