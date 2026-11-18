/* GA4 con Consent Mode v2 e Cloudflare Web Analytics senza cookie.
   analytics.config.js è generato dal build e contiene gli ID reali. */
(function () {
  "use strict";

  var config = window.GINA_ANALYTICS || {};
  var ga = String(config.ga4 || "").trim();
  var cf = String(config.cloudflare || "").trim();

  window.dataLayer = window.dataLayer || [];
  function gtag() { window.dataLayer.push(arguments); }
  window.gtag = window.gtag || gtag;

  if (ga) {
    gtag("consent", "default", {
      ad_storage: "denied",
      ad_user_data: "denied",
      ad_personalization: "denied",
      analytics_storage: "denied",
      functionality_storage: "granted",
      security_storage: "granted"
    });
    gtag("js", new Date());
    gtag("config", ga, { anonymize_ip: true, allow_google_signals: false });

    var loader = document.createElement("script");
    loader.async = true;
    loader.src = "https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(ga);
    document.head.appendChild(loader);
  }

  window.ginaAnalyticsConsent = function (choice) {
    if (ga && typeof window.gtag === "function") {
      window.gtag("consent", "update", {
        ad_storage: "denied",
        ad_user_data: "denied",
        ad_personalization: "denied",
        analytics_storage: choice === "granted" ? "granted" : "denied"
      });
      if (choice === "granted") {
        window.gtag("event", "page_view", {
          page_location: window.location.href,
          page_title: document.title
        });
      }
    }
  };

  if (cf) {
    var beacon = document.createElement("script");
    beacon.defer = true;
    beacon.src = "https://static.cloudflareinsights.com/beacon.min.js";
    beacon.setAttribute("data-cf-beacon", JSON.stringify({ token: cf }));
    document.head.appendChild(beacon);
  }

  try {
    var choice = window.localStorage.getItem("gina_consent_v1");
    if (choice) window.ginaAnalyticsConsent(choice);
  } catch (error) {
    /* nessun consenso memorizzato */
  }
})();
