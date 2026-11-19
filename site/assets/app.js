/* Gina public site: consenso analytics e preferenze cookie.
   Nessuna richiesta a terzi e nessuno storage non tecnico prima del consenso.
   Senza analytics configurati mostra un avviso informativo non bloccante:
   non chiede né registra consensi, perché non c'è nulla da consentire.
   Il link "Cookie" nel piede di ogni pagina riapre le preferenze. */
(function () {
  "use strict";

  var STORAGE_KEY = "gina_consent_v1";
  var NOTICE_KEY = "gina_notice_v1";
  var config = window.GINA_ANALYTICS || {};
  var privacyUrl = config.privacyUrl || "/privacy.html";
  var measurement = Boolean(config.ga4 || config.cloudflare);
  var box = null;
  var opener = null;

  function stored() {
    try {
      return window.localStorage.getItem(STORAGE_KEY);
    } catch (error) {
      return null;
    }
  }

  function store(value) {
    try {
      window.localStorage.setItem(STORAGE_KEY, value);
    } catch (error) {
      /* modalità privata: la scelta vale solo per questa pagina */
    }
  }

  function noticeSeen() {
    try {
      return window.localStorage.getItem(NOTICE_KEY) === "1";
    } catch (error) {
      return false;
    }
  }

  function dismissNotice() {
    try {
      window.localStorage.setItem(NOTICE_KEY, "1");
    } catch (error) {
      /* modalità privata: l'avviso resta chiuso solo per questa pagina */
    }
  }

  function apply(choice) {
    store(choice);
    if (window.ginaAnalyticsConsent) window.ginaAnalyticsConsent(choice);
  }

  function node(tag, attributes, text) {
    var element = document.createElement(tag);
    Object.keys(attributes || {}).forEach(function (name) {
      element.setAttribute(name, attributes[name]);
    });
    if (text != null) element.textContent = text;
    return element;
  }

  function button(label, choice, kind) {
    var element = node("button", { type: "button", class: kind }, label);
    if (choice) element.setAttribute("data-choice", choice);
    return element;
  }

  function details(text) {
    var paragraph = node("p", null, text + " ");
    paragraph.appendChild(node("a", { href: privacyUrl + "#cookie" }, "Dettagli"));
    paragraph.appendChild(document.createTextNode("."));
    return paragraph;
  }

  function close() {
    if (!box) return;
    box.remove();
    box = null;
    if (opener && opener.focus) opener.focus();
    opener = null;
  }

  function build(title, text, withButtons, withClose, focusFirst) {
    close();
    box = node("aside", { class: "consent", role: "dialog", "aria-label": title });
    var copy = node("div", { class: "consent-copy" });
    copy.appendChild(node("strong", null, title));
    copy.appendChild(details(text));
    box.appendChild(copy);

    var row = node("div", { class: "row" });
    if (withButtons) {
      row.appendChild(button("Rifiuta", "denied", "btn ghost"));
      row.appendChild(button("Accetta", "granted", "btn primary"));
    }
    if (withClose) {
      var closer = button("Chiudi", null, "btn ghost");
      closer.addEventListener("click", close);
      row.appendChild(closer);
    }
    box.appendChild(row);

    box.addEventListener("click", function (event) {
      var choice = event.target.closest("[data-choice]");
      if (!choice) return;
      apply(choice.getAttribute("data-choice"));
      close();
    });
    box.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && withClose) {
        event.preventDefault();
        close();
      }
    });

    document.body.appendChild(box);
    if (focusFirst) {
      var target = box.querySelector("[data-choice]") || box.querySelector("button");
      if (target && target.focus) target.focus();
    }
  }

  function openSettings(trigger) {
    opener = trigger || null;
    var current = stored();
    var text;
    if (!measurement) {
      text = "Questo sito non ha analytics configurati: nessun cookie di misurazione, " +
        "solo il dato tecnico che ricorda questa preferenza nel tuo browser.";
    } else if (current === "granted") {
      text = "Consenso attuale: analytics attivi. Puoi revocarlo quando vuoi: " +
        "il sito continua a funzionare identico.";
    } else if (current === "denied") {
      text = "Consenso attuale: analytics disattivati, nessuna richiesta a Google. " +
        "Puoi attivarli quando vuoi.";
    } else {
      text = "Non hai ancora scelto: gli analytics sono disattivati e nulla viene caricato da terzi.";
    }
    build("Preferenze cookie", text, measurement, true, true);
  }

  function banner() {
    if (!measurement || stored()) return;
    build(
      "Analytics solo con il tuo consenso",
      "Uso Google Analytics per capire quali pagine servono davvero. Se rifiuti non carico " +
        "nulla da Google, non salvo cookie di misurazione e il sito funziona identico.",
      true,
      false,
      false
    );
  }

  /* Nessun analytics configurato: informa senza chiedere una scelta. */
  function notice() {
    if (measurement || noticeSeen()) return;
    close();
    box = node("aside", {
      class: "consent notice",
      role: "region",
      "aria-label": "Informativa cookie"
    });
    var copy = node("div", { class: "consent-copy" });
    copy.appendChild(node("strong", null, "Nessun cookie di profilazione"));
    copy.appendChild(details(
      "Questo sito non ha analytics attivi e non usa cookie pubblicitari: non c'è nulla " +
        "da accettare o rifiutare."
    ));
    box.appendChild(copy);

    var row = node("div", { class: "row" });
    var closer = button("Ho capito", null, "btn ghost");
    closer.addEventListener("click", function () {
      dismissNotice();
      close();
    });
    row.appendChild(closer);
    box.appendChild(row);

    document.body.appendChild(box);
  }

  function init() {
    var links = document.querySelectorAll("[data-cookie-settings]");
    for (var index = 0; index < links.length; index += 1) {
      links[index].addEventListener("click", function (event) {
        event.preventDefault();
        openSettings(this);
      });
    }
    var saved = stored();
    if (saved) apply(saved);
    if (measurement) banner();
    else notice();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
