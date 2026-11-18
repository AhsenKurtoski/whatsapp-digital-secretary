/* GA4 con Consent Mode v2 e Cloudflare Web Analytics senza cookie.
   Nessuna richiesta a terzi parte prima del consenso: i loader vengono creati
   solo quando l'utente accetta (vedi app.js). analytics.config.js è generato dal
   build e contiene gli ID reali. */
(function () {
  "use strict";

  var config = window.GINA_ANALYTICS || {};
  var ga = String(config.ga4 || "").trim();
  var cf = String(config.cloudflare || "").trim();
  var enabled = Boolean(ga || cf);
  var loaded = false;

  window.dataLayer = window.dataLayer || [];
  function gtag() { window.dataLayer.push(arguments); }
  window.gtag = window.gtag || gtag;

  /* Consent Mode v2: tutto negato finché non arriva una scelta esplicita. */
  gtag("consent", "default", {
    ad_storage: "denied",
    ad_user_data: "denied",
    ad_personalization: "denied",
    analytics_storage: "denied",
    functionality_storage: "granted",
    security_storage: "granted"
  });

  function load() {
    if (!enabled || loaded) return;
    loaded = true;
    if (ga) {
      gtag("js", new Date());
      gtag("config", ga, {
        anonymize_ip: true,
        allow_google_signals: false,
        allow_ad_personalization_signals: false
      });
      var loader = document.createElement("script");
      loader.async = true;
      loader.src = "https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(ga);
      document.head.appendChild(loader);
    }
    if (cf) {
      var beacon = document.createElement("script");
      beacon.defer = true;
      beacon.src = "https://static.cloudflareinsights.com/beacon.min.js";
      beacon.setAttribute("data-cf-beacon", JSON.stringify({ token: cf }));
      document.head.appendChild(beacon);
    }
  }

  window.ginaAnalyticsConsent = function (choice) {
    var granted = choice === "granted" && enabled;
    gtag("consent", "update", {
      ad_storage: "denied",
      ad_user_data: "denied",
      ad_personalization: "denied",
      analytics_storage: granted ? "granted" : "denied"
    });
    if (granted) load();
  };
})();
