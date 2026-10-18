"""Avvio: python -m remotedesk  |  python -m remotedesk relay"""

from __future__ import annotations

import sys


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "diagnostics"):
        from remotedesk import __version__
        if sys.argv[1] == "--version":
            print(__version__)
        else:
            import json
            from remotedesk.distribution import CURRENT, PUBLIC
            from remotedesk.capabilities import capability_summary
            print(json.dumps({"version": __version__, "public_release": PUBLIC,
                              "default_relay": CURRENT.relay, "accounts_url": CURRENT.accounts_url,
                              "updates_configured": bool(CURRENT.updates_url and CURRENT.update_public_key),
                              "capabilities": capability_summary()}, indent=2, ensure_ascii=False))
        return
    if len(sys.argv) > 1 and sys.argv[1] == "identity":
        from remotedesk.maintenance import main as identity_main
        identity_main(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "accounts":
        from remotedesk.account_server import main as accounts_main
        accounts_main(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "relay":
        from remotedesk.relay import main as relay_main

        relay_main(sys.argv[2:])
        return

    from remotedesk.profile_lock import profile_lock
    with profile_lock() as directory:
        if (directory / "restore-in-progress").exists():
            raise RuntimeError("Ripristino incompleto: controllare il profilo RemoteDesk prima dell'avvio.")
        _run_client()


def _run_client() -> None:
    from remotedesk.capture import CaptureThread, enable_per_monitor_dpi
    from remotedesk.host import HostApp

    enable_per_monitor_dpi()
    from remotedesk.state import ShareState

    state = ShareState()
    capture = CaptureThread(state)
    capture.start()

    def on_sharing_changed(on: bool) -> None:
        if not on:
            state.set_sharing(False)

    try:
        app = HostApp(state, on_sharing_changed)
        app.run()
    finally:
        state.set_sharing(False)
        capture.stop()


if __name__ == "__main__":
    main()
