# Docker full-stack: OpenClaw + dashboard

`docker-compose.full.yml` avvia due servizi:

- `openclaw`: gateway e canale WhatsApp, con configurazione e sessione nel volume
  `openclaw-data`;
- `dashboard`: la dashboard Gina, con dati in `secretary-data`.

Entrambe le porte sono pubblicate **solo su loopback** dell'host:
`127.0.0.1:8787` e `127.0.0.1:18789`.

## Avvio

```bash
cp docker/openclaw.json.example docker/openclaw.json   # personalizza il modello
docker compose -f docker-compose.full.yml up -d --build
docker compose -f docker-compose.full.yml logs -f openclaw
```

Al primo avvio:

1. apri `http://127.0.0.1:18789` e completa `onboard` / login WhatsApp col QR;
2. crea `config.json` e la persona:
   `docker compose -f docker-compose.full.yml run --rm dashboard python3 setup.py --non-interactive ...`;
3. inserisci le chiavi AI in `secretary-data/secretary/secrets.env` (permessi 600);
4. `docker compose -f docker-compose.full.yml restart dashboard`.

## Verifica

```bash
curl -fsS http://127.0.0.1:18789/health
curl -fsS http://127.0.0.1:8787/ -o /dev/null -w '%{http_code}\n'   # 200 = login page
docker compose -f docker-compose.full.yml ps
```

## Limiti reali del container

| Funzione | Dentro Docker | Conseguenza |
|---|---|---|
| systemd utente | assente | **niente watchdog e timer**: la resilienza 24/7 è affidata a `restart: unless-stopped` |
| `loginctl enable-linger` | non applicabile | usa `restart` del container |
| dashboard su `127.0.0.1` | sì, tramite port mapping | non pubblicare mai `0.0.0.0:8787` |
| sessione WhatsApp | nel volume `openclaw-data` | senza volume va rifatto il QR a ogni ricreazione |
| backup | `backup.py` va eseguito nel container o sull'host montando i volumi | testa il ripristino |
| aggiornamenti | rebuild dell'immagine | fissa `OPENCLAW_VERSION` e aggiorna con test |

## Rischio ban WhatsApp

Un container non cambia le regole di WhatsApp: un numero nuovo che scrive a molti
contatti sconosciuti può essere bloccato. Mantieni volumi bassi, usa un numero
dedicato e leggi [`WHATSAPP_BAN.md`](WHATSAPP_BAN.md). In caso di ban, ferma il
container prima di ritentare.

## Alternativa consigliata

Per il 24/7 reale il progetto consiglia un host con systemd (PC sempre acceso o VM
Oracle Always Free): watchdog, timer e linger funzionano solo lì. Vedi
[`ALWAYS_ON.md`](ALWAYS_ON.md) e `scripts/install-oracle.sh`.
