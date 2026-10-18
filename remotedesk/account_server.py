"""Bounded HTTPS account API. Deploy directly with TLS, not behind a TLS terminator."""
from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from remotedesk.accounts import AccountError, Accounts
from remotedesk.database import Database

MAX_BODY = 192 * 1024
logger = logging.getLogger(__name__)


def handler_for(accounts: Accounts, registration: str):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(20)
            self.request.do_handshake()
            super().setup()

        def log_message(self, format, *args):
            # Never log usernames, passwords, tokens, backup contents or registration keys.
            pass

        def reply(self, status, payload):
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Strict-Transport-Security", "max-age=31536000")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            try:
                if (self.headers.get("Origin") or self.headers.get("Transfer-Encoding") or
                        len(self.headers.get_all("Content-Length", [])) != 1 or
                        self.headers.get("Content-Type", "") != "application/json"):
                    raise AccountError("Richiesta non valida.")
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= MAX_BODY:
                    raise AccountError("Dimensione richiesta non valida.")
                accounts.limit("ip:" + self.client_address[0], 60)
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise AccountError("Richiesta non valida.")
                if self.path == "/v1/register":
                    import hmac
                    accounts.limit("register-ip:" + self.client_address[0], 5, 3600)
                    if not registration or not hmac.compare_digest(str(data.get("registration_code", "")), registration):
                        raise AccountError("Registrazione su invito: codice non valido.")
                    result = accounts.register(data.get("username"), data.get("password"))
                elif self.path == "/v1/login":
                    result = accounts.login(data.get("username"), data.get("password"))
                elif self.path == "/v1/reset":
                    result = accounts.reset(data.get("username"), data.get("recovery_code"), data.get("password"))
                elif self.path.startswith("/v1/"):
                    header = self.headers.get("Authorization", "")
                    token = header[7:] if header.startswith("Bearer ") else ""
                    result = accounts.authenticated(token, self.path[4:], data)
                else:
                    raise AccountError("Percorso non valido.")
                self.reply(200, result)
            except (ValueError, KeyError, TypeError):
                self.reply(400, {"error": "Richiesta rifiutata: dati, credenziali o limite tentativi non validi."})
            except (OSError, TimeoutError):
                self.close_connection = True
            except Exception:
                logger.error("Account API: operazione database non riuscita")
                self.reply(503, {"error": "Servizio temporaneamente non disponibile."})
    return Handler


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, address, handler, context):
        self.context = context
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(address, handler)

    def get_request(self):
        sock, address = self.socket.accept()
        try:
            return self.context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), address
        except BaseException:
            sock.close()
            raise

    def process_request(self, request, client_address):
        if not self.slots.acquire(False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        logger.warning("Account API: connessione terminata o richiesta rifiutata")


def main(argv=None):
    parser = argparse.ArgumentParser(description="RemoteDesk account API HTTPS")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8771)
    parser.add_argument("--certfile", required=True)
    parser.add_argument("--keyfile", required=True)
    args = parser.parse_args(argv)
    url = os.environ.get("REMOTEDESK_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgres://")):
        parser.error("Configurare REMOTEDESK_DATABASE_URL con PostgreSQL per il servizio pubblico.")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(args.certfile, args.keyfile)
    server = BoundedServer((args.host, args.port), handler_for(Accounts(Database(url)),
                            os.environ.get("REMOTEDESK_REGISTRATION_CODE", "")), context)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
