"""480x320 touchscreen GUI: Main / About tabs plus always-on status bar."""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import ttk
from typing import Optional

from raspberryduc.elm327 import ElmError
from raspberryduc.protocol import ConnectionInfo, M3CSession
from raspberryduc.version import (
    APP_NAME,
    APP_TITLE,
    COPYRIGHT,
    ECU_TARGET,
    GPL_NOTICE,
    LICENSE_NAME,
    __version__,
)

LOG = logging.getLogger(__name__)

SCREEN_W = 480
SCREEN_H = 320
BG = "#1a1a1a"
BG_PANEL = "#242424"
FG = "#f2f2f2"
FG_DIM = "#b0b0b0"
ACCENT = "#c40000"
OK = "#3dcc6d"
WARN = "#e0a100"
ERR = "#e05555"

FONT = ("DejaVu Sans", 10)
FONT_SM = ("DejaVu Sans", 9)
FONT_MD = ("DejaVu Sans", 11, "bold")
FONT_LG = ("DejaVu Sans", 12, "bold")


class RaspberryDucApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.configure(bg=BG)
        self.geometry(f"{SCREEN_W}x{SCREEN_H}+0+0")
        self.minsize(SCREEN_W, SCREEN_H)
        self.resizable(False, False)
        try:
            self.attributes("-fullscreen", True)
        except tk.TclError:
            pass
        self.bind("<Escape>", lambda _e: self.destroy())

        self.session = M3CSession()
        self._busy = False
        self._lock = threading.Lock()
        self._scan_after: Optional[str] = None

        self._apply_style()
        self._build()
        self._set_disconnected()

    def _apply_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=FG, font=FONT)
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=BG_PANEL)
        style.configure("Status.TFrame", background="#111111")
        style.configure(
            "TNotebook",
            background=BG,
            borderwidth=0,
            tabmargins=(4, 4, 4, 0),
        )
        style.configure(
            "TNotebook.Tab",
            background="#333333",
            foreground=FG,
            padding=(18, 8),
            font=FONT_MD,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", ACCENT)],
            foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "Connect.TButton",
            font=FONT_MD,
            padding=(10, 6),
            background=ACCENT,
            foreground="#ffffff",
        )
        style.map("Connect.TButton", background=[("active", "#e01010")])

    def _build(self) -> None:
        self.status_bar = ttk.Frame(self, style="Status.TFrame", padding=(6, 4))
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        self.status_dot = tk.Canvas(
            self.status_bar, width=14, height=14, bg="#111111", highlightthickness=0
        )
        self.status_dot.pack(side=tk.LEFT, padx=(2, 6))
        self._dot_id = self.status_dot.create_oval(2, 2, 12, 12, fill=ERR, outline="")

        self.status_text = tk.Label(
            self.status_bar,
            text="Disconnected",
            bg="#111111",
            fg=FG,
            font=FONT_SM,
            anchor="w",
        )
        self.status_text.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.port_text = tk.Label(
            self.status_bar,
            text="COM: —",
            bg="#111111",
            fg=FG_DIM,
            font=FONT_SM,
            width=16,
            anchor="w",
        )
        self.port_text.pack(side=tk.LEFT, padx=(0, 6))

        self.connect_btn = ttk.Button(
            self.status_bar,
            text="Connect",
            style="Connect.TButton",
            command=self._on_connect_clicked,
            width=11,
        )
        self.connect_btn.pack(side=tk.RIGHT, padx=(4, 2), pady=2)

        notebook = ttk.Notebook(self)
        notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.notebook = notebook

        self.main_tab = ttk.Frame(notebook, padding=6)
        self.about_tab = ttk.Frame(notebook, padding=6)
        notebook.add(self.main_tab, text="Main")
        notebook.add(self.about_tab, text="About")

        self._build_main()
        self._build_about()

    def _build_main(self) -> None:
        header = tk.Label(
            self.main_tab,
            text="2015 Scrambler Icon 800",
            bg=BG,
            fg=FG,
            font=FONT_LG,
            anchor="w",
        )
        header.pack(fill=tk.X)

        sub = tk.Label(
            self.main_tab,
            text=ECU_TARGET,
            bg=BG,
            fg=FG_DIM,
            font=FONT_SM,
            anchor="w",
        )
        sub.pack(fill=tk.X, pady=(0, 4))

        vin_row = ttk.Frame(self.main_tab, style="Panel.TFrame")
        vin_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(
            vin_row, text="VIN", bg=BG_PANEL, fg=FG_DIM, font=FONT_SM, width=6, anchor="w"
        ).pack(side=tk.LEFT, padx=(6, 4), pady=6)
        self.vin_var = tk.StringVar(value="Connect to read VIN")
        tk.Label(
            vin_row,
            textvariable=self.vin_var,
            bg=BG_PANEL,
            fg=FG,
            font=FONT_MD,
            anchor="w",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, pady=6)

        tk.Label(
            self.main_tab,
            text="Active fault codes",
            bg=BG,
            fg=FG,
            font=FONT_MD,
            anchor="w",
        ).pack(fill=tk.X)

        list_frame = ttk.Frame(self.main_tab)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
        self.fault_list = tk.Listbox(
            list_frame,
            bg="#111111",
            fg=FG,
            selectbackground=ACCENT,
            font=FONT_SM,
            highlightthickness=0,
            borderwidth=0,
            activestyle="none",
        )
        scroll = ttk.Scrollbar(list_frame, command=self.fault_list.yview)
        self.fault_list.configure(yscrollcommand=scroll.set)
        self.fault_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._set_fault_placeholder("Not connected")

        self.note_var = tk.StringVar(value="")
        tk.Label(
            self.main_tab,
            textvariable=self.note_var,
            bg=BG,
            fg=WARN,
            font=FONT_SM,
            anchor="w",
            wraplength=460,
            justify="left",
        ).pack(fill=tk.X, pady=(2, 0))

    def _build_about(self) -> None:
        tk.Label(
            self.about_tab,
            text=f"{APP_NAME}  v{__version__}",
            bg=BG,
            fg=FG,
            font=FONT_LG,
            anchor="w",
        ).pack(fill=tk.X)
        tk.Label(
            self.about_tab,
            text=APP_TITLE,
            bg=BG,
            fg=FG_DIM,
            font=FONT,
            anchor="w",
        ).pack(fill=tk.X)
        tk.Label(
            self.about_tab,
            text=COPYRIGHT,
            bg=BG,
            fg=FG,
            font=FONT,
            anchor="w",
        ).pack(fill=tk.X, pady=(8, 0))
        tk.Label(
            self.about_tab,
            text=LICENSE_NAME,
            bg=BG,
            fg=ACCENT,
            font=FONT_MD,
            anchor="w",
        ).pack(fill=tk.X, pady=(2, 6))

        notice = tk.Text(
            self.about_tab,
            bg="#111111",
            fg=FG,
            font=FONT_SM,
            wrap="word",
            highlightthickness=0,
            borderwidth=0,
            height=10,
        )
        notice.pack(fill=tk.BOTH, expand=True)
        notice.insert("1.0", GPL_NOTICE.strip())
        notice.configure(state="disabled")

    def _scan_ports_idle(self) -> None:
        self._scan_after = None
        if self.session.connected or self._busy:
            return
        from raspberryduc.detect import list_candidates

        cands = list_candidates()
        if cands:
            self.port_text.configure(text=f"COM: {cands[0].device}")
        else:
            self.port_text.configure(text="COM: —")
        self._schedule_scan(2500)

    def _schedule_scan(self, delay_ms: int) -> None:
        if self._scan_after is not None:
            try:
                self.after_cancel(self._scan_after)
            except tk.TclError:
                pass
        self._scan_after = self.after(delay_ms, self._scan_ports_idle)

    def _on_connect_clicked(self) -> None:
        if self._busy:
            return
        if self.session.connected:
            self._run_bg(self._do_disconnect, after=self._on_disconnected)
        else:
            self._set_status("Connecting…", WARN)
            self.connect_btn.configure(state="disabled")
            self._run_bg(self._do_connect, after=self._on_connected)

    def _run_bg(self, work, after) -> None:
        self._busy = True

        def runner() -> None:
            result = None
            error: Optional[BaseException] = None
            try:
                result = work()
            except BaseException as exc:  # noqa: BLE001 — surface any worker failure
                error = exc
                LOG.exception("Background task failed")
            self.after(0, lambda: self._finish_bg(after, result, error))

        threading.Thread(target=runner, daemon=True).start()

    def _finish_bg(self, after, result, error) -> None:
        self._busy = False
        self.connect_btn.configure(state="normal")
        after(result, error)

    def _do_connect(self) -> ConnectionInfo:
        with self._lock:
            return self.session.connect()

    def _do_disconnect(self) -> None:
        with self._lock:
            self.session.disconnect()

    def _on_connected(self, info: Optional[ConnectionInfo], error: Optional[BaseException]) -> None:
        if error or info is None:
            msg = str(error) if error else "Connect failed"
            if isinstance(error, ElmError):
                msg = str(error)
            self._set_disconnected()
            self.note_var.set(msg)
            self._set_fault_placeholder("No data")
            return
        self._set_status("ELM327 connected", OK)
        self.port_text.configure(text=f"COM: {info.port}")
        self.connect_btn.configure(text="Disconnect")
        snap = self.session.snapshot
        if snap.vin:
            self.vin_var.set(snap.vin)
        else:
            self.vin_var.set("Unavailable")
        self.fault_list.delete(0, tk.END)
        if snap.faults:
            for fault in snap.faults:
                self.fault_list.insert(tk.END, fault.display())
        else:
            self._set_fault_placeholder("No active fault codes")
        self.note_var.set(snap.notes)

    def _on_disconnected(self, _result, error: Optional[BaseException]) -> None:
        self._set_disconnected()
        if error:
            self.note_var.set(str(error))

    def _set_disconnected(self) -> None:
        self._set_status("Disconnected", ERR)
        if not self.session.connected:
            self.connect_btn.configure(text="Connect")
        self.vin_var.set("Connect to read VIN")
        self._set_fault_placeholder("Not connected")
        self.note_var.set("")
        self._schedule_scan(200)

    def _set_status(self, text: str, color: str) -> None:
        self.status_text.configure(text=text)
        self.status_dot.itemconfigure(self._dot_id, fill=color)

    def _set_fault_placeholder(self, text: str) -> None:
        self.fault_list.delete(0, tk.END)
        self.fault_list.insert(tk.END, text)


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = RaspberryDucApp()
    app.mainloop()
