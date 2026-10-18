"""Finestra Impostazioni (italiano)."""

from __future__ import annotations

import sys
import tkinter as tk
import tkinter.messagebox as messagebox
from tkinter import filedialog, ttk
from typing import TYPE_CHECKING

from remotedesk import __version__
from remotedesk.config import parse_relay_host_port
from remotedesk.grants import GRANT_UI_LABELS, UNAVAILABLE_GRANTS
from remotedesk.identity import hash_access_password, valid_new_access_password
from remotedesk.theme import FIELD, PAPER, group_desk_id

if TYPE_CHECKING:
    from remotedesk.host import HostApp

QUALITY_CHOICES: tuple[tuple[str, str], ...] = (
    ("Fluido", "fluid"),
    ("Bilanciato", "balanced"),
    ("Nitido", "sharp"),
)
INVITE_MINUTES_MAP: dict[str, int] = {
    "1 minuto": 1,
    "2 minuti": 2,
    "5 minuti": 5,
    "10 minuti": 10,
}
INVITE_LABEL_FOR_MINUTES: dict[int, str] = {v: k for k, v in INVITE_MINUTES_MAP.items()}
TIMEOUT_SECONDS_MAP: dict[str, int] = {"30 secondi": 30, "1 minuto": 60, "2 minuti": 120}
VIEW_MODE_MAP: dict[str, str] = {"Adatta alla finestra": "fit", "Riempi finestra": "stretch"}
RECORD_INTERVAL_MAP: dict[str, int] = {
    "2 fotogrammi al secondo": 500,
    "4 fotogrammi al secondo": 250,
    "8 fotogrammi al secondo": 125,
}
CONFLICT_POLICY_MAP: dict[str, str] = {
    "Chiedi ogni volta": "ask",
    "Mantieni entrambi i file": "rename",
    "Sovrascrivi": "overwrite",
}


def apply_start_with_windows(enable: bool) -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                cmd = f'"{sys.executable}"' if getattr(sys, "frozen", False) else f'"{sys.executable}" -m remotedesk'
                winreg.SetValueEx(key, "RemoteDesk", 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, "RemoteDesk")
                except FileNotFoundError:
                    pass
    except OSError as exc:
        return str(exc)
    return None


class SettingsWindow:
    CATEGORY_BUILDERS = {
        "app_general": "_build_generale",
        "app_connection": "_build_connessione",
        "security_access": "_build_accesso",
        "security_permissions": "_build_permessi",
        "security_privacy": "_build_privacy",
        "session_display": "_build_schermo",
        "session_audio": "_build_audio",
        "session_files": "_build_file",
        "session_recording": "_build_registrazione",
        "information": "_build_informazioni",
    }

    def __init__(self, host: HostApp) -> None:
        self._host = host
        self._root = host.root
        self._config = host._config
        self._win: tk.Toplevel | None = None
        self._options_frame: ttk.Frame | None = None
        self._settings_canvas: tk.Canvas | None = None
        self._category_var = tk.StringVar(value="app_general")
        self._whitelist_working: list[str] = []
        self._default_grant_vars: dict[str, tk.BooleanVar] = {}

        self._start_windows_var = tk.BooleanVar(value=self._config.start_with_windows)
        self._show_banner_var = tk.BooleanVar(value=self._config.show_banner)
        self._interactive_var = tk.BooleanVar(value=self._config.interactive_access)
        self._confirm_files_var = tk.BooleanVar(value=self._config.confirm_incoming_files)
        self._show_cursor_var = tk.BooleanVar(value=self._config.show_remote_cursor)
        self._new_pwd_var = tk.StringVar()
        self._confirm_pwd_var = tk.StringVar()
        self._relay_var = tk.StringVar(value=self._config.relay)
        self._download_dir_var = tk.StringVar(value=self._config.download_dir)
        self._record_dir_var = tk.StringVar(value=self._config.record_dir)
        self._screenshot_dir_var = tk.StringVar(value=self._config.screenshot_dir)
        self._ask_download_path_var = tk.BooleanVar(value=self._config.ask_download_path)
        self._start_fullscreen_var = tk.BooleanVar(value=self._config.start_fullscreen)
        self._auto_record_var = tk.BooleanVar(value=self._config.auto_record_outgoing)

        q_label = "Bilanciato"
        for label, key in QUALITY_CHOICES:
            if key == self._config.default_quality:
                q_label = label
                break
        self._quality_var = tk.StringVar(value=q_label)

        invite_label = INVITE_LABEL_FOR_MINUTES.get(self._config.invite_minutes, "2 minuti")
        self._invite_var = tk.StringVar(value=invite_label)
        self._timeout_var = tk.StringVar(value="1 minuto")
        for label, seconds in TIMEOUT_SECONDS_MAP.items():
            if seconds == self._config.connection_timeout_seconds:
                self._timeout_var.set(label)
                break
        self._view_mode_var = tk.StringVar(value="Adatta alla finestra")
        for label, mode in VIEW_MODE_MAP.items():
            if mode == self._config.view_mode:
                self._view_mode_var.set(label)
                break
        self._record_interval_var = tk.StringVar(value="2 fotogrammi al secondo")
        for label, interval in RECORD_INTERVAL_MAP.items():
            if interval == self._config.record_interval_ms:
                self._record_interval_var.set(label)
                break
        self._conflict_var = tk.StringVar(value="Chiedi ogni volta")
        for label, policy in CONFLICT_POLICY_MAP.items():
            if policy == self._config.download_conflict:
                self._conflict_var.set(label)
                break

    def _sync_from_config(self) -> None:
        self._default_grant_vars = {}
        self._start_windows_var.set(self._config.start_with_windows)
        self._show_banner_var.set(self._config.show_banner)
        self._interactive_var.set(self._config.interactive_access)
        self._confirm_files_var.set(self._config.confirm_incoming_files)
        self._show_cursor_var.set(self._config.show_remote_cursor)
        self._relay_var.set(self._config.relay)
        self._download_dir_var.set(self._config.download_dir)
        self._record_dir_var.set(self._config.record_dir)
        self._screenshot_dir_var.set(self._config.screenshot_dir)
        self._ask_download_path_var.set(self._config.ask_download_path)
        self._start_fullscreen_var.set(self._config.start_fullscreen)
        self._auto_record_var.set(self._config.auto_record_outgoing)
        self._whitelist_working = list(self._config.whitelist)
        for label, key in QUALITY_CHOICES:
            if key == self._config.default_quality:
                self._quality_var.set(label)
                break
        self._invite_var.set(INVITE_LABEL_FOR_MINUTES.get(self._config.invite_minutes, "2 minuti"))
        self._timeout_var.set(next(
            (label for label, seconds in TIMEOUT_SECONDS_MAP.items()
             if seconds == self._config.connection_timeout_seconds),
            "1 minuto",
        ))
        self._view_mode_var.set(next(
            (label for label, mode in VIEW_MODE_MAP.items() if mode == self._config.view_mode),
            "Adatta alla finestra",
        ))
        self._record_interval_var.set(next(
            (label for label, value in RECORD_INTERVAL_MAP.items()
             if value == self._config.record_interval_ms),
            "2 fotogrammi al secondo",
        ))
        self._conflict_var.set(next(
            (label for label, policy in CONFLICT_POLICY_MAP.items()
             if policy == self._config.download_conflict),
            "Chiedi ogni volta",
        ))

    def open(self) -> None:
        if self._win is not None:
            try:
                if self._win.winfo_exists():
                    self._win.lift()
                    self._win.focus_force()
                    return
            except tk.TclError:
                pass
            self._win = None

        self._sync_from_config()
        win = tk.Toplevel(self._root)
        self._win = win
        win.title("Impostazioni")
        win.transient(self._root)
        screen_width = win.winfo_screenwidth()
        screen_height = win.winfo_screenheight()
        width = min(920, max(680, int(screen_width * 0.76)))
        height = min(740, max(480, int(screen_height * 0.78)))
        self._root.update_idletasks()
        x = max(0, self._root.winfo_rootx() + (self._root.winfo_width() - width) // 2)
        y = max(0, self._root.winfo_rooty() + (self._root.winfo_height() - height) // 2)
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.minsize(680, 480)
        win.resizable(True, True)

        outer = ttk.Frame(win, padding=16)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)

        heading = ttk.Frame(outer)
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(heading, text="Impostazioni", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(
            heading,
            text="Personalizza connessione, sicurezza e sessioni.",
            style="Muted.TLabel",
        ).pack(anchor=tk.W, pady=(2, 0))

        body = ttk.Frame(outer)
        body.grid(row=1, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        nav = ttk.Frame(body, style="Plate.TFrame", padding=10)
        nav.grid(row=0, column=0, sticky="ns", padx=(0, 14))
        ttk.Label(nav, text="Categorie", style="CardSection.TLabel").pack(anchor=tk.W, pady=(2, 8))
        tree = ttk.Treeview(nav, show="tree", selectmode="browse", height=15)
        tree.pack(fill=tk.Y, expand=True)
        application = tree.insert("", tk.END, text="Applicazione", open=True)
        tree.insert(application, tk.END, iid="app_general", text="Generale")
        tree.insert(application, tk.END, iid="app_connection", text="Connessione")
        security = tree.insert("", tk.END, text="Sicurezza", open=True)
        tree.insert(security, tk.END, iid="security_access", text="Accesso")
        tree.insert(security, tk.END, iid="security_permissions", text="Profili di permesso")
        tree.insert(security, tk.END, iid="security_privacy", text="Privacy")
        session = tree.insert("", tk.END, text="Sessione", open=True)
        tree.insert(session, tk.END, iid="session_display", text="Schermo")
        tree.insert(session, tk.END, iid="session_audio", text="Audio")
        tree.insert(session, tk.END, iid="session_files", text="File")
        tree.insert(session, tk.END, iid="session_recording", text="Registrazione")
        tree.insert("", tk.END, iid="information", text="Informazioni")

        panel = ttk.Frame(body, style="Plate.TFrame")
        panel.grid(row=0, column=1, sticky="nsew")
        panel.rowconfigure(0, weight=1)
        panel.columnconfigure(0, weight=1)
        self._settings_canvas = tk.Canvas(panel, highlightthickness=0, background=PAPER)
        scrollbar = ttk.Scrollbar(panel, orient=tk.VERTICAL, command=self._settings_canvas.yview)
        self._settings_canvas.configure(yscrollcommand=scrollbar.set)
        self._settings_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._options_frame = ttk.Frame(self._settings_canvas, style="Card.TFrame", padding=(20, 18, 20, 20))
        window_id = self._settings_canvas.create_window(
            (0, 0), window=self._options_frame, anchor=tk.NW
        )
        self._options_frame.bind(
            "<Configure>",
            lambda _e: self._settings_canvas.configure(scrollregion=self._settings_canvas.bbox("all")),
        )

        def resize_options(event: tk.Event) -> None:
            self._settings_canvas.itemconfigure(window_id, width=event.width)
            self._fit_settings_labels(event.width)

        self._settings_canvas.bind("<Configure>", resize_options)
        self._settings_canvas.bind(
            "<MouseWheel>",
            self._scroll_options,
        )

        def on_select(_evt: object = None) -> None:
            selected = tree.selection()
            if not selected:
                return
            item = selected[0]
            if item == application:
                item = "app_general"
            elif item == security:
                item = "security_access"
            elif item == session:
                item = "session_display"
            if item not in self.CATEGORY_BUILDERS:
                return
            self._category_var.set(item)
            self._rebuild_panel()

        tree.bind("<<TreeviewSelect>>", on_select)
        tree.selection_set("app_general")

        btn_row = ttk.Frame(outer)
        btn_row.grid(row=2, column=0, sticky="e", pady=(12, 0))
        ttk.Button(btn_row, text="Salva", style="Primary.TButton", command=self._save).pack(
            side=tk.RIGHT, padx=(8, 0)
        )
        ttk.Button(btn_row, text="Chiudi", style="Quiet.TButton", command=self._save_and_close).pack(side=tk.RIGHT)

        win.protocol("WM_DELETE_WINDOW", self._save_and_close)
        self._whitelist_working = list(self._config.whitelist)
        self._rebuild_panel()

    def _clear_options(self) -> None:
        if self._options_frame is None:
            return
        for child in self._options_frame.winfo_children():
            child.destroy()

    def _rebuild_panel(self) -> None:
        self._clear_options()
        assert self._options_frame is not None
        cat = self._category_var.get()
        build_name = self.CATEGORY_BUILDERS.get(cat)
        build = getattr(self, build_name) if build_name else None
        if build:
            build(self._options_frame)
            self._bind_options_mousewheel(self._options_frame)
        if self._settings_canvas is not None:
            self._fit_settings_labels(self._settings_canvas.winfo_width())
            self._settings_canvas.yview_moveto(0)

    def _fit_settings_labels(self, panel_width: int) -> None:
        if self._options_frame is None:
            return
        wrap_width = max(180, panel_width - 48)
        pending = list(self._options_frame.winfo_children())
        while pending:
            widget = pending.pop()
            if isinstance(widget, ttk.Label):
                default = getattr(widget, "_settings_wrap_default", None)
                if default is None:
                    default = int(widget.cget("wraplength") or 0)
                    widget._settings_wrap_default = default
                if default:
                    widget.configure(wraplength=min(default, wrap_width))
            pending.extend(widget.winfo_children())

    def _scroll_options(self, event: tk.Event) -> str:
        if self._settings_canvas is not None:
            steps = int(-event.delta / 120)
            if steps == 0 and event.delta:
                steps = -1 if event.delta > 0 else 1
            self._settings_canvas.yview_scroll(steps, "units")
        return "break"

    def _bind_options_mousewheel(self, widget: tk.Misc) -> None:
        widget.bind("<MouseWheel>", self._scroll_options, add="+")
        for child in widget.winfo_children():
            self._bind_options_mousewheel(child)

    def _build_generale(self, frame: ttk.Frame) -> None:
        ttk.Label(frame, text="Generale", style="CardTitle.TLabel").pack(
            anchor=tk.W, pady=(0, 4)
        )
        ttk.Label(
            frame,
            text="Identità e comportamento all'avvio dell'applicazione.",
            style="CardMuted.TLabel",
        ).pack(anchor=tk.W, pady=(0, 12))
        identity = ttk.LabelFrame(frame, text="Identità di questa scrivania", padding=10)
        identity.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(identity, text="Alias mostrato agli altri dispositivi", style="Card.TLabel").pack(anchor=tk.W)
        ttk.Entry(identity, textvariable=self._host._alias_var).pack(fill=tk.X, pady=(4, 0))

        startup = ttk.LabelFrame(frame, text="Avvio e notifiche", padding=10)
        startup.pack(fill=tk.X)
        ttk.Checkbutton(startup, text="Avvia RemoteDesk con Windows", variable=self._start_windows_var).pack(
            anchor=tk.W, pady=2
        )
        ttk.Label(
            startup,
            text="Aggiunge o rimuove la voce di avvio automatico per l'utente Windows corrente.",
            style="CardMuted.TLabel",
            wraplength=440,
        ).pack(anchor=tk.W, padx=(24, 0), pady=(0, 6))

    def _build_connessione(self, frame: ttk.Frame) -> None:
        ttk.Label(frame, text="Connessione", style="CardTitle.TLabel").pack(
            anchor=tk.W, pady=(0, 4)
        )
        ttk.Label(
            frame,
            text="Impostazioni usate per raggiungere il relay e attendere l'autorizzazione del PC remoto.",
            style="CardMuted.TLabel",
            wraplength=460,
        ).pack(anchor=tk.W, pady=(0, 12))
        relay = ttk.LabelFrame(frame, text="Server relay", padding=10)
        relay.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(relay, text="Indirizzo host:porta", style="Card.TLabel").pack(anchor=tk.W)
        ttk.Entry(relay, textvariable=self._relay_var).pack(fill=tk.X, pady=(4, 4))
        from remotedesk.distribution import CURRENT
        if CURRENT.relay:
            def use_default():
                self._relay_var.set(CURRENT.relay)
            ttk.Button(relay, text="Usa il server della distribuzione", command=use_default).pack(anchor=tk.W, pady=4)
        ttk.Label(
            relay,
            text="Il canale tra i due PC usa TLS; il relay inoltra il traffico cifrato.",
            style="CardMuted.TLabel",
            wraplength=440,
        ).pack(anchor=tk.W)
        invite = ttk.LabelFrame(frame, text="Inviti e attesa", padding=10)
        invite.pack(fill=tk.X)
        ttk.Label(invite, text="Scadenza dell'invito", style="Card.TLabel").pack(anchor=tk.W)
        ttk.Combobox(
            invite,
            textvariable=self._invite_var,
            values=list(INVITE_MINUTES_MAP.keys()),
            state="readonly",
            width=22,
        ).pack(anchor=tk.W, pady=(4, 10))
        ttk.Label(invite, text="Tempo massimo di attesa della risposta", style="Card.TLabel").pack(anchor=tk.W)
        ttk.Combobox(
            invite,
            textvariable=self._timeout_var,
            values=list(TIMEOUT_SECONDS_MAP.keys()),
            state="readonly",
            width=22,
        ).pack(anchor=tk.W, pady=(4, 0))

    def _build_accesso(self, frame: ttk.Frame) -> None:
        ttk.Label(frame, text="Accesso", style="CardTitle.TLabel").pack(
            anchor=tk.W, pady=(0, 4)
        )
        ttk.Label(
            frame,
            text="Decidi quando accettare le richieste e quali identità possono raggiungere questa scrivania.",
            style="CardMuted.TLabel",
            wraplength=460,
        ).pack(anchor=tk.W, pady=(0, 12))
        access = ttk.LabelFrame(frame, text="Richieste e password", padding=10)
        access.pack(fill=tk.X, pady=(0, 10))
        ttk.Checkbutton(
            access,
            text="Consenti richieste con conferma manuale",
            variable=self._interactive_var,
        ).pack(anchor=tk.W, pady=2)
        ttk.Label(access, text="Nuova password per l'accesso non presidiato", style="Card.TLabel").pack(
            anchor=tk.W, pady=(8, 0)
        )
        ttk.Entry(access, textvariable=self._new_pwd_var, show="*").pack(fill=tk.X, pady=(4, 8))
        ttk.Label(access, text="Ripeti la password", style="Card.TLabel").pack(anchor=tk.W)
        ttk.Entry(access, textvariable=self._confirm_pwd_var, show="*").pack(fill=tk.X, pady=(4, 4))
        ttk.Label(
            access,
            text="Lascia entrambi i campi vuoti per non cambiarla. L'accesso con password usa il profilo predefinito.",
            style="CardMuted.TLabel",
            wraplength=440,
        ).pack(anchor=tk.W, pady=(0, 8))

        def remove_password() -> None:
            self._config.access_password_hash = ""
            self._config.save()
            self._new_pwd_var.set("")
            self._confirm_pwd_var.set("")
            messagebox.showinfo("RemoteDesk", "Password di accesso rimossa.", parent=self._win)

        ttk.Button(access, text="Rimuovi password", style="Quiet.TButton", command=remove_password).pack(
            anchor=tk.W
        )

        allowlist = ttk.LabelFrame(frame, text="Lista di accesso consentito", padding=10)
        allowlist.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        ttk.Label(
            allowlist,
            text="Se la lista contiene ID, gli altri dispositivi vengono rifiutati. Gli inviti validi restano utilizzabili.",
            style="CardMuted.TLabel",
            wraplength=440,
        ).pack(anchor=tk.W, pady=(0, 6))
        wl_frame = ttk.Frame(allowlist)
        wl_frame.pack(fill=tk.BOTH, expand=True)
        wl_lb = tk.Listbox(wl_frame, height=5, highlightthickness=0, borderwidth=0)
        wl_lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(wl_frame, orient=tk.VERTICAL, command=wl_lb.yview)
        wl_lb.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        wl_id_var = tk.StringVar()

        def reload_wl() -> None:
            wl_lb.delete(0, tk.END)
            for kid in self._whitelist_working:
                wl_lb.insert(tk.END, group_desk_id(kid))

        def add_wl() -> None:
            kid = wl_id_var.get().strip().replace(" ", "")
            if len(kid) != 9 or not kid.isdigit():
                messagebox.showerror("RemoteDesk", "ID a 9 cifre non valido.", parent=self._win)
                return
            if kid not in self._whitelist_working:
                self._whitelist_working.append(kid)
            wl_id_var.set("")
            reload_wl()

        def remove_wl() -> None:
            sel = wl_lb.curselection()
            if sel:
                self._whitelist_working.pop(sel[0])
                reload_wl()

        reload_wl()
        row = ttk.Frame(allowlist)
        row.pack(fill=tk.X, pady=(6, 0))
        ttk.Entry(row, textvariable=wl_id_var, width=18).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(row, text="Aggiungi", style="Quiet.TButton", command=add_wl).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(row, text="Rimuovi", style="Quiet.TButton", command=remove_wl).pack(side=tk.LEFT)

        def forget_trusted() -> None:
            self._config.trusted.clear()
            self._config.save()
            messagebox.showinfo("RemoteDesk", "Impronte salvate rimosse.", parent=self._win)

        ttk.Button(
            frame,
            text="Dimentica le impronte dei dispositivi salvati",
            style="Quiet.TButton",
            command=forget_trusted,
        ).pack(anchor=tk.W)

    def _build_permessi(self, frame: ttk.Frame) -> None:
        ttk.Label(frame, text="Profilo predefinito", style="CardTitle.TLabel").pack(
            anchor=tk.W, pady=(0, 4)
        )
        ttk.Label(
            frame,
            text="Queste caselle vengono preselezionate nella richiesta di consenso. Sono applicate anche all'accesso con password; puoi modificarle per ogni invito.",
            style="CardMuted.TLabel",
            wraplength=460,
        ).pack(anchor=tk.W, pady=(0, 12))
        current = self._host._default_session_grants()
        self._default_grant_vars = {
            name: tk.BooleanVar(value=False if name in UNAVAILABLE_GRANTS else getattr(current, name))
            for name, _label in GRANT_UI_LABELS
        }
        groups = (
            ("Controllo e comunicazione", ("control", "chat", "clipboard", "clipboard_files", "files")),
            ("Sistema e privacy", ("restart", "privacy", "cad", "block_input", "lock", "disconnect_user", "sysinfo", "secure_windows")),
            ("Schermo e sessione", ("audio", "pointer", "draw", "tcp", "record")),
        )
        labels = dict(GRANT_UI_LABELS)
        for title, names in groups:
            group = ttk.LabelFrame(frame, text=title, padding=8)
            group.pack(fill=tk.X, pady=(0, 8))
            for name in names:
                checkbox = ttk.Checkbutton(
                    group,
                    text=labels[name],
                    variable=self._default_grant_vars[name],
                )
                checkbox.pack(anchor=tk.W, pady=1)
                if name in UNAVAILABLE_GRANTS:
                    checkbox.state(["disabled"])
        ttk.Label(
            frame,
            text="L'accesso interattivo richiede il consenso dell'host; l'accesso con password usa questo profilo. Audio, file tramite appunti, Ctrl+Alt+Canc e protezione delle finestre di sistema non sono disponibili in questa build.",
            style="CardMuted.TLabel",
            wraplength=460,
        ).pack(anchor=tk.W, pady=(4, 0))

    def _build_privacy(self, frame: ttk.Frame) -> None:
        ttk.Label(frame, text="Privacy", style="CardTitle.TLabel").pack(
            anchor=tk.W, pady=(0, 4)
        )
        ttk.Label(
            frame,
            text="Indicatori locali che rendono visibile quando lo schermo è condiviso.",
            style="CardMuted.TLabel",
            wraplength=460,
        ).pack(anchor=tk.W, pady=(0, 12))
        indicators = ttk.LabelFrame(frame, text="Durante la condivisione", padding=10)
        indicators.pack(fill=tk.X)
        ttk.Checkbutton(
            indicators,
            text="Mostra un avviso sempre visibile con il pulsante Interrompi",
            variable=self._show_banner_var,
        ).pack(anchor=tk.W, pady=2)
        ttk.Label(
            indicators,
            text="Disattivare l'avviso non interrompe né nasconde la sessione al client remoto.",
            style="CardMuted.TLabel",
            wraplength=440,
        ).pack(anchor=tk.W, padx=(24, 0), pady=(0, 4))

    def _build_schermo(self, frame: ttk.Frame) -> None:
        ttk.Label(frame, text="Schermo", style="CardTitle.TLabel").pack(
            anchor=tk.W, pady=(0, 4)
        )
        ttk.Label(
            frame,
            text="Preferenze usate all'avvio delle nuove sessioni in uscita.",
            style="CardMuted.TLabel",
        ).pack(anchor=tk.W, pady=(0, 12))
        quality = ttk.LabelFrame(frame, text="Immagine", padding=10)
        quality.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(quality, text="Qualità predefinita", style="Card.TLabel").pack(anchor=tk.W)
        ttk.Combobox(
            quality,
            textvariable=self._quality_var,
            values=[label for label, _ in QUALITY_CHOICES],
            state="readonly",
            width=24,
        ).pack(anchor=tk.W, pady=(4, 10))
        ttk.Label(quality, text="Adattamento del video", style="Card.TLabel").pack(anchor=tk.W)
        ttk.Combobox(
            quality,
            textvariable=self._view_mode_var,
            values=list(VIEW_MODE_MAP.keys()),
            state="readonly",
            width=24,
        ).pack(anchor=tk.W, pady=(4, 8))
        ttk.Checkbutton(
            quality,
            text="Mostra il puntatore di controllo sul video",
            variable=self._show_cursor_var,
        ).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(
            quality,
            text="Apri le nuove sessioni a schermo intero",
            variable=self._start_fullscreen_var,
        ).pack(anchor=tk.W, pady=2)

        screenshots = ttk.LabelFrame(frame, text="Schermate salvate", padding=10)
        screenshots.pack(fill=tk.X)
        ttk.Label(
            screenshots,
            text="Il pulsante Schermata nella barra della sessione salva il fotogramma ricevuto in JPEG.",
            style="CardMuted.TLabel",
            wraplength=440,
        ).pack(anchor=tk.W, pady=(0, 6))
        row = ttk.Frame(screenshots)
        row.pack(fill=tk.X)
        ttk.Entry(row, textvariable=self._screenshot_dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        def browse_screenshots() -> None:
            path = filedialog.askdirectory(title="Cartella schermate", parent=self._win)
            if path:
                self._screenshot_dir_var.set(path)

        ttk.Button(row, text="Sfoglia…", style="Quiet.TButton", command=browse_screenshots).pack(side=tk.RIGHT)

    def _build_audio(self, frame: ttk.Frame) -> None:
        ttk.Label(frame, text="Audio", style="CardTitle.TLabel").pack(
            anchor=tk.W, pady=(0, 4)
        )
        ttk.Label(
            frame,
            text="L'acquisizione dell'uscita audio di sistema non è ancora disponibile in questa build. Il permesso Audio resta disattivato nel profilo predefinito.",
            style="CardMuted.TLabel",
            wraplength=460,
        ).pack(anchor=tk.W, pady=(0, 12))
        for title, description in (
            ("Trasmissione", "La sessione non invia audio dal PC remoto."),
            ("Riproduzione", "La sessione non riproduce ancora audio dal dispositivo remoto."),
            ("Dispositivi", "La selezione di dispositivi audio verrà mostrata quando sarà implementata la cattura audio."),
        ):
            group = ttk.LabelFrame(frame, text=title, padding=10)
            group.pack(fill=tk.X, pady=(0, 8))
            ttk.Label(group, text=description, style="CardMuted.TLabel", wraplength=440).pack(anchor=tk.W)

    def _build_file(self, frame: ttk.Frame) -> None:
        ttk.Label(frame, text="Trasferimento file", style="CardTitle.TLabel").pack(
            anchor=tk.W, pady=(0, 4)
        )
        ttk.Label(
            frame,
            text="Configura l'arrivo dei file e il comportamento quando il nome è già presente.",
            style="CardMuted.TLabel",
        ).pack(anchor=tk.W, pady=(0, 12))
        receive = ttk.LabelFrame(frame, text="Ricezione", padding=10)
        receive.pack(fill=tk.X, pady=(0, 10))
        ttk.Checkbutton(
            receive,
            text="Chiedi conferma prima di accettare un trasferimento",
            variable=self._confirm_files_var,
        ).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(
            receive,
            text="Chiedi una cartella di destinazione per ogni file",
            variable=self._ask_download_path_var,
        ).pack(anchor=tk.W, pady=2)
        ttk.Label(receive, text="Cartella predefinita", style="Card.TLabel").pack(anchor=tk.W, pady=(8, 0))
        ttk.Label(
            receive,
            text="Vuota = Download/RemoteDesk.",
            style="CardMuted.TLabel",
        ).pack(anchor=tk.W, pady=(0, 4))
        row = ttk.Frame(receive)
        row.pack(fill=tk.X)
        ttk.Entry(row, textvariable=self._download_dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        def browse() -> None:
            path = filedialog.askdirectory(title="Cartella di download", parent=self._win)
            if path:
                self._download_dir_var.set(path)

        ttk.Button(row, text="Sfoglia…", style="Quiet.TButton", command=browse).pack(side=tk.RIGHT)

        conflicts = ttk.LabelFrame(frame, text="File con lo stesso nome", padding=10)
        conflicts.pack(fill=tk.X)
        ttk.Label(conflicts, text="Comportamento predefinito", style="Card.TLabel").pack(anchor=tk.W)
        ttk.Combobox(
            conflicts,
            textvariable=self._conflict_var,
            values=list(CONFLICT_POLICY_MAP.keys()),
            state="readonly",
            width=28,
        ).pack(anchor=tk.W, pady=(4, 6))
        ttk.Label(
            conflicts,
            text="La scelta predefinita chiede prima di sovrascrivere; puoi conservare entrambi i file con un nome distinto.",
            style="CardMuted.TLabel",
            wraplength=440,
        ).pack(anchor=tk.W)

    def _build_registrazione(self, frame: ttk.Frame) -> None:
        ttk.Label(frame, text="Registrazione", style="CardTitle.TLabel").pack(
            anchor=tk.W, pady=(0, 4)
        )
        ttk.Label(
            frame,
            text="La registrazione locale salva una sequenza di immagini JPEG delle sessioni in uscita. Parte solo se il PC remoto concede il permesso Registrazione.",
            style="CardMuted.TLabel",
            wraplength=460,
        ).pack(anchor=tk.W, pady=(0, 12))
        capture = ttk.LabelFrame(frame, text="Sessioni in uscita", padding=10)
        capture.pack(fill=tk.X, pady=(0, 10))
        ttk.Checkbutton(
            capture,
            text="Avvia la registrazione automaticamente quando è autorizzata",
            variable=self._auto_record_var,
        ).pack(anchor=tk.W, pady=2)
        ttk.Label(capture, text="Frequenza massima di salvataggio", style="Card.TLabel").pack(
            anchor=tk.W, pady=(8, 0)
        )
        ttk.Combobox(
            capture,
            textvariable=self._record_interval_var,
            values=list(RECORD_INTERVAL_MAP.keys()),
            state="readonly",
            width=28,
        ).pack(anchor=tk.W, pady=(4, 0))
        location = ttk.LabelFrame(frame, text="Posizione", padding=10)
        location.pack(fill=tk.X)
        ttk.Label(
            location,
            text="Vuota = Download/RemoteDesk/registrazioni. Ogni sessione crea una cartella con data e ora.",
            style="CardMuted.TLabel",
            wraplength=440,
        ).pack(anchor=tk.W, pady=(0, 4))
        row = ttk.Frame(location)
        row.pack(fill=tk.X)
        ttk.Entry(row, textvariable=self._record_dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        def browse() -> None:
            path = filedialog.askdirectory(title="Cartella registrazioni", parent=self._win)
            if path:
                self._record_dir_var.set(path)

        ttk.Button(row, text="Sfoglia…", style="Quiet.TButton", command=browse).pack(side=tk.RIGHT)

    def _build_informazioni(self, frame: ttk.Frame) -> None:
        ttk.Label(frame, text="Informazioni", style="CardTitle.TLabel").pack(
            anchor=tk.W, pady=(0, 12)
        )
        ttk.Label(frame, text=f"Versione {__version__}", style="Card.TLabel").pack(anchor=tk.W, pady=2)
        from remotedesk.product_ui import open_account_window, open_update_window, open_legal_window
        ttk.Button(frame, text="Account e backup identità", command=lambda: open_account_window(
            self._win, self._config)).pack(anchor=tk.W, pady=6)
        ttk.Button(frame, text="Scarica aggiornamento verificato", command=lambda: open_update_window(
            self._win)).pack(anchor=tk.W, pady=6)
        ttk.Button(frame, text="Condizioni, privacy e licenze", command=lambda: open_legal_window(
            self._win)).pack(anchor=tk.W, pady=6)
        from remotedesk.capabilities import capability_summary
        ttk.Label(frame, text=capability_summary(), style="CardMuted.TLabel",
                  wraplength=480).pack(anchor=tk.W, pady=6)
        ttk.Label(
            frame,
            text=(
                f"ID {group_desk_id(self._config.desk_id)}"
                if self._config.identity_provisioned
                else "ID in assegnazione dal relay"
            ),
            style="Card.TLabel",
        ).pack(anchor=tk.W, pady=2)
        ttk.Label(
            frame,
            text=f"Impronta {self._host._fingerprint}",
            style="CardMono.TLabel",
            wraplength=480,
        ).pack(anchor=tk.W, pady=2)
        ttk.Label(
            frame,
            text="Le sessioni sono cifrate fra i due PC. Il relay non legge il contenuto.",
            style="CardMuted.TLabel",
            wraplength=480,
        ).pack(anchor=tk.W, pady=(12, 0))

    def _quality_key(self) -> str:
        label = self._quality_var.get()
        for ui_label, key in QUALITY_CHOICES:
            if ui_label == label:
                return key
        return "balanced"

    def _save(self, *, close: bool = False) -> bool:
        relay = self._relay_var.get().strip()
        from remotedesk.distribution import CURRENT
        relay = relay or CURRENT.relay
        if relay and parse_relay_host_port(relay) is None:
            messagebox.showerror("RemoteDesk", "Indirizzo relay non valido (usa host:porta).")
            return False

        new_pwd = self._new_pwd_var.get()
        confirm_pwd = self._confirm_pwd_var.get()
        if new_pwd or confirm_pwd:
            if new_pwd != confirm_pwd:
                messagebox.showerror("RemoteDesk", "Le password non coincidono.")
                return False
            if not valid_new_access_password(new_pwd):
                messagebox.showerror("RemoteDesk", "Usa una password di almeno 12 caratteri (massimo 128 byte).")
                return False
            self._config.access_password_hash = hash_access_password(new_pwd)
            self._new_pwd_var.set("")
            self._confirm_pwd_var.set("")

        alias = self._host._alias_var.get().strip()
        self._config.alias = alias
        self._host._alias_heading.configure(text=alias or "Questa scrivania")

        relay_changed = relay != self._config.relay
        self._config.relay = relay
        self._config.start_with_windows = self._start_windows_var.get()
        prev_banner = self._config.show_banner
        self._config.show_banner = self._show_banner_var.get()
        self._config.interactive_access = self._interactive_var.get()
        self._config.confirm_incoming_files = self._confirm_files_var.get()
        self._config.default_quality = self._quality_key()
        self._config.show_remote_cursor = self._show_cursor_var.get()
        if self._default_grant_vars:
            self._config.default_grants = {
                name: bool(var.get()) for name, var in self._default_grant_vars.items()
            }
        self._config.transmit_audio = self._config.default_grants.get("audio", False)
        invite_label = self._invite_var.get()
        self._config.invite_minutes = INVITE_MINUTES_MAP.get(invite_label, 2)
        self._config.download_dir = self._download_dir_var.get().strip()
        self._config.record_dir = self._record_dir_var.get().strip()
        self._config.screenshot_dir = self._screenshot_dir_var.get().strip()
        self._config.view_mode = VIEW_MODE_MAP.get(self._view_mode_var.get(), "fit")
        self._config.start_fullscreen = self._start_fullscreen_var.get()
        self._config.auto_record_outgoing = self._auto_record_var.get()
        self._config.record_interval_ms = RECORD_INTERVAL_MAP.get(self._record_interval_var.get(), 500)
        self._config.ask_download_path = self._ask_download_path_var.get()
        self._config.download_conflict = CONFLICT_POLICY_MAP.get(self._conflict_var.get(), "ask")
        self._config.connection_timeout_seconds = TIMEOUT_SECONDS_MAP.get(self._timeout_var.get(), 60)
        self._config.whitelist = list(self._whitelist_working)

        self._config.save()

        err = apply_start_with_windows(self._config.start_with_windows)
        if err:
            messagebox.showerror("RemoteDesk", f"Impossibile aggiornare l'avvio con Windows:\n{err}")

        self._host._state.set_quality(self._config.default_quality)
        if self._host._connect_panel is not None:
            self._host._connect_panel.apply_display_settings(
                quality=self._config.default_quality,
                show_cursor=self._config.show_remote_cursor,
                view_mode=self._config.view_mode,
                start_fullscreen=self._config.start_fullscreen,
                auto_record=self._config.auto_record_outgoing,
                record_interval_ms=self._config.record_interval_ms,
            )

        if prev_banner and not self._config.show_banner:
            self._host._banner.hide()
        elif self._config.show_banner and self._host._state.sharing:
            self._host._maybe_show_banner()

        if relay_changed:
            self._host._host_relay.reconnect()

        if close and self._win is not None:
            try:
                self._win.destroy()
            except tk.TclError:
                pass
            self._win = None
        return True

    def _save_and_close(self) -> None:
        if self._save(close=True):
            return
        # invalid save: keep window open
