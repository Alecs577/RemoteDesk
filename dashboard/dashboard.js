(function () {
  const digits = Array.from(document.querySelectorAll(".digit"));
  const status = document.getElementById("plate-status");
  const callId = document.getElementById("call-id");
  const result = document.getElementById("call-result");
  const server = document.getElementById("server");

  function value() {
    return digits.map((d) => d.value.replace(/\D/g, "").slice(0, 1)).join("");
  }

  function paint() {
    const id = value();
    callId.value = id;
    if (id.length === 9) {
      const shown = id.slice(0, 3) + " " + id.slice(3, 6) + " " + id.slice(6);
      status.textContent = "Numero " + shown + " pronto da chiamare.";
    } else {
      status.textContent = "Servono nove cifre. Ne manca " + (9 - id.length) + ".";
    }
  }

  digits.forEach((input, i) => {
    input.addEventListener("input", () => {
      input.value = input.value.replace(/\D/g, "").slice(0, 1);
      if (input.value && digits[i + 1]) digits[i + 1].focus();
      paint();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Backspace" && !input.value && digits[i - 1]) {
        digits[i - 1].focus();
      }
    });
    input.addEventListener("paste", (event) => {
      const text = (event.clipboardData || window.clipboardData).getData("text").replace(/\D/g, "").slice(0, 9);
      if (!text) return;
      event.preventDefault();
      digits.forEach((d, n) => { d.value = text[n] || ""; });
      paint();
    });
  });

  const tabs = Array.from(document.querySelectorAll(".output-names button"));
  const sheets = Array.from(document.querySelectorAll(".sheet"));
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.setAttribute("aria-selected", t === tab ? "true" : "false"));
      sheets.forEach((sheet) => {
        const on = sheet.id === tab.getAttribute("aria-controls");
        sheet.classList.toggle("is-on", on);
        sheet.hidden = !on;
      });
    });
  });

  let role = "host";
  document.querySelectorAll(".seg").forEach((button) => {
    button.addEventListener("click", () => {
      role = button.getAttribute("data-role");
      document.querySelectorAll(".seg").forEach((b) => b.classList.toggle("is-on", b === button));
    });
  });

  document.getElementById("call-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const id = value();
    if (id.length !== 9) {
      result.textContent = "Il numero sulla targa deve avere nove cifre.";
      return;
    }
    const shown = id.slice(0, 3) + " " + id.slice(3, 6) + " " + id.slice(6);
    if (role === "host") {
      result.textContent = "Questo PC mostra " + shown + " e aspetta Accetta. Server " + server.value + ".";
    } else {
      result.textContent = "Su Connetti componi " + shown + " verso " + server.value + ". Lì qualcuno preme Accetta.";
    }
  });

  const stop = document.getElementById("stop-demo");
  const line = document.getElementById("banner-line");
  let sharing = true;
  stop.addEventListener("click", () => {
    sharing = !sharing;
    if (sharing) {
      line.textContent = "RemoteDesk sta condividendo questo schermo";
      stop.textContent = "Interrompi";
    } else {
      line.textContent = "Condivisione interrotta. Mouse e schermo sono fermi.";
      stop.textContent = "Riprendi l’esempio";
    }
  });

  paint();

  const mark = document.querySelector(".wordmark");
  function scaleMark() {
    const p = Math.min(1, window.scrollY / window.innerHeight);
    mark.style.transform = "scale(" + (1 - p * 0.06) + ")";
  }
  scaleMark();
  window.addEventListener("scroll", scaleMark, { passive: true });

  const seen = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) entry.target.classList.add("is-in");
    });
  }, { threshold: 0.18 });
  document.querySelectorAll(".reveal").forEach((el, index) => {
    el.style.transitionDelay = (index % 5) * 70 + "ms";
    seen.observe(el);
  });
})();
