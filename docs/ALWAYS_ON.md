# Restare online 24/7 senza pagare

Questa pagina risponde a una domanda precisa: **si può tenere la segreteria online
24 ore su 24 senza abbonamenti?** Sì, ma solo se una macchina resta accesa e connessa.
Qui trovi cosa fa già il progetto da solo, cosa non può fare, e le uniche strade
davvero gratuite (verificate a novembre 2026).

## Cosa fa già il progetto

- `openclaw-dashboard.service` ha `Restart=always` con `RestartSec=3`: se la dashboard
  muore, systemd la riavvia da sola.
- `watchdog.py`, ogni 2 minuti, riavvia il gateway dopo **due** controlli falliti,
  riavvia WhatsApp solo dopo **30 minuti** di stato non sano (per non entrare in un loop
  di riconnessioni), riavvia la dashboard se non risponde e riattiva i timer fermati.
- `healthcheck.py`, ogni 30 minuti, controlla configurazione, segreti, gateway, WhatsApp,
  dashboard, timer, Google, MCP, chiavi AI, approvazioni, disco e avvio automatico
  Windows; manda un avviso WhatsApp **deduplicato** al titolare quando qualcosa si rompe
  o un token sta per scadere.
- `reminders.py` ricorda le approvazioni ferme e gli appuntamenti imminenti.
- `doctor.py --short` dice cosa manca e il comando esatto per ripararlo.

## Cosa nessun software può fare da solo

1. **Accendere un computer spento.** Serve un host sempre acceso.
2. **Evitare la sospensione.** Se Windows/WSL2 va in sleep, la segreteria si ferma:
   disabilita la sospensione o usa un dispositivo dedicato.
3. **Riscansionare il QR di WhatsApp.** WhatsApp Web può invalidare la sessione: in quel
   caso serve il telefono del bot. `healthcheck` te lo segnala, ma non può aggirarlo.
4. **Rinnovare un token Google scaduto** senza il tuo consenso (il bridge Apps Script
   evita la scadenza di 7 giorni dell'app OAuth in Testing).
5. **Garantire capacità AI gratuita illimitata.** Con molto traffico i piani gratuiti
   Gemini/Groq rispondono 429; la coda assorbe i picchi, ma per volumi alti servono più
   chiavi o un piano a pagamento.

## Le opzioni gratuite reali (novembre 2026)

| Opzione | Costo | Carta richiesta | Adatta a 24/7? | Limiti da conoscere |
|---|---|---|---|---|
| **PC/portatile già tuo, o mini-PC/Raspberry** | 0 € | No | Sì, se resta acceso | Consumo elettrico, aggiornamenti Windows, serve disattivare la sospensione |
| **Oracle Cloud Always Free** | 0 € per sempre | Sì, solo verifica identità | Sì, con attenzione | 2 OCPU + 12 GB ARM (dimezzati nel 2026), 200 GB disco, 10 TB egress; solo nella *home region*; Oracle può recuperare istanze **inattive**; capacità ARM non garantita |
| **Google Cloud e2-micro free** | 0 € per la VM, ma l'IP esterno si paga | Sì, account di fatturazione | Solo con configurazione esperta | 1 VM in us-west1/us-central1/us-east1, 30 GB disco, 1 GB egress; l'indirizzo IPv4 esterno è fatturato, quindi non è davvero a costo zero |
| **Render / Koyeb / PaaS gratuiti** | 0 € | Alcuni no | **No** | Vanno in sleep dopo 15–60 minuti, niente sessione WhatsApp persistente |
| **VPS economico (Hetzner, OVH…)** | ~4–6 €/mese | Sì | Sì | Non è gratuito: escluso dalla richiesta |

> Fly.io ha chiuso le allowance gratuite a ottobre 2024, Heroku nel 2022, Koyeb chiede
> una carta dal 2026. Le guide più vecchie sono fuori tempo.

## Strada consigliata: dispositivo tuo sempre acceso (0 €, senza carta)

La più affidabile e l'unica senza carta. Un mini-PC usato, un Raspberry Pi 4/5 o un
vecchio portatile bastano (2 GB di RAM sono il minimo comodo; 4 GB meglio).

```bash
# 1. Installa il progetto sulla macchina (Linux nativo, non WSL, se puoi)
git clone https://github.com/AhsenKurtoski/whatsapp-digital-secretary.git
cd whatsapp-digital-secretary
python3 setup.py
bash scripts/install.sh

# 2. Avvio al boot senza login (fondamentale per il 24/7)
sudo loginctl enable-linger "$USER"

# 3. Verifica
python3 ~/.openclaw/secretary/doctor.py --short
```

Su Windows/WSL2 disabilita la sospensione e crea il task di avvio:

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```

`healthcheck.py` controlla già la presenza del task **OpenClaw WSL Gateway** e te la
segnala nella dashboard (Health) e su WhatsApp.

## Alternativa: Oracle Cloud Always Free (24/7 indipendente dal tuo PC)

Serve una carta per la verifica (addebito tipico 0–1 € stornato), **non** un piano a
pagamento. Scegli con cura la *home region*: le risorse Always Free devono nascere lì.

Lo script `scripts/install-oracle.sh` fa i passaggi ripetibili e verifica che la
dashboard non sia esposta. Sulla VM Ubuntu 24.04 (A1.Flex, 2 OCPU / 12 GB,
100 GB boot volume), come utente `ubuntu`:

```bash
git clone https://github.com/AhsenKurtoski/whatsapp-digital-secretary.git
cd whatsapp-digital-secretary

# 1. Diagnosi in sola lettura: non modifica nulla, esce 1 se c'è un blocco
bash scripts/install-oracle.sh --check

# 2. Dipendenze, Node 22, OpenClaw, aggiornamento repo e avvio senza login
bash scripts/install-oracle.sh --prepare

# 3. Passaggi interattivi (servono il tuo telefono)
openclaw onboard                       # modello AI e gateway
# poi: interfaccia OpenClaw -> login WhatsApp col QR
# e chiavi API in ~/.openclaw/secretary/secrets.env (chmod 600)

# 4. Configurazione attività + installazione + verifica finale
bash scripts/install-oracle.sh --install \
  --business-name "Studio Rossi" --owner-name "Mario Rossi" \
  --owner-number +393331234567 --city Milano
```

`--check` controlla sistema, Python/Node/OpenClaw, linger, repository, segreti e
soprattutto **dove ascolta la dashboard**: se la 8787 è su un indirizzo diverso da
`127.0.0.1`, o se ufw la apre, lo segnala come problema bloccante. `--install`
esegue `setup.py`, `install.sh` e `verify.sh`, e non tocca né firewall né Security
List. `--yes` evita le conferme, `--repo-dir`/`--port` cambiano i percorsi.

Poi:

- apri la porta solo per SSH (22) e **mai** la 8787: la dashboard deve restare su
  `127.0.0.1` e si raggiunge con Tailscale + tunnel SSH ([REMOTE_ACCESS.md](REMOTE_ACCESS.md));
- fai un backup cifrato di `~/.openclaw` (sessione WhatsApp) e
  `~/.config/digital-secretary`;
- tieni il traffico reale: Oracle può recuperare istanze Always Free che per 7 giorni
  stanno sotto il 20% di CPU e rete al 95° percentile. Una segreteria con poco traffico
  rientra in quella definizione: se ti arriva l'avviso, riavvia l'istanza o passa a un
  host tuo.

## Monitoraggio esterno gratuito (opzionale)

Il watchdog ripara i processi, ma se l'intero host si spegne nessuno te lo dice.
Un monitor HTTP gratuito (per esempio UptimeRobot, piano free con controlli ogni
5 minuti) può puntare a un endpoint **pubblico** della VM — non alla dashboard, che è
privata. In alternativa usa un secondo numero WhatsApp che ti scrive: se non ricevi il
riepilogo giornaliero (`digest`), qualcosa è fermo.

## Verifica finale del 24/7

```bash
python3 ~/.openclaw/secretary/doctor.py --short   # nessun FAIL, linger ok
systemctl --user is-enabled openclaw-dashboard.service
systemctl --user list-timers 'openclaw-*'
python3 ~/.openclaw/secretary/healthcheck.py --json
```

Poi riavvia la macchina e, **senza fare login**, controlla che la dashboard risponda e
che un messaggio WhatsApp di prova riceva risposta. Questo è l'unico test che dimostra
davvero il 24/7.
