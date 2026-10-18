"""Small account and update screens with background I/O and UI-thread delivery."""
from __future__ import annotations

import base64
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from pathlib import Path

from remotedesk.account_client import AccountClient
from remotedesk.config import AppConfig
from remotedesk.identity import _atomic_write
from remotedesk.recovery import export_identity


def _background(window, function, callback):
    result = queue.Queue()
    def work():
        try:
            result.put((True, function()))
        except Exception as exc:
            result.put((False, str(exc) or "Verifica crittografica non riuscita."))
    threading.Thread(target=work, daemon=True).start()
    def poll():
        if not window.winfo_exists():
            return
        try:
            ok, value = result.get_nowait()
        except queue.Empty:
            window.after(100, poll)
            return
        callback(ok, value)
    window.after(100, poll)


def open_update_window(parent):
    from remotedesk.updates import download_update
    win = tk.Toplevel(parent)
    win.title("Aggiornamento RemoteDesk")
    win.minsize(400, 160)
    label = ttk.Label(win, text="Verifica e download in corso…", wraplength=480, padding=20)
    label.pack(fill=tk.BOTH, expand=True)
    def done(ok, value):
        label.configure(text=(f"Aggiornamento verificato salvato in:\n{value}\n\n"
                              "Chiudi RemoteDesk e avvia il file scaricato per usare la nuova versione.")
                        if ok else str(value))
    _background(win, download_update, done)


def open_account_window(parent, config: AppConfig):
    client = AccountClient()
    win = tk.Toplevel(parent)
    win.title("Account e backup identità")
    win.minsize(400, 380)
    win.geometry("520x640")
    canvas = tk.Canvas(win, highlightthickness=0)
    scrollbar = ttk.Scrollbar(win, orient=tk.VERTICAL, command=canvas.yview)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(fill=tk.BOTH, expand=True)
    frame = ttk.Frame(canvas, padding=20)
    content = canvas.create_window((0, 0), window=frame, anchor="nw")
    frame.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
    def resize(event):
        canvas.itemconfigure(content, width=event.width)
        for widget in frame.winfo_children():
            if isinstance(widget, ttk.Label) and int(widget.cget("wraplength") or 0):
                widget.configure(wraplength=max(180, event.width-40))
    canvas.bind("<Configure>", resize)
    win.bind("<MouseWheel>", lambda event: canvas.yview_scroll(-int(event.delta/120), "units"))
    ttk.Label(frame, text="Account RemoteDesk", style="CardTitle.TLabel").pack(anchor=tk.W)
    ttk.Label(frame, text="Il backup è cifrato con una passphrase separata. Conservala: "
              "il servizio non può recuperarla.", wraplength=440).pack(anchor=tk.W, pady=10)
    fields = {}
    for name, label, hidden in (("username", "Username", False), ("password", "Password account", True),
                                ("device", "Nome dispositivo", False)):
        ttk.Label(frame, text=label).pack(anchor=tk.W)
        var = tk.StringVar(value=config.desk_id if name == "device" else "")
        ttk.Entry(frame, textvariable=var, show="•" if hidden else "").pack(fill=tk.X, pady=(2,8))
        fields[name] = var
    status = tk.StringVar(value="Non connesso")
    ttk.Label(frame, textvariable=status, wraplength=440).pack(anchor=tk.W, pady=8)
    buttons = []
    def run(function, on_success=None):
        for button in buttons:
            button.configure(state=tk.DISABLED)
        status.set("Operazione in corso…")
        def done(ok, value):
            for button in buttons:
                button.configure(state=tk.NORMAL)
            status.set("Operazione completata." if ok else str(value))
            if ok and on_success:
                on_success(value)
        _background(win, function, done)
    def credentials():
        return fields["username"].get(), fields["password"].get()
    def show_recovery(result):
        code = result["recovery_code"]
        dialog = tk.Toplevel(win)
        dialog.title("Conserva il codice di recupero account")
        ttk.Label(dialog, text="Mostrato una sola volta. Consente di reimpostare la password account.\n"
                  "Non sostituisce la passphrase del backup.", padding=15).pack()
        text = tk.Text(dialog, height=2, width=55, wrap=tk.WORD)
        text.insert("1.0", code)
        text.configure(state=tk.DISABLED)
        text.pack(padx=15, pady=10)
    def register():
        user, password = credentials()
        invitation = simpledialog.askstring("Registrazione", "Codice di invito del servizio:", show="•", parent=win)
        if invitation:
            run(lambda: client.request("register", username=user, password=password,
                                        registration_code=invitation), show_recovery)
    def login():
        user, password = credentials()
        run(lambda: client.request("login", username=user, password=password),
            lambda _: (fields["password"].set(""), status.set("Accesso effettuato per un'ora.")))
    def backup(cloud):
        if cloud and not client.token:
            messagebox.showinfo("RemoteDesk", "Accedi prima all'account.", parent=win)
            return
        password = simpledialog.askstring("Backup", "Nuova passphrase backup (almeno 16 caratteri):", show="•", parent=win)
        if not password:
            return
        confirm = simpledialog.askstring("Backup", "Ripeti la passphrase backup:", show="•", parent=win)
        if password != confirm:
            messagebox.showerror("RemoteDesk", "Le passphrase non coincidono.", parent=win)
            return
        device = fields["device"].get()
        path = None if cloud else filedialog.asksaveasfilename(parent=win, defaultextension=".rdbackup",
                                                       filetypes=[("Backup RemoteDesk", "*.rdbackup")])
        if not cloud and not path:
            return
        def work():
            blob = export_identity(config, password)
            if cloud:
                return client.request("upload", device=device, blob=base64.b64encode(blob).decode("ascii"))
            _atomic_write(Path(path), blob)
        run(work)
    def download():
        device = fields["device"].get()
        path = filedialog.asksaveasfilename(parent=win, defaultextension=".rdbackup")
        if not path:
            return
        def work():
            result = client.request("download", device=device)
            _atomic_write(Path(path), base64.b64decode(result["blob"], validate=True))
        run(work, lambda _: messagebox.showinfo("RemoteDesk", "Backup cifrato salvato. Sul PC di destinazione "
             "usa il comando 'identity restore' prima del primo avvio dell'app.", parent=win))
    def reset():
        user, password = credentials()
        code = simpledialog.askstring("Recupero account", "Codice di recupero account:", show="•", parent=win)
        if code:
            run(lambda: client.request("reset", username=user, password=password, recovery_code=code), show_recovery)
    def devices():
        run(lambda: client.request("devices"), lambda result: messagebox.showinfo("Backup dispositivi",
             "\n".join(item["device"] for item in result["devices"]) or "Nessun backup salvato.", parent=win))
    def delete_device():
        device = fields["device"].get()
        if messagebox.askyesno("Elimina backup", f"Eliminare il backup cifrato di {device} dall'account?", parent=win):
            run(lambda: client.request("delete-device", device=device))
    def delete_account():
        if messagebox.askyesno("Elimina account", "Eliminare definitivamente account, sessioni e backup sul servizio? "
                              "L'identità del PC rimane nella directory relay.", parent=win):
            run(lambda: client.request("delete-account"))
    for label, action in (("Crea account", register), ("Accedi", login), ("Recupera account", reset),
                          ("Esporta backup locale", lambda: backup(False)),
                          ("Salva backup nell'account", lambda: backup(True)),
                          ("Elenca backup dispositivi", devices),
                          ("Scarica backup dall'account", download),
                          ("Elimina backup dispositivo", delete_device),
                          ("Disconnetti account", lambda: run(lambda: client.request("logout"))),
                          ("Elimina account e backup", delete_account)):
        button = ttk.Button(frame, text=label, command=action)
        button.pack(fill=tk.X, pady=3)
        buttons.append(button)
    ttk.Label(frame, text="Ogni PC attivo deve avere un'identità distinta. Ripristina un ID "
              "solo per sostituire un dispositivo dismesso.", wraplength=440).pack(pady=8)


def open_legal_window(parent):
    import sys
    from tkinter import scrolledtext
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    win = tk.Toplevel(parent)
    win.title("Condizioni, privacy e licenze")
    win.geometry("720x560")
    notebook = ttk.Notebook(win)
    notebook.pack(fill=tk.BOTH, expand=True)
    for title, path in (("Condizioni", root / "legal/terms.md"),
                        ("Privacy", root / "legal/privacy.md"),
                        ("Componenti di terzi", root / "notices/THIRD_PARTY_NOTICES.txt")):
        frame = ttk.Frame(notebook)
        notebook.add(frame, text=title)
        text = scrolledtext.ScrolledText(frame, wrap=tk.WORD)
        text.pack(fill=tk.BOTH, expand=True)
        if not path.is_file() and title == "Componenti di terzi":
            path = root / "build/notices/THIRD_PARTY_NOTICES.txt"
        text.insert("1.0", path.read_text("utf-8") if path.is_file() else "Documento disponibile nella distribuzione compilata.")
        text.configure(state=tk.DISABLED)
