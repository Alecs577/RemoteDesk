"""Report Windows capabilities without attempting privilege escalation."""
import ctypes
import sys


def capability_summary() -> str:
    if sys.platform != "win32":
        return "Il controllo del desktop è disponibile su Windows."
    try:
        elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        elevated = False
    return ("Privilegi amministratore: " + ("sì" if elevated else "no") +
            ". Windows può limitare il controllo delle finestre elevate. "
            "Desktop UAC e Ctrl+Alt+Canc richiedono un servizio Windows dedicato; "
            "questa versione non dichiara di supportarli.")
