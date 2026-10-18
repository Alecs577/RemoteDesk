"""Regression: every settings category and product dialog must construct without callbacks failing."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


@unittest.skipUnless(sys.platform == "win32", "Windows desktop UI")
class SettingsUiTests(unittest.TestCase):
    def test_all_categories_and_product_dialogs(self):
        import tkinter as tk
        from remotedesk.config import AppConfig
        from remotedesk.grants import SessionGrants
        from remotedesk.settings import SettingsWindow
        from remotedesk.theme import apply_theme
        from remotedesk.product_ui import open_account_window, open_legal_window, open_update_window
        root = tk.Tk()
        root.withdraw()
        try:
            apply_theme(root)
            errors = []
            root.report_callback_exception = lambda *args: errors.append(str(args))
            host = SimpleNamespace(root=root, _config=AppConfig(desk_id="123456789"), _fingerprint="ab"*32,
                                   _alias_var=tk.StringVar(root, value="Prova"),
                                   _default_session_grants=lambda: SessionGrants())
            settings = SettingsWindow(host)
            settings.open()
            settings._win.withdraw()
            for category in settings.CATEGORY_BUILDERS:
                settings._category_var.set(category)
                settings._rebuild_panel()
                root.update_idletasks()
                self.assertTrue(settings._options_frame.winfo_children(), category)
            open_account_window(settings._win, host._config)
            open_legal_window(settings._win)
            with patch("remotedesk.updates.download_update", return_value=Path("test-update.exe")):
                open_update_window(settings._win)
                for child in settings._win.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        child.withdraw()
                root.after(250, root.quit)
                root.mainloop()
            self.assertEqual(errors, [])
        finally:
            root.destroy()
