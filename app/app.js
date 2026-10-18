(function () {
  const KEY = "remotedesk.console";
  const VIEWS = ["scrivania", "chiamata", "sessione", "contatti", "impostazioni"];
  const TITLES = {
    scrivania: ["Questo PC", null],
    chiamata: ["Chiamata", "Componi un numero"],
    sessione: ["Sessione", "Schermo e consenso"],
    contatti: ["Contatti", "Rubrica"],
    impostazioni: ["Impostazioni", "Alias, relay, fiducia"],
  };
  const GROUPS = [
    ["Sessione", [
      ["control", "Controllo mouse e tastiera", true],
      ["pointer", "Puntatore colorato quando l’input fisico è bloccato", true],
      ["draw", "Disegnare sullo schermo", false],
      ["privacy", "Modo privacy", false],
      ["record", "Registrazione sessione", false],
      ["audio", "Ascoltare uscita audio", false],
    ]],
    ["Scambio", [
      ["chat", "Chat", true],
      ["clipboard", "Appunti", true],
      ["clipboard_files", "Appunti per trasferire file", false],
      ["files", "File manager", true],
    ]],
    ["Dispositivo", [
      ["sysinfo", "Informazioni di sistema", true],
      ["tcp", "Tunnel TCP", false],
      ["restart", "Riavviare", false],
      ["cad", "Ctrl+Alt+Canc", false],
      ["block_input", "Bloccare tastiera e mouse locali", false],
      ["lock", "Blocca il mio dispositivo", false],
      ["disconnect_user", "Disconnetti utente", false],
      ["secure_windows", "Finestre proibite", false],
    ]],
  ];
  const FLAT = GROUPS.flatMap(([, rows]) => rows);

  const defaults = {
    alias: "Sala contratti",
    deskId: "482915037",
    relay: "203.0.113.10:8770",
    passwordSet: false,
    fingerprint: "4f:a1:9c:22:b7:e0:18:d3",
    contacts: [
      { alias: "Marco Bellini", id: "109284657" },
      { alias: "Ufficio due", id: "773041882" },
      { alias: "Archivio", id: "550198334" },
    ],
    trusted: ["109284657"],
    incoming: { alias: "Marco Bellini", id: "109284657", example: true },
  };

  let state = load();
  let session = null;
  let grantMode = "consent";
  let flashTimer = 0;

  const $ = (id) => document.getElementById(id);

  function load() {
    try {
      const raw = JSON.parse(localStorage.getItem(KEY) || "null");
      if (!raw || typeof raw !== "object") return structuredClone(defaults);
      return {
        ...structuredClone(defaults),
        ...raw,
        contacts: Array.isArray(raw.contacts) ? raw.contacts : defaults.contacts,
        trusted: Array.isArray(raw.trusted) ? raw.trusted : defaults.trusted,
      };
    } catch {
      return structuredClone(defaults);
    }
  }

  function persist() {
    const { alias, deskId, relay, passwordSet, fingerprint, contacts, trusted, incoming } = state;
    localStorage.setItem(KEY, JSON.stringify({
      alias, deskId, relay, passwordSet, fingerprint, contacts, trusted, incoming,
    }));
  }

  function formatId(id) {
    return String(id || "").replace(/(\d{3})(?=\d)/g, "$1 ").trim();
  }

  function groupsOf(id) {
    const clean = String(id || "").padStart(9, "0").slice(0, 9);
    return [clean.slice(0, 3), clean.slice(3, 6), clean.slice(6, 9)];
  }

  function paintDigits(node, id) {
    const parts = groupsOf(id);
    node.setAttribute("aria-label", String(id || "").replace(/\s/g, ""));
    node.querySelectorAll("span").forEach((span, i) => { span.textContent = parts[i]; });
  }

  function validId(id) {
    return /^\d{9}$/.test(id);
  }

  function validRelay(value) {
    if (!value) return true;
    const host = value.slice(0, value.lastIndexOf(":")).trim();
    const port = Number(value.slice(value.lastIndexOf(":") + 1));
    if (!host || host.startsWith("[") || !Number.isInteger(port)) return false;
    return port >= 1 && port <= 65535;
  }

  function aliasFor(id) {
    const found = state.contacts.find((c) => c.id === id);
    return found ? found.alias : "Scrivania " + formatId(id);
  }

  function flash(text) {
    const node = $("flash");
    node.textContent = text || "";
    clearTimeout(flashTimer);
    if (text) flashTimer = setTimeout(() => { node.textContent = ""; }, 6000);
  }

  function show(name) {
    document.querySelectorAll("[data-view]").forEach((view) => {
      view.hidden = view.dataset.view !== name;
    });
    document.querySelectorAll("[data-nav]").forEach((btn) => {
      if (btn.dataset.nav === name) btn.setAttribute("aria-current", "page");
      else btn.removeAttribute("aria-current");
    });
    const [eye, heading] = TITLES[name];
    $("eyebrow").textContent = eye;
    $("title").textContent = heading || state.alias || "Questa scrivania";
    document.title = "RemoteDesk — " + (heading || state.alias || "Scrivania");
  }

  function route() {
    const name = (location.hash || "#scrivania").slice(1);
    show(VIEWS.includes(name) ? name : "scrivania");
  }

  function renderStatus() {
    const host = session && session.role === "host";
    const guest = session && session.role === "guest";
    $("rail-state").textContent = host ? "In condivisione" : guest ? "In attesa di Accetta" : "In attesa";
    $("lamp").classList.toggle("is-live", !!host);
    $("banner").hidden = !host;
    $("session-idle").hidden = !!session;
    $("session-guest").hidden = !guest;
    $("session-host").hidden = !host;
    if (guest) {
      paintDigits($("guest-digits"), session.remoteId);
      $("guest-alias").textContent = session.remoteAlias;
    }
  }

  function renderIncoming() {
    const card = $("incoming-card");
    const empty = $("incoming-empty");
    const showCard = !!(state.incoming && !session);
    card.hidden = !showCard;
    empty.hidden = showCard;
    if (session) empty.textContent = "C’è già una sessione. Chiudila prima di accettarne un’altra.";
    else empty.textContent = "Nessuna richiesta. Il numero resta in attesa.";
    if (state.incoming) {
      $("incoming-alias").textContent = state.incoming.alias;
      $("incoming-id").textContent = formatId(state.incoming.id);
    }
    const note = $("invite-note");
    note.hidden = !state.inviteNote;
    note.textContent = state.inviteNote || "";
  }

  function renderFacts() {
    paintDigits($("desk-digits"), state.deskId);
    const relay = state.relay || "Server non impostato";
    $("relay-pill").textContent = relay;
    $("fact-relay").textContent = relay;
    $("fact-relay-help").textContent = state.relay
      ? "In ascolto per l’incontro. Le scrivanie si trovano qui, non per indirizzo di casa."
      : "Nessun relay: le chiamate in uscita non hanno un incontro.";
    $("fact-fp").textContent = state.fingerprint;
    $("fact-access").textContent = state.passwordSet ? "Password impostata" : "Chiedi sempre";
    if (!location.hash || location.hash === "#scrivania") {
      $("title").textContent = state.alias || "Questa scrivania";
    }
  }

  function personRow(person, actions) {
    const row = document.createElement("div");
    row.className = "list-row";
    const main = document.createElement("div");
    main.className = "list-main";
    const strong = document.createElement("strong");
    strong.textContent = person.alias;
    const span = document.createElement("span");
    span.textContent = formatId(person.id);
    main.append(strong, span);
    if (state.trusted.includes(person.id)) {
      const mark = document.createElement("em");
      mark.className = "trusted";
      mark.textContent = "Fidato";
      main.append(mark);
    }
    const box = document.createElement("div");
    box.className = "row-actions";
    actions.forEach((action) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-ghost btn-small";
      btn.textContent = action.label;
      btn.addEventListener("click", action.onClick);
      box.append(btn);
    });
    row.append(main, box);
    return row;
  }

  function renderContacts() {
    const callBox = $("call-contacts");
    const list = $("contact-list");
    callBox.replaceChildren();
    list.replaceChildren();
    if (!state.contacts.length) {
      const p = document.createElement("p");
      p.className = "empty";
      p.textContent = "Nessun contatto. Aggiungi un alias e nove cifre.";
      list.append(p);
      const q = document.createElement("p");
      q.className = "empty";
      q.textContent = "La rubrica è vuota.";
      callBox.append(q);
      return;
    }
    state.contacts.forEach((person) => {
      const pick = document.createElement("button");
      pick.type = "button";
      pick.className = "pick-row";
      const left = document.createElement("span");
      left.textContent = person.alias;
      const right = document.createElement("span");
      right.className = "mono";
      right.textContent = formatId(person.id);
      pick.append(left, right);
      pick.addEventListener("click", () => {
        $("call-id").value = person.id;
        $("call-password").focus();
      });
      callBox.append(pick);
      list.append(personRow(person, [
        {
          label: "Chiama",
          onClick: () => {
            $("call-id").value = person.id;
            location.hash = "chiamata";
          },
        },
        {
          label: state.trusted.includes(person.id) ? "Non fidarti" : "Fidati",
          onClick: () => {
            if (state.trusted.includes(person.id)) {
              state.trusted = state.trusted.filter((id) => id !== person.id);
            } else {
              state.trusted = state.trusted.concat(person.id);
            }
            persist();
            render();
          },
        },
        {
          label: "Rimuovi",
          onClick: () => {
            state.contacts = state.contacts.filter((c) => c.id !== person.id);
            persist();
            render();
          },
        },
      ]));
    });
  }

  function renderTrusted() {
    const box = $("trusted-list");
    box.replaceChildren();
    if (!state.trusted.length) {
      const p = document.createElement("p");
      p.className = "empty";
      p.textContent = "Nessuna scrivania fidata.";
      box.append(p);
      return;
    }
    state.trusted.forEach((id) => {
      box.append(personRow({ alias: aliasFor(id), id }, [{
        label: "Togli",
        onClick: () => {
          state.trusted = state.trusted.filter((item) => item !== id);
          persist();
          render();
        },
      }]));
    });
  }

  function renderChat() {
    const thread = $("thread");
    thread.replaceChildren();
    const lines = session && session.chat ? session.chat : [];
    if (!lines.length) {
      const p = document.createElement("p");
      p.className = "empty";
      p.textContent = "Nessun messaggio. La chat vale solo in questa sessione.";
      thread.append(p);
      return;
    }
    lines.forEach((line) => {
      const p = document.createElement("p");
      p.className = "msg" + (line.mine ? " is-mine" : "");
      const who = document.createElement("strong");
      who.textContent = line.who;
      p.append(who, document.createTextNode(line.text));
      thread.append(p);
    });
    thread.scrollTop = thread.scrollHeight;
  }

  function renderGrants() {
    const list = $("grant-live");
    list.replaceChildren();
    const granted = session && session.grants ? session.grants : {};
    FLAT.forEach(([key, label]) => {
      if (!granted[key]) return;
      const li = document.createElement("li");
      li.textContent = label;
      list.append(li);
    });
    if (!list.children.length) {
      const li = document.createElement("li");
      li.textContent = "Solo vedere. Nessun controllo, file o chat.";
      list.append(li);
    }
  }

  function render() {
    renderFacts();
    renderIncoming();
    renderStatus();
    renderContacts();
    renderTrusted();
    renderChat();
    renderGrants();
  }

  function buildGrantBox() {
    const box = $("grant-box");
    GROUPS.forEach(([title, rows]) => {
      const head = document.createElement("p");
      head.className = "check-head";
      head.textContent = title;
      box.append(head);
      rows.forEach(([key, label]) => {
        const lab = document.createElement("label");
        const input = document.createElement("input");
        input.type = "checkbox";
        input.value = key;
        input.addEventListener("change", () => {
          document.querySelectorAll("[data-profile]").forEach((btn) => {
            btn.setAttribute("aria-pressed", "false");
          });
        });
        lab.append(input, document.createTextNode(label));
        box.append(lab);
      });
    });
  }

  function applyProfile(name) {
    document.querySelectorAll("[data-profile]").forEach((btn) => {
      btn.setAttribute("aria-pressed", String(btn.dataset.profile === name));
    });
    document.querySelectorAll("#grant-box input").forEach((input) => {
      const row = FLAT.find(([key]) => key === input.value);
      input.checked = name === "view" ? false : !!(row && row[2]);
    });
  }

  function readGrants() {
    const grants = {};
    document.querySelectorAll("#grant-box input").forEach((input) => {
      grants[input.value] = input.checked;
    });
    return grants;
  }

  function openGrants(mode) {
    grantMode = mode;
    $("grant-error").hidden = true;
    $("grant-target-wrap").hidden = mode !== "invite";
    $("grant-title").textContent = mode === "invite" ? "Invita una scrivania" : "Consenti la sessione";
    $("grant-confirm").textContent = mode === "invite" ? "Invita" : "Accetta";
    if (mode === "invite") {
      $("grant-lead").textContent = "L’invito dura due minuti. I permessi sono quelli che l’altro PC potrà usare se accetta.";
      $("grant-target").value = "";
    } else if (state.incoming) {
      $("grant-lead").textContent = state.incoming.alias + " (" + formatId(state.incoming.id) + ") vuole collegarsi. Niente è già condiviso.";
    }
    applyProfile("default");
    $("grants").showModal();
    if (mode === "invite") $("grant-target").focus();
  }

  function endSession() {
    session = null;
    render();
    if ((location.hash || "#scrivania") === "#sessione") flash("Sessione chiusa. Lo schermo non esce.");
  }

  function startHost(grants) {
    const incoming = state.incoming;
    session = {
      role: "host",
      remoteId: incoming.id,
      remoteAlias: incoming.alias,
      grants,
      chat: [],
    };
    state.incoming = null;
    persist();
    render();
    location.hash = "sessione";
    flash("Consenso registrato in questa anteprima. Lo schermo non lascia il browser.");
  }

  function startGuest(id) {
    session = {
      role: "guest",
      remoteId: id,
      remoteAlias: aliasFor(id),
      grants: {},
      chat: [],
    };
    render();
    location.hash = "sessione";
    $("call-password").value = "";
    flash("In attesa di Accetta su " + formatId(id) + ". La chiamata non esce da questa pagina.");
  }

  function say(who, text, mine) {
    if (!session || session.role !== "host") return;
    session.chat.push({ who, text, mine: !!mine });
    renderChat();
  }

  $("copy-id").addEventListener("click", async () => {
    const text = state.deskId;
    let ok = false;
    try {
      await navigator.clipboard.writeText(text);
      ok = true;
    } catch {
      const area = document.createElement("textarea");
      area.value = text;
      document.body.append(area);
      area.select();
      ok = document.execCommand("copy");
      area.remove();
    }
    flash(ok ? "ID copiato." : "Copia non riuscita.");
  });

  $("open-invite").addEventListener("click", () => openGrants("invite"));
  $("accept-incoming").addEventListener("click", () => {
    if (session) {
      flash("C’è già una sessione. Interrompila prima di accettarne un’altra.");
      return;
    }
    openGrants("consent");
  });
  $("refuse-incoming").addEventListener("click", () => {
    state.incoming = null;
    persist();
    render();
    flash("Richiesta rifiutata.");
  });

  $("call-id").addEventListener("input", () => { $("call-error").hidden = true; });
  $("call-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const id = $("call-id").value.trim();
    const error = $("call-error");
    error.hidden = true;
    if (!validId(id)) {
      error.hidden = false;
      error.textContent = "L’ID è di nove cifre.";
      return;
    }
    if (id === state.deskId) {
      error.hidden = false;
      error.textContent = "È l’ID di questa scrivania.";
      return;
    }
    if (!state.relay) {
      error.hidden = false;
      error.textContent = "Manca il server relay. Impostalo prima di chiamare.";
      return;
    }
    if (session) {
      error.hidden = false;
      error.textContent = "C’è già una sessione aperta.";
      return;
    }
    startGuest(id);
  });

  $("cancel-call").addEventListener("click", () => {
    endSession();
    flash("Chiamata annullata.");
  });
  $("stop-share").addEventListener("click", () => {
    endSession();
    flash("Condivisione interrotta.");
  });

  $("chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("chat-text");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    say(state.alias || "Questa scrivania", text, true);
  });

  $("send-clip").addEventListener("click", async () => {
    try {
      const text = (await navigator.clipboard.readText()).trim();
      say("Appunti", text || "Appunti vuoti.", true);
    } catch {
      say("Appunti", "Il browser non ha consegnato gli appunti.", true);
    }
  });

  $("send-file").addEventListener("click", () => $("file-pick").click());
  $("file-pick").addEventListener("change", () => {
    const file = $("file-pick").files && $("file-pick").files[0];
    if (!file) return;
    say("File", file.name + " · in attesa di accettazione", true);
    $("file-pick").value = "";
  });

  $("contact-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const alias = $("contact-alias").value.trim();
    const id = $("contact-id").value.trim();
    const error = $("contact-error");
    error.hidden = true;
    if (!alias) {
      error.hidden = false;
      error.textContent = "Scrivi un alias.";
      return;
    }
    if (!validId(id)) {
      error.hidden = false;
      error.textContent = "L’ID è di nove cifre.";
      return;
    }
    if (id === state.deskId) {
      error.hidden = false;
      error.textContent = "È l’ID di questa scrivania.";
      return;
    }
    if (state.contacts.some((c) => c.id === id)) {
      error.hidden = false;
      error.textContent = "Questo ID è già in rubrica.";
      return;
    }
    state.contacts = state.contacts.concat({ alias, id });
    persist();
    $("contact-alias").value = "";
    $("contact-id").value = "";
    render();
    flash("Contatto aggiunto.");
  });

  $("settings-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const error = $("settings-error");
    const relay = $("set-relay").value.trim();
    error.hidden = true;
    if (!validRelay(relay)) {
      error.hidden = false;
      error.textContent = "Il relay è host:porta, con porta tra 1 e 65535.";
      return;
    }
    state.alias = $("set-alias").value.trim();
    state.relay = relay;
    if ($("set-password").value) {
      state.passwordSet = true;
      $("set-clear-password").checked = false;
    } else if ($("set-clear-password").checked) {
      state.passwordSet = false;
      $("set-clear-password").checked = false;
    }
    $("set-password").value = "";
    persist();
    render();
    flash("Impostazioni salvate su questo browser.");
  });

  document.querySelectorAll("[data-profile]").forEach((btn) => {
    btn.addEventListener("click", () => applyProfile(btn.dataset.profile));
  });

  $("grant-form").addEventListener("submit", (event) => {
    event.preventDefault();
    $("grant-confirm").click();
  });
  $("grant-cancel").addEventListener("click", () => $("grants").close());
  $("grant-confirm").addEventListener("click", () => {
    const error = $("grant-error");
    error.hidden = true;
    if (grantMode === "invite") {
      const id = $("grant-target").value.trim();
      if (!validId(id)) {
        error.hidden = false;
        error.textContent = "L’ID è di nove cifre.";
        return;
      }
      if (id === state.deskId) {
        error.hidden = false;
        error.textContent = "Non puoi invitare questa stessa scrivania.";
        return;
      }
      state.inviteNote = "Invito pronto per " + formatId(id) + ". Vale due minuti, poi si butta.";
      persist();
      $("grants").close();
      render();
      flash(state.inviteNote);
      return;
    }
    if (!state.incoming) {
      $("grants").close();
      return;
    }
    const grants = readGrants();
    $("grants").close();
    startHost(grants);
  });

  document.querySelectorAll("[data-nav]").forEach((btn) => {
    btn.addEventListener("click", () => { location.hash = btn.dataset.nav; });
  });
  window.addEventListener("hashchange", route);

  $("set-alias").value = state.alias;
  $("set-relay").value = state.relay;
  buildGrantBox();
  render();
  route();
})();
