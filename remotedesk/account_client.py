"""Account HTTPS client, with short-lived tokens kept only in memory."""
from __future__ import annotations

import json
from urllib.error import HTTPError

from remotedesk.distribution import CURRENT
from remotedesk.updates import https_request


class AccountClient:
    def __init__(self, url: str | None = None):
        self.url = (CURRENT.accounts_url if url is None else url).rstrip("/")
        self.token = ""

    def request(self, operation: str, **data) -> dict:
        if not self.url:
            raise ValueError("Servizio account non configurato in questa build.")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        try:
            with https_request(self.url + "/v1/" + operation,
                               json.dumps(data).encode("utf-8"), headers) as response:
                raw = response.read(192 * 1024 + 1)
                if len(raw) > 192 * 1024:
                    raise ValueError("Risposta account troppo grande.")
                result = json.loads(raw)
        except HTTPError as exc:
            raise ValueError("Operazione account rifiutata. Controllare credenziali e limiti di accesso.") from exc
        if operation == "login":
            self.token = result["token"]
        elif operation in ("logout", "delete-account"):
            self.token = ""
        return result
