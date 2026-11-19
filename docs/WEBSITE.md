# Sito pubblico, SEO, analytics e sicurezza

La dashboard della segreteria è **privata** e ascolta solo su `127.0.0.1`: non va
pubblicata, indicizzata o esposta. Il sito pubblico è un progetto separato, nella
cartella [`site/`](../site), pensato per far trovare l'autore e il progetto quando
qualcuno cerca **Ahsen Kurtoski** o **Gina WhatsApp secretary**.

`site/` contiene i template e la configurazione; `site_build.py` genera la cartella
pronta da pubblicare; `seo_audit.py` è il controllo automatico che blocca una
pubblicazione con SEO o header di sicurezza mancanti.

## 1. Cosa viene generato

```text
site/dist/
├── index.html              # home: prodotto, privacy, autore, FAQ + JSON-LD
├── privacy.html            # informativa privacy e cookie (tabella cookie)
├── termini.html            # termini d'uso: licenza, garanzie, marchi, foro
├── 404.html                # pagina noindex
├── robots.txt              # Allow + Sitemap
├── sitemap.xml             # home + privacy, lastmod reale
├── _headers                # security headers per Cloudflare Pages / Netlify
├── vercel.json             # gli stessi header per Vercel
├── security.txt            # RFC 9116 (anche in .well-known/security.txt)
├── manifest.json
└── assets/
    ├── style.css
    ├── app.js              # banner consenso + preferenze cookie revocabili
    ├── analytics.js        # GA4 Consent Mode v2, caricato solo dopo il consenso
    ├── analytics.config.js # ID generati dal build (mai inventati)
    ├── favicon.svg
    ├── dashboard-480.webp  # hero responsive: 480/720/1080/1440 px
    ├── dashboard-720.webp
    ├── dashboard-1080.webp # versione precaricata su mobile
    ├── dashboard-1440.webp
    └── og.jpg              # anteprima social 1200×917 JPEG (~96 KiB)
```

L'immagine hero è servita in WebP a quattro larghezze con `srcset`/`sizes`,
`loading="eager"`, `fetchpriority="high"` e un `preload` con `imagesrcset`: è
l'elemento LCP e senza questi attributi Lighthouse misurava 5,3 s di LCP.

La card social è un JPEG da 1200 px (96 KiB) e non più l'843 KiB PNG: le
anteprime social venivano scaricate intere a ogni condivisione. Per rigenerarla
dallo screenshot reale:

```bash
python3 -c "from PIL import Image; im = Image.open('docs/screenshot-dashboard.png').convert('RGB'); w = 1200; im = im.resize((w, round(w * im.height / im.width)), Image.LANCZOS); im.save('site/assets/og.jpg', 'JPEG', quality=82, optimize=True, progressive=True)"
```

SEO incluso: title e description per pagina, canonical assoluto, Open Graph,
Twitter Card, `hreflang` non necessario (sito in italiano), JSON-LD `Person`,
`SoftwareApplication`, `WebSite` e `FAQPage`, sitemap, robots, immagini con `alt`,
link esterni con `rel="noopener"`, HTML semantico e un solo `h1` per pagina.

## 2. Configurazione

Modifica [`site/config.json`](../site/config.json):

| Campo | Obbligatorio | Cosa mettere |
| --- | --- | --- |
| `base_url` | sì | Il dominio reale, es. `https://ahsenkurtoski.it` (senza slash finale). |
| `owner_name` | sì | `Ahsen Kurtoski`. |
| `owner_url` | sì | Profilo GitHub o sito personale. |
| `repo_url` | sì | Repository pubblico del progetto. |
| `description` | sì | Descrizione di 50–165 caratteri, usata anche come meta description. |
| `analytics.ga4_measurement_id` | no | ID GA4 `G-XXXXXXXXXX`. Vuoto = nessun Google Analytics. |
| `analytics.cloudflare_token` | no | Token di Cloudflare Web Analytics (senza cookie). |
| `verification.google` | no | Token del tag HTML di Google Search Console. |
| `verification.bing` | no | Token `msvalidate.01` di Bing Webmaster Tools. |
| `verification.yandex` | no | Token di Yandex Webmaster. |
| `security_txt_contact` | no | URL o email per le segnalazioni di sicurezza. |

Il build **si ferma** se un ID è malformato o se `base_url` manca: non pubblica mai
un tag di verifica sbagliato o un canonical finto. Gli ID vuoti semplicemente non
producono tag.

### Google Analytics 4

1. Crea la proprietà in [analytics.google.com](https://analytics.google.com) con
   l'account del titolare.
2. Copia l'ID di misurazione `G-…` e mettilo in `analytics.ga4_measurement_id`.
3. Il sito carica GA4 solo dopo il consenso (Consent Mode v2, `analytics_storage`
   negato di default, IP anonimizzato, segnali pubblicitari disattivati).

### Google Search Console

1. Apri [search.google.com/search-console](https://search.google.com/search-console)
   e aggiungi la proprietà per dominio o per prefisso URL.
2. Scegli la verifica con **tag HTML**, copia il valore di `content` e mettilo in
   `verification.google`.
3. Fai il build, pubblica e premi **Verifica**.
4. In **Sitemap** invia `https://tuodominio/sitemap.xml`.
5. Usa **Controllo URL** sulla home per chiedere l'indicizzazione.

La verifica DNS (record TXT) è un'alternativa valida e non richiede modifiche al
sito: in quel caso lascia `verification.google` vuoto.

### Bing Webmaster Tools

1. Apri [bing.com/webmasters](https://www.bing.com/webmasters) e aggiungi il sito.
2. Scegli la verifica con meta tag, copia il valore di `msvalidate.01` e mettilo in
   `verification.bing`.
3. Invia la sitemap da **Sitemaps**. Bing può importare anche la proprietà da
   Google Search Console.

### Yandex Webmaster

Facoltativo per un sito italiano. Aggiungi il sito in
[yandex.com/webmaster](https://yandex.com/webmaster), copia il token di verifica e
mettilo in `verification.yandex`.

### Account esterni

Google Analytics, Search Console, Bing Webmaster e Yandex richiedono un account del
titolare e la verifica del dominio: non possono essere creati dal repository. Il
codice è completo; servono solo gli ID reali in `site/config.json`.

## 3. Build e audit

```bash
python3 site_build.py --config site/config.json --out site/dist
python3 seo_audit.py --dir site/dist
```

Output atteso:

```text
SITO OK: 4 pagine in site/dist · base https://tuodominio.it · analytics GA4
SEO AUDIT OK
```

L'audit controlla title, description, canonical, un solo `h1`, `lang`, viewport,
Open Graph, Twitter Card, JSON-LD obbligatorio, `alt` delle immagini, sitemap,
robots, `security.txt`, `_headers`, `vercel.json` e formato dell'ID GA4. Senza GA4
produce un avviso esplicito, non un errore.

Per provare in locale senza dominio reale:

```bash
python3 site_build.py --base-url https://example.com --out /tmp/site-preview
python3 -m http.server 8080 --directory /tmp/site-preview
```

## 4. Pubblicazione

Pubblica **solo** `site/dist`, mai la root del repository e mai la dashboard.

### Cloudflare Pages (consigliato)

- Build command: `python3 site_build.py --config site/config.json --out site/dist`
- Output directory: `site/dist`
- Cloudflare applica `_headers` e `_redirects`; HTTPS e HSTS sono gestiti dal bordo.
- Aggiungi il dominio personalizzato e imposta `base_url` sullo stesso dominio.

### Netlify

- Build command e publish directory come sopra; Netlify legge `_headers`.

### Vercel

Il progetto è collegato al repository e pubblica solo il sito statico; la dashboard
non viene mai inclusa.

- Framework preset: **Other**.
- Build command: `python3 site_build.py --config site/config.json --out public`.
- Output directory: `public`.
- Vercel legge gli header dal [`vercel.json`](../vercel.json) nella root del
  repository, non da quello dentro la cartella generata: il test
  `test_vercel_manifest_matches_the_build` impedisce che i due divergano.
- `base_url` è `https://gina-secretary.vercel.app`, quindi canonical, sitemap e
  `robots.txt` puntano già al dominio definitivo.
- Auto-deploy attivo su `main`: `vercel.json` dichiara `git.deploymentEnabled.main:
  true` e ogni push crea un deployment di produzione promosso su
  `gina-secretary.vercel.app` (verificato il 19/11/2026 con `4c71e47`, READY e alias
  assegnato quattro secondi dopo il push).
- Il progetto Vercel è separato dalla dashboard: non pubblicare mai la porta 8787.

Verifica dopo il deploy:

```bash
curl -sSI https://gina-secretary.vercel.app/ | grep -Ei 'content-security|strict-transport|x-content|x-frame|referrer|permissions'
curl -sS https://gina-secretary.vercel.app/sitemap.xml
```

### GitHub Pages

GitHub Pages non permette header HTTP personalizzati. Il sito include una
Content-Security-Policy via `<meta>` come fallback, ma HSTS e `frame-ancestors`
richiedono un proxy. Per una configurazione completa usa Cloudflare Pages o metti
Cloudflare davanti a GitHub Pages.

Dopo la pubblicazione:

```bash
curl -sSI https://tuodominio.it/ | grep -Ei 'content-security|strict-transport|x-content|x-frame|referrer|permissions'
```

## 5. Sicurezza del sito

Header applicati da `_headers` e `vercel.json`:

| Header | Valore |
| --- | --- |
| `Content-Security-Policy` | solo self + Google Tag Manager + Cloudflare Insights; niente inline script; `frame-src`, `worker-src`, `media-src` negati |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains; preload` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | accelerometro, camera, geolocalizzazione, giroscopio, magnetometro, microfono, pagamenti, USB, cattura schermo e Topics disattivati |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Cross-Origin-Resource-Policy` | `same-origin` |
| `X-Permitted-Cross-Domain-Policies` | `none` |

In più: `security.txt` conforme a RFC 9116, nessun font o immagine da CDN esterni,
nessun form, nessun cookie prima del consenso, analytics con IP anonimizzato.

### Cookie banner e consenso

Il sito mostra sempre un avviso cookie, con due comportamenti distinti:

- **Senza analytics configurati** (oggi `analytics.ga4_measurement_id` e
  `analytics.cloudflare_token` sono vuoti) compare un avviso informativo
  **non bloccante**: dice che non ci sono cookie di profilazione né analytics attivi,
  quindi non c'è nulla da accettare, e si chiude con **Ho capito**. Non viene scritto
  `gina_consent_v1` e non viene registrato alcun consenso: la sola chiave
  `gina_notice_v1` ricorda che l'avviso è stato chiuso. Così il banner è visibile
  subito, senza inventare una scelta che non esiste.
- **Con almeno un ID configurato** compare il banner di consenso vero e proprio:

- Prima della scelta **nessuno script di terze parti viene caricato**: `analytics.js`
  registra solo i default di Consent Mode v2 (`analytics_storage: denied`) e i loader
  di Google/Cloudflare nascono dentro `load()`, chiamata solo dopo "Accetta".
- La scelta è salvata in `localStorage` (`gina_consent_v1`, dato tecnico) e il link
  **Cookie** nel piede di ogni pagina riapre le preferenze: revocare è facile quanto
  accettare. Su `privacy.html` la tabella `#cookie` elenca nome, fornitore, finalità,
  durata e se il cookie richiede consenso.
- "Rifiuta" non riduce nulla: pagine, link e download funzionano identici.

Il repository usa Dependabot, `pip-audit`, Bandit e CodeQL
([`.github/workflows/codeql.yml`](../.github/workflows/codeql.yml)) per il codice.

## 6. Controlli in CI

```bash
python3 site_build.py --self-test
python3 seo_audit.py --self-test
python3 -m unittest discover -s tests -p 'test_site.py' -v
```

Se `base_url` è configurato, `scripts/verify.sh` esegue anche build e audit reali.

## 7. Lighthouse CI (tre esecuzioni, mediana)

[`lighthouserc.json`](../lighthouserc.json) misura il sito **live** in mobile con tre
run per URL (`/`, `/privacy.html` e `/termini.html`) e valuta la mediana, così un picco di rete non
trasforma un numero fortunato in un gate. Soglie bloccanti:

| Controllo | Soglia |
| --- | --- |
| Performance | ≥ 0,90 |
| Accessibility | ≥ 0,95 |
| Best Practices | ≥ 0,95 |
| SEO | 1,00 |
| LCP (mediana) | ≤ 2,5 s |
| CLS (mediana) | ≤ 0,10 |
| TBT (mediana) | avviso oltre 200 ms |

In locale, con Chrome installato:

```bash
npx --yes @lhci/cli@0.16.1 autorun
```

I report HTML finiscono in `artifacts/lighthouse/`.

Misura reale del 19/11/2026 (Chrome for Testing 158, mobile, throttling simulato,
nove run totali sul sito live con l'avviso cookie attivo, tutte le assertion
superate):

| URL | Performance | Accessibility | Best Practices | SEO | LCP (mediana) | CLS | TBT |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `/` | 100 · 100 · 100 | 100 | 100 | 100 | 0,85 s | 0 | 0–36 ms (mediana 1 ms) |
| `/privacy.html` | 100 · 100 · 100 | 100 | 100 | 100 | 0,81 s | 0 | 0 ms |
| `/termini.html` | 100 · 100 · 100 | 100 | 100 | 100 | 0,81 s | 0 | 0 ms |

Se in un container Lighthouse riporta `NO_FCP` solo sulle pagine di testo, mancano i
font di sistema (la home dipinge comunque l'immagine hero). Non è un problema del
sito: installa `fonts-dejavu-core` o fornisci un `fonts.conf` al browser. La stessa configurazione gira
in [`.github/workflows/lighthouse.yml`](../.github/workflows/lighthouse.yml) a ogni
push su `main`, sulle pull request, ogni lunedì e su richiesta manuale. Il workflow
testa l'URL di produzione; ogni push su `main` genera un deployment Vercel
automatico (`vercel.json` dichiara `git.deploymentEnabled.main: true`), quindi il
gate misura il commit appena pubblicato.
