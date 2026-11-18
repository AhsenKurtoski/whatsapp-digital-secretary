/* Gina public site: consenso analytics e piccoli miglioramenti progressivi.
   Nessuna dipendenza esterna, nessun cookie prima dell'accept. */
(function () {
  "use strict";

  var config = window.GINA_ANALYTICS || {};
  var hasAnalytics = Boolean(config.ga4 || config.cloudflare);
  var storageKey = "gina_consent_v1";

  function stored() {
    try {
      return window.localStorage.getItem(storageKey);
    } catch (error) {
      return null;
    }
  }

  function store(value) {
    try {
      window.localStorage.setItem(storageKey, value);
    } catch (error) {
      /* modalità privata: il consenso vale solo per questa pagina */
    }
  }

  function banner() {
    if (!hasAnalytics || stored()) return;
    var box = document.createElement("aside");
    box.className = "consent";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", "Consenso agli analytics");
    box.innerHTML =
      "<p>Uso analytics solo con il tuo consenso. Se rifiuti, il sito funziona identico e non salva cookie di misurazione. " +
      '<a href="' + (config.privacyUrl || "/privacy.html") + '">Dettagli</a>.</p>' +
      '<div class="row"><button type="button" class="btn ghost" data-choice="denied">Rifiuta</button>' +
      '<button type="button" class="btn primary" data-choice="granted">Accetta</button></div>';
    box.addEventListener("click", function (event) {
      var button = event.target.closest("[data-choice]");
      if (!button) return;
      var choice = button.getAttribute("data-choice");
      store(choice);
      if (window.ginaAnalyticsConsent) window.ginaAnalyticsConsent(choice);
      box.remove();
    });
    document.body.appendChild(box);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", banner);
  } else {
    banner();
  }
})();
