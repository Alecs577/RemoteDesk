"""Pagina HTML del visualizzatore (italiano)."""

VIEWER_HTML = """<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RemoteDesk — Visualizzatore</title>
  <style>
    body { font-family: "Segoe UI", sans-serif; margin: 1.25rem; background: #f4fbfe; color: #143044; }
    h1 { font-size: 1.35rem; font-weight: 500; }
    #connect { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; margin-bottom: 1rem; }
    input#code { font-size: 1rem; width: min(26em, 100%); padding: 0.4rem 0.55rem; border: 1px solid #c9e7f6; border-radius: 8px; color: #143044; }
    button { padding: 0.45rem 0.9rem; cursor: pointer; background: #176184; color: #fff; border: 0; border-radius: 8px; }
    #status { margin: 0.5rem 0; min-height: 1.2em; color: #4d6574; }
    #screen { max-width: 100%; border: 1px solid #c9e7f6; border-radius: 12px; background: #e5f4fb; touch-action: none; user-select: none; }
    #viewer { display: none; }
  </style>
</head>
<body>
  <h1>RemoteDesk — Visualizzatore</h1>
  <div id="connect">
    <label for="code">Chiave sessione:</label>
    <input id="code" type="text" maxlength="128" autocomplete="off">
    <button type="button" id="btnConnect">Connetti</button>
    <button type="button" id="btnDisconnect" style="display:none">Disconnetti</button>
  </div>
  <p id="status">Inserisci il codice mostrato sul computer che condivide lo schermo.</p>
  <div id="viewer">
    <img id="screen" alt="Schermo remoto" draggable="false">
  </div>
  <script>
(function () {
  const codeInput = document.getElementById('code');
  const btnConnect = document.getElementById('btnConnect');
  const btnDisconnect = document.getElementById('btnDisconnect');
  const statusEl = document.getElementById('status');
  const viewer = document.getElementById('viewer');
  const screen = document.getElementById('screen');

  let sessionCode = '';
  let pollTimer = null;
  let connected = false;

  function setStatus(msg) { statusEl.textContent = msg; }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    connected = false;
    viewer.style.display = 'none';
    btnDisconnect.style.display = 'none';
    btnConnect.style.display = '';
    codeInput.disabled = false;
  }

  function imageCoords(evt) {
    const r = screen.getBoundingClientRect();
    const x = (evt.clientX - r.left) * (screen.naturalWidth / r.width);
    const y = (evt.clientY - r.top) * (screen.naturalHeight / r.height);
    return { x: Math.max(0, Math.min(screen.naturalWidth, x)),
             y: Math.max(0, Math.min(screen.naturalHeight, y)) };
  }

  async function sendInput(body) {
    try {
      await fetch('/input', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
    } catch (e) { /* ignore */ }
  }

  screen.addEventListener('mousemove', (e) => {
    if (!connected) return;
    const p = imageCoords(e);
    sendInput({ code: sessionCode, kind: 'move', x: p.x, y: p.y });
  });
  screen.addEventListener('mousedown', (e) => {
    if (!connected) return;
    e.preventDefault();
    const p = imageCoords(e);
    const button = e.button === 2 ? 'right' : (e.button === 1 ? 'middle' : 'left');
    sendInput({ code: sessionCode, kind: 'down', x: p.x, y: p.y, button });
  });
  screen.addEventListener('mouseup', (e) => {
    if (!connected) return;
    const button = e.button === 2 ? 'right' : (e.button === 1 ? 'middle' : 'left');
    sendInput({ code: sessionCode, kind: 'up', x: 0, y: 0, button });
  });
  screen.addEventListener('contextmenu', (e) => e.preventDefault());
  screen.addEventListener('click', (e) => {
    if (!connected) return;
    const p = imageCoords(e);
    const button = e.button === 2 ? 'right' : 'left';
    sendInput({ code: sessionCode, kind: 'click', x: p.x, y: p.y, button });
  });

  document.addEventListener('keydown', (e) => {
    if (!connected) return;
    if (e.target === codeInput) return;
    e.preventDefault();
    sendInput({ code: sessionCode, kind: 'key', key: e.key });
  });

  async function pollFrame() {
    try {
      const res = await fetch('/frame.jpg?code=' + encodeURIComponent(sessionCode), { cache: 'no-store' });
      if (res.status === 403) {
        setStatus('Accesso negato: codice errato o condivisione non attiva.');
        stopPolling();
        return;
      }
      if (res.status === 204) return;
      if (!res.ok) return;
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const prev = screen.src;
      screen.onload = () => { if (prev && prev.startsWith('blob:')) URL.revokeObjectURL(prev); };
      screen.src = url;
      viewer.style.display = 'block';
    } catch (e) {
      setStatus('Errore di rete durante il polling.');
    }
  }

  async function startSession() {
    const c = codeInput.value.trim();
    if (!/^\\d{6}$/.test(c)) {
      setStatus('Il codice deve essere di 6 cifre numeriche.');
      return;
    }
    btnConnect.disabled = true;
    setStatus('Connessione in corso (in attesa consenso host)…');
    let res;
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 65000);
      res = await fetch('/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: c }),
        signal: ctrl.signal
      });
      clearTimeout(t);
    } catch (e) {
      btnConnect.disabled = false;
      setStatus('Errore di rete o timeout durante la connessione.');
      return;
    }
    if (res.status === 403) {
      btnConnect.disabled = false;
      setStatus('Connessione rifiutata dall\\'host o codice errato.');
      return;
    }
    if (res.status === 409) {
      btnConnect.disabled = false;
      setStatus('Un\\'altra richiesta di connessione è già in sospeso.');
      return;
    }
    if (res.status !== 200) {
      btnConnect.disabled = false;
      setStatus('Connessione non riuscita (HTTP ' + res.status + ').');
      return;
    }
    sessionCode = c;
    connected = true;
    codeInput.disabled = true;
    btnConnect.style.display = 'none';
    btnDisconnect.style.display = '';
    setStatus('Connesso. In attesa di frame…');
    pollFrame();
    pollTimer = setInterval(pollFrame, 125);
  }

  btnConnect.addEventListener('click', () => { startSession(); });

  btnDisconnect.addEventListener('click', () => {
    stopPolling();
    setStatus('Disconnesso.');
  });
})();
  </script>
</body>
</html>
"""
