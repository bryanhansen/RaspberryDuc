"""480x320 touchscreen GUI: Main / Realtime / About plus always-on status bar."""

from __future__ import annotations

import logging
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Dict, List, Optional

from raspberryduc.config import apply_logging, load_config
from raspberryduc.elm327 import ElmError
from raspberryduc.live import (
    LIVE_PARAMS,
    NONE_KEY,
    choices as live_choices,
    format_value,
)
from raspberryduc.protocol import ConnectionInfo, M3CSession
from raspberryduc.service import Indicator, OptionState, ServiceSnapshot
from raspberryduc.version import (
    APP_NAME,
    APP_TITLE,
    COPYRIGHT,
    ECU_TARGET,
    LICENSE_NAME,
    __version__,
    license_text,
)

LOG = logging.getLogger(__name__)

SCREEN_W = 480
SCREEN_H = 320
BG = "#1a1a1a"
BG_PANEL = "#2a2a2a"
FG = "#f2f2f2"
FG_DIM = "#c8c4bc"
ACCENT = "#c41212"
OK = "#3dcc6d"
WARN = "#e0a100"
ERR = "#e05555"
SELECT_BG = "#f2ede3"
SELECT_FG = "#1c1410"
SELECT_ARROW = "#b41c1c"
SELECT_HI = "#c41212"

FONT = ("DejaVu Sans", 10)
FONT_SM = ("DejaVu Sans", 9)
FONT_MD = ("DejaVu Sans", 11, "bold")
FONT_LG = ("DejaVu Sans", 12, "bold")
FONT_VALUE = ("DejaVu Sans", 16, "bold")

FREQ_OPTIONS = (("0.5 s", 0.5), ("1 s", 1.0), ("2 s", 2.0), ("5 s", 5.0))
DEFAULT_PANEL_KEYS = ("rpm", "coolant", "tps", "batt_v")


class TouchPicker(tk.Frame):
    """Fullscreen-safe chooser. Pi TFT leaves in-window Listbox overlays blank.

    Options are tk.Button rows in an overrideredirect Toplevel so X11 paints a
    real window. ``_block_until`` ignores the touch that closed the list.
    """

    _open_picker: Optional["TouchPicker"] = None

    def __init__(self, master, values, initial: str, on_change=None) -> None:
        super().__init__(master, bg=BG_PANEL)
        self._values = list(values)
        self._on_change = on_change
        self.var = tk.StringVar(value=initial)
        self._overlay: Optional[tk.Toplevel] = None
        self._block_until = 0.0
        shell = tk.Frame(
            self,
            bg=SELECT_BG,
            highlightbackground=SELECT_ARROW,
            highlightthickness=1,
        )
        shell.pack(fill=tk.BOTH, expand=True)
        tk.Button(
            shell,
            textvariable=self.var,
            command=self._open,
            bg=SELECT_BG,
            fg=SELECT_FG,
            activebackground=SELECT_BG,
            activeforeground=SELECT_FG,
            relief=tk.FLAT,
            bd=0,
            font=FONT,
            anchor="w",
            padx=8,
            pady=3,
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        arrow = tk.Label(
            shell,
            text="▾",
            bg=SELECT_ARROW,
            fg="#ffffff",
            font=FONT_MD,
            width=2,
        )
        arrow.pack(side=tk.RIGHT, fill=tk.Y)
        arrow.bind("<Button-1>", lambda _e: self._open())

    def get(self) -> str:
        return self.var.get()

    def _open(self) -> None:
        if time.monotonic() < self._block_until:
            return
        if self._overlay is not None:
            return
        if TouchPicker._open_picker is not None and TouchPicker._open_picker is not self:
            TouchPicker._open_picker._close()
        root = self.winfo_toplevel()
        root.update_idletasks()
        pop = tk.Toplevel(root)
        pop.transient(root)
        pop.overrideredirect(True)
        pop.configure(bg=SELECT_ARROW)
        x = root.winfo_rootx() + 20
        y = root.winfo_rooty() + 46
        pop.geometry(f"440x220+{x}+{y}")
        try:
            pop.attributes("-topmost", True)
        except tk.TclError:
            pass
        inner = tk.Frame(pop, bg=SELECT_BG)
        inner.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)
        canvas = tk.Canvas(inner, bg=SELECT_BG, highlightthickness=0, bd=0)
        vsb = tk.Scrollbar(
            inner,
            orient="vertical",
            command=canvas.yview,
            bg="#3a3a3a",
            troughcolor="#111111",
            activebackground=SELECT_HI,
            highlightthickness=0,
        )
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        holder = tk.Frame(canvas, bg=SELECT_BG)
        window = canvas.create_window((0, 0), window=holder, anchor="nw")

        def sync(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all") or (0, 0, 0, 0))
            canvas.itemconfigure(window, width=max(1, canvas.winfo_width()))

        holder.bind("<Configure>", sync)
        canvas.bind("<Configure>", sync)
        canvas.bind("<Button-4>", lambda _e: canvas.yview_scroll(-1, "units"))
        canvas.bind("<Button-5>", lambda _e: canvas.yview_scroll(1, "units"))
        current = self.var.get()
        for label in self._values:
            selected = label == current
            tk.Button(
                holder,
                text=label,
                command=lambda item=label: self._choose(item),
                bg=SELECT_HI if selected else SELECT_BG,
                fg="#ffffff" if selected else SELECT_FG,
                activebackground=SELECT_HI,
                activeforeground="#ffffff",
                disabledforeground=SELECT_FG,
                relief=tk.FLAT,
                bd=0,
                highlightthickness=1,
                highlightbackground="#d8cdb8",
                font=FONT,
                anchor="w",
                padx=12,
                pady=7,
            ).pack(fill=tk.X, pady=1, padx=2)
        self._overlay = pop
        TouchPicker._open_picker = self
        pop.bind("<Escape>", lambda _e: self._close())
        pop.update_idletasks()
        pop.lift()
        try:
            pop.focus_force()
        except tk.TclError:
            pass
        root.after_idle(lambda: self._raise_overlay())

    def _raise_overlay(self) -> None:
        pop = self._overlay
        if pop is None:
            return
        try:
            pop.lift()
            pop.update_idletasks()
        except tk.TclError:
            pass

    def _choose(self, label: str) -> None:
        self.var.set(label)
        if self._on_change:
            self._on_change()
        self._close()

    def _close(self) -> None:
        self._block_until = time.monotonic() + 0.45
        overlay = self._overlay
        self._overlay = None
        if TouchPicker._open_picker is self:
            TouchPicker._open_picker = None
        if overlay is None:
            return
        try:
            overlay.destroy()
        except tk.TclError:
            pass


class ValuePanel(ttk.Frame):
    def __init__(self, master, index: int, default_key: str) -> None:
        super().__init__(master, style="Panel.TFrame", padding=4)
        self.index = index
        self.value_var = tk.StringVar(value="—")
        self.units_var = tk.StringVar(value="")
        self._labels = {key: label for key, label in live_choices()}
        self._keys = {label: key for key, label in live_choices()}
        default_label = self._labels.get(default_key, "None")
        self.picker = TouchPicker(
            self,
            [label for _key, label in live_choices()],
            default_label,
            on_change=self._on_select,
        )
        self.picker.pack(fill=tk.X)

        value_row = ttk.Frame(self, style="Panel.TFrame")
        value_row.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        tk.Label(
            value_row,
            textvariable=self.value_var,
            bg=BG_PANEL,
            fg=FG,
            font=FONT_VALUE,
            anchor="e",
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Label(
            value_row,
            textvariable=self.units_var,
            bg=BG_PANEL,
            fg=FG_DIM,
            font=FONT_SM,
            anchor="w",
            width=5,
        ).pack(side=tk.LEFT, padx=(6, 0))
        self._on_select()

    def selected_key(self) -> str:
        return self._keys.get(self.picker.get(), NONE_KEY)

    def set_numeric(self, value: Optional[float]) -> None:
        key = self.selected_key()
        if key == NONE_KEY:
            self.value_var.set("—")
            return
        self.value_var.set(format_value(key, value))

    def _on_select(self, _event=None) -> None:
        key = self.selected_key()
        if key == NONE_KEY:
            self.units_var.set("")
            self.value_var.set("—")
            return
        self.units_var.set(LIVE_PARAMS[key].units)


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
        self._vehicle_ok = False
        self._rt_active = False
        self._rt_stop = threading.Event()
        self._rt_thread: Optional[threading.Thread] = None
        self._last_live: Dict[str, Optional[float]] = {}
        self._paint_pending = False

        self._apply_style()
        self._build()
        self._set_disconnected()
        self.bind("<Map>", self._on_mapped, add="+")
        self.bind("<FocusIn>", lambda _e: self._force_paint(), add="+")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed, add="+")
        self.after_idle(self._force_paint)
        self.after(120, self._force_paint)
        self.after(400, self._force_paint)

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
            tabmargins=(2, 2, 2, 0),
        )
        style.configure(
            "TNotebook.Tab",
            background="#333333",
            foreground=FG,
            padding=(6, 5),
            font=("DejaVu Sans", 9, "bold"),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", ACCENT)],
            foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "Connect.TButton",
            font=FONT_MD,
            padding=(8, 5),
            background=ACCENT,
            foreground="#ffffff",
        )
        style.map("Connect.TButton", background=[("active", "#e01010")])
        style.configure(
            "Live.TCombobox",
            font=FONT,
            fieldbackground=SELECT_BG,
            background=SELECT_ARROW,
            foreground=SELECT_FG,
            arrowcolor="#ffffff",
            arrowsize=18,
            padding=(6, 4),
            bordercolor="#d8cdb8",
            lightcolor=SELECT_BG,
            darkcolor="#8a7f70",
            insertcolor=SELECT_FG,
        )
        style.map(
            "Live.TCombobox",
            fieldbackground=[
                ("readonly", SELECT_BG),
                ("!disabled", SELECT_BG),
                ("disabled", "#9a958c"),
            ],
            foreground=[
                ("readonly", SELECT_FG),
                ("!disabled", SELECT_FG),
                ("disabled", "#4a453f"),
            ],
            background=[
                ("readonly", SELECT_ARROW),
                ("active", SELECT_HI),
                ("pressed", "#8e1212"),
            ],
            arrowcolor=[("readonly", "#ffffff"), ("!disabled", "#ffffff")],
            selectbackground=[("readonly", SELECT_HI)],
            selectforeground=[("readonly", "#ffffff")],
        )
        self.option_add("*TCombobox*Listbox.background", SELECT_BG)
        self.option_add("*TCombobox*Listbox.foreground", SELECT_FG)
        self.option_add("*TCombobox*Listbox.selectBackground", SELECT_HI)
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.option_add("*TCombobox*Listbox.font", FONT)
        self.option_add("*TCombobox*Listbox.relief", "flat")
        style.configure("Vertical.TScrollbar", background="#333333", troughcolor="#111111", width=14)

    def _style_combo_popdown(self, combo: ttk.Combobox) -> None:
        """Force light list contrast; Pi/clam often ignores TCombobox field colors."""

        def apply(_event=None) -> None:
            try:
                self.tk.eval(
                    f"""
                    set popdown [ttk::combobox::PopdownWindow {combo}]
                    $popdown.f.l configure -background {SELECT_BG} -foreground {SELECT_FG} \
                        -selectbackground {SELECT_HI} -selectforeground #ffffff \
                        -activestyle none
                    """
                )
            except tk.TclError:
                pass

        combo.bind("<ButtonPress-1>", apply, add="+")
        combo.after(150, apply)

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

        self.main_tab = ttk.Frame(notebook, padding=4)
        self.service_tab = ttk.Frame(notebook, padding=4)
        self.realtime_tab = ttk.Frame(notebook, padding=4)
        self.about_tab = ttk.Frame(notebook, padding=4)
        notebook.add(self.main_tab, text="Main")
        notebook.add(self.service_tab, text="Service")
        notebook.add(self.realtime_tab, text="Live")
        notebook.add(self.about_tab, text="About")

        self._build_main()
        self._build_service()
        self._build_realtime()
        self._build_about()

    def _on_mapped(self, _event=None) -> None:
        self._force_paint()

    def _on_tab_changed(self, _event=None) -> None:
        if TouchPicker._open_picker is not None:
            TouchPicker._open_picker._close()
        self._force_paint()
        self.after(80, self._force_paint)

    def _force_paint(self, _event=None) -> None:
        """Queue a geometry flush. Small TFTs often need an expose after tab/connect."""
        if self._paint_pending:
            return
        self._paint_pending = True
        self.after_idle(self._do_paint)

    def _do_paint(self) -> None:
        self._paint_pending = False
        try:
            self.update_idletasks()
            notebook = getattr(self, "notebook", None)
            if notebook is not None:
                notebook.update_idletasks()
                current = notebook.select()
                if current:
                    notebook.nametowidget(current).update_idletasks()
            self.update_idletasks()
        except tk.TclError:
            pass

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

    def _build_service(self) -> None:
        self.service_banner = tk.StringVar(value="Connect to read service data")
        tk.Label(
            self.service_tab,
            textvariable=self.service_banner,
            bg=BG,
            fg=FG_DIM,
            font=FONT_SM,
            anchor="w",
            wraplength=460,
            justify="left",
        ).pack(fill=tk.X, pady=(0, 4))

        grid = ttk.Frame(self.service_tab)
        grid.pack(fill=tk.BOTH, expand=True)
        for i in range(2):
            grid.columnconfigure(i, weight=1)
            grid.rowconfigure(i, weight=1)

        self._oil_dot, self.oil_status, self.oil_detail = self._service_card(
            grid, "Oil service", 0, 0
        )
        self._desmo_dot, self.desmo_status, self.desmo_detail = self._service_card(
            grid, "Desmo service", 0, 1
        )
        self._grips_dot, self.grips_status, self.grips_detail = self._service_card(
            grid, "Heated grips", 1, 0
        )
        self._interval_dot, self.interval_status, self.interval_detail = self._service_card(
            grid, "Service interval", 1, 1
        )
        self._reset_service_panel()

    def _service_card(self, grid, title: str, row: int, col: int):
        card = ttk.Frame(grid, style="Panel.TFrame", padding=5)
        card.grid(row=row, column=col, sticky="nsew", padx=3, pady=3)
        header = ttk.Frame(card, style="Panel.TFrame")
        header.pack(fill=tk.X)
        canvas = tk.Canvas(header, width=14, height=14, bg=BG_PANEL, highlightthickness=0)
        canvas.pack(side=tk.LEFT, padx=(0, 6))
        dot = canvas.create_oval(2, 2, 12, 12, fill=FG_DIM, outline="")
        tk.Label(
            header, text=title, bg=BG_PANEL, fg=FG_DIM, font=FONT_SM, anchor="w"
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        status = tk.StringVar(value="—")
        detail = tk.StringVar(value="")
        tk.Label(
            card, textvariable=status, bg=BG_PANEL, fg=FG, font=FONT_MD, anchor="w"
        ).pack(fill=tk.X, pady=(4, 0))
        tk.Label(
            card, textvariable=detail, bg=BG_PANEL, fg=FG_DIM, font=FONT_SM, anchor="w"
        ).pack(fill=tk.X)
        return (canvas, dot), status, detail

    def _build_realtime(self) -> None:
        grid = ttk.Frame(self.realtime_tab)
        grid.pack(fill=tk.BOTH, expand=True)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        grid.rowconfigure(0, weight=1)
        grid.rowconfigure(1, weight=1)

        self.panels: List[ValuePanel] = []
        for i, key in enumerate(DEFAULT_PANEL_KEYS):
            panel = ValuePanel(grid, i, key)
            row, col = divmod(i, 2)
            panel.grid(row=row, column=col, sticky="nsew", padx=3, pady=3)
            self.panels.append(panel)

        controls = ttk.Frame(self.realtime_tab)
        controls.pack(fill=tk.X, pady=(2, 0))

        tk.Label(controls, text="Rate", bg=BG, fg=FG_DIM, font=FONT_SM).pack(side=tk.LEFT)
        freq_wrap = tk.Frame(controls, bg=BG, width=92, height=28)
        freq_wrap.pack(side=tk.LEFT, padx=(4, 8))
        freq_wrap.pack_propagate(False)
        self.freq_picker = TouchPicker(
            freq_wrap,
            [label for label, _sec in FREQ_OPTIONS],
            "1 s",
        )
        self.freq_picker.pack(fill=tk.BOTH, expand=True)

        self.rt_status_var = tk.StringVar(value="Updates: Inactive")
        self.rt_status = tk.Label(
            controls,
            textvariable=self.rt_status_var,
            bg=BG,
            fg=FG_DIM,
            font=FONT_SM,
            anchor="w",
        )
        self.rt_status.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.rt_btn = ttk.Button(
            controls,
            text="Start",
            style="Connect.TButton",
            command=self._on_realtime_clicked,
            width=8,
        )
        self.rt_btn.pack(side=tk.RIGHT)
        self._set_realtime_controls()

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
        ).pack(fill=tk.X, pady=(4, 0))
        tk.Label(
            self.about_tab,
            text=LICENSE_NAME,
            bg=BG,
            fg=ACCENT,
            font=FONT_MD,
            anchor="w",
        ).pack(fill=tk.X, pady=(2, 4))

        text_frame = ttk.Frame(self.about_tab)
        text_frame.pack(fill=tk.BOTH, expand=True)
        notice = tk.Text(
            text_frame,
            bg="#111111",
            fg=FG,
            font=FONT_SM,
            wrap="word",
            highlightthickness=0,
            borderwidth=0,
            padx=4,
            pady=4,
        )
        scroll = ttk.Scrollbar(text_frame, command=notice.yview)
        notice.configure(yscrollcommand=scroll.set)
        notice.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        notice.insert("1.0", license_text())
        notice.configure(state="disabled")
        self._about_text = notice
        notice.bind("<MouseWheel>", self._on_about_wheel)
        notice.bind("<Button-4>", self._on_about_linux_scroll)
        notice.bind("<Button-5>", self._on_about_linux_scroll)

    def _on_about_wheel(self, event) -> str:
        steps = int(-event.delta / 120) if event.delta else 0
        if steps:
            self._about_text.yview_scroll(steps, "units")
        return "break"

    def _on_about_linux_scroll(self, event) -> str:
        self._about_text.yview_scroll(-1 if event.num == 4 else 1, "units")
        return "break"

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
            self._stop_realtime(keep_values=True)
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
        self._vehicle_ok = bool(snap.vin)
        if snap.vin:
            self.vin_var.set(snap.vin)
        else:
            self.vin_var.set("No vehicle detected")
        self.fault_list.delete(0, tk.END)
        if snap.faults:
            for fault in snap.faults:
                self.fault_list.insert(tk.END, fault.display())
        elif self._vehicle_ok:
            self._set_fault_placeholder("No active fault codes")
        else:
            self._set_fault_placeholder("No vehicle detected")
        self.note_var.set(snap.notes)
        self._set_realtime_controls()
        self._apply_service(snap.service, elm_connected=True)
        self._force_paint()
        self.after(80, self._force_paint)

    def _on_disconnected(self, _result, error: Optional[BaseException]) -> None:
        self._set_disconnected()
        if error:
            self.note_var.set(str(error))

    def _set_disconnected(self) -> None:
        self._stop_realtime(keep_values=True)
        self._vehicle_ok = False
        self._set_status("Disconnected", ERR)
        if not self.session.connected:
            self.connect_btn.configure(text="Connect")
        self.vin_var.set("Connect to read VIN")
        self._set_fault_placeholder("Not connected")
        self.note_var.set("")
        self._set_realtime_controls()
        self._reset_service_panel()
        self._schedule_scan(200)
        self._force_paint()

    def _set_status(self, text: str, color: str) -> None:
        self.status_text.configure(text=text)
        self.status_dot.itemconfigure(self._dot_id, fill=color)

    def _set_fault_placeholder(self, text: str) -> None:
        self.fault_list.delete(0, tk.END)
        self.fault_list.insert(tk.END, text)

    def _indicator_color(self, state: Indicator) -> str:
        if state == Indicator.OK:
            return OK
        if state == Indicator.DUE:
            return ERR
        return FG_DIM

    def _option_color(self, state: OptionState) -> str:
        if state == OptionState.ON:
            return OK
        if state == OptionState.OFF:
            return WARN
        return FG_DIM

    def _set_dot(self, handle, color: str) -> None:
        canvas, item = handle
        canvas.itemconfigure(item, fill=color)

    def _format_km(self, km: Optional[int]) -> str:
        if km is None:
            return "—"
        return f"{km:,} km".replace(",", " ")

    def _apply_service(self, snap: ServiceSnapshot, elm_connected: bool) -> None:
        if not elm_connected:
            self._reset_service_panel()
            return
        if not snap.vehicle_present:
            self.service_banner.set("No vehicle detected.")
            for status, detail in (
                (self.oil_status, self.oil_detail),
                (self.desmo_status, self.desmo_detail),
                (self.grips_status, self.grips_detail),
                (self.interval_status, self.interval_detail),
            ):
                status.set("—")
                detail.set("")
            for handle in (
                self._oil_dot,
                self._desmo_dot,
                self._grips_dot,
                self._interval_dot,
            ):
                self._set_dot(handle, FG_DIM)
            return
        banner = snap.source or "ECU service data"
        if snap.notes:
            banner = snap.notes
        self.service_banner.set(banner)
        self.oil_status.set(snap.oil.value)
        self.desmo_status.set(snap.desmo.value)
        self.grips_status.set(snap.grips.value)
        self._set_dot(self._oil_dot, self._indicator_color(snap.oil))
        self._set_dot(self._desmo_dot, self._indicator_color(snap.desmo))
        self._set_dot(self._grips_dot, self._option_color(snap.grips))
        self.oil_detail.set(
            self._format_km(snap.oil_remaining_km) + " left"
            if snap.oil_remaining_km is not None
            else ""
        )
        self.desmo_detail.set(
            self._format_km(snap.desmo_remaining_km) + " left"
            if snap.desmo_remaining_km is not None
            else ""
        )
        if snap.grips == OptionState.ON:
            self.grips_detail.set("ECU option enabled")
        elif snap.grips == OptionState.OFF:
            self.grips_detail.set("ECU option disabled")
        else:
            self.grips_detail.set("")
        if snap.interval_km is not None:
            self.interval_status.set(self._format_km(snap.interval_km))
            self.interval_detail.set("From ECU")
            self._set_dot(self._interval_dot, OK)
        else:
            self.interval_status.set("Unavailable")
            self.interval_detail.set("")
            self._set_dot(self._interval_dot, FG_DIM)

    def _reset_service_panel(self) -> None:
        if not hasattr(self, "service_banner"):
            return
        self.service_banner.set("Connect to read service data")
        for status, detail in (
            (self.oil_status, self.oil_detail),
            (self.desmo_status, self.desmo_detail),
            (self.grips_status, self.grips_detail),
            (self.interval_status, self.interval_detail),
        ):
            status.set("—")
            detail.set("")
        for handle in (
            self._oil_dot,
            self._desmo_dot,
            self._grips_dot,
            self._interval_dot,
        ):
            self._set_dot(handle, FG_DIM)

    def _period_seconds(self) -> float:
        label = self.freq_picker.get()
        for name, seconds in FREQ_OPTIONS:
            if name == label:
                return seconds
        return 1.0

    def _set_realtime_controls(self) -> None:
        if self._rt_active:
            self.rt_status_var.set("Updates: Active")
            self.rt_status.configure(fg=OK)
            self.rt_btn.configure(text="Stop", state="normal")
            return
        self.rt_status_var.set("Updates: Inactive")
        self.rt_status.configure(fg=FG_DIM)
        self.rt_btn.configure(text="Start")
        if self._vehicle_ok and self.session.connected:
            self.rt_btn.configure(state="normal")
        else:
            self.rt_btn.configure(state="disabled")

    def _on_realtime_clicked(self) -> None:
        if self._rt_active:
            self._stop_realtime(keep_values=True)
            return
        if not (self._vehicle_ok and self.session.connected):
            return
        self._start_realtime()

    def _start_realtime(self) -> None:
        self._rt_stop.clear()
        self._rt_active = True
        self._set_realtime_controls()
        self._rt_thread = threading.Thread(target=self._realtime_loop, daemon=True)
        self._rt_thread.start()

    def _stop_realtime(self, keep_values: bool) -> None:
        was_active = self._rt_active
        self._rt_active = False
        self._rt_stop.set()
        if keep_values:
            for panel in getattr(self, "panels", []):
                key = panel.selected_key()
                if key in self._last_live:
                    panel.set_numeric(self._last_live[key])
        if was_active:
            self._set_realtime_controls()

    def _realtime_loop(self) -> None:
        while not self._rt_stop.is_set():
            started = time.monotonic()
            keys = []
            for panel in self.panels:
                key = panel.selected_key()
                if key != NONE_KEY and key not in keys:
                    keys.append(key)
            results: Dict[str, Optional[float]] = {}
            try:
                # One ELM VCP: connect/disconnect and live polls must not overlap.
                with self._lock:
                    if not self.session.connected:
                        raise ElmError("disconnected")
                    for key in keys:
                        if self._rt_stop.is_set():
                            break
                        results[key] = self.session.read_live(key)
            except Exception:
                LOG.exception("Realtime update failed")
                self.after(0, self._on_realtime_error)
                return
            self.after(0, lambda r=results: self._apply_live(r))
            remaining = self._period_seconds() - (time.monotonic() - started)
            if remaining > 0:
                self._rt_stop.wait(remaining)

    def _apply_live(self, results: Dict[str, Optional[float]]) -> None:
        if not self._rt_active:
            return
        for key, value in results.items():
            if value is not None:
                self._last_live[key] = value
        for panel in self.panels:
            key = panel.selected_key()
            if key == NONE_KEY:
                panel.set_numeric(None)
                continue
            if key in results and results[key] is not None:
                panel.set_numeric(results[key])
            elif key in self._last_live:
                panel.set_numeric(self._last_live[key])

    def _on_realtime_error(self) -> None:
        self._stop_realtime(keep_values=True)
        self.rt_status_var.set("Updates: Error")
        self.rt_status.configure(fg=ERR)


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    apply_logging(load_config())
    app = RaspberryDucApp()
    app.mainloop()
