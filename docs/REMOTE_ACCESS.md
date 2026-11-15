# Accesso remoto privato con Tailscale e SSH

La dashboard deve restare in ascolto su `127.0.0.1`. Per usarla da un altro
dispositivo senza esporla a Internet, usa **Tailscale** come rete privata e un
**tunnel SSH locale**. Non servono domini, porte pubbliche, VM a pagamento o carte
di credito: Tailscale ha un piano personale gratuito e OpenSSH è già nel sistema.

## Perché questa architettura

```text
telefono/portatile ── Tailscale (rete privata cifrata) ── PC della segreteria
        browser → 127.0.0.1:8787 ── SSH -L ── 127.0.0.1:8787 dashboard
```

- La dashboard continua ad ascoltare solo sul loopback: non esiste un bind pubblico.
- Il tunnel SSH inoltra una porta locale del client verso il loopback dell’host.
- Il browser usa `http://127.0.0.1:8787`, quindi cookie, login e controllo CSRF
  restano same-origin.
- Tailscale non apre la dashboard a Internet: solo i dispositivi autorizzati nella
  tua tailnet possono raggiungere l’host.

## Prerequisiti, tutti gratuiti

1. Un dispositivo sempre acceso che esegue già la segreteria (PC, mini PC,
   Raspberry Pi o vecchio portatile). Se il PC va in sospensione, la segreteria non
   è raggiungibile: disabilita la sospensione o usa un dispositivo dedicato.
2. Un account Tailscale personale gratuito, senza carta di credito.
3. Un server SSH sull’host (OpenSSH) e il client SSH sul dispositivo da cui apri
   la dashboard.
4. La dashboard installata e attiva su `127.0.0.1:8787`.

## Configurazione dell’host

Installa Tailscale e OpenSSH con il gestore pacchetti della tua distribuzione, poi:

```bash
sudo systemctl enable --now ssh
sudo tailscale up
tailscale status
```

Se l’host e il client sono sulla stessa tailnet, `tailscale ping nome-host` deve
rispondere. Non pubblicare la porta `8787` nel router o nel firewall.

Per limitare SSH alla sola interfaccia Tailscale, consenti la porta 22 solo su
`tailscale0` e non fare port forwarding dal router. Su una VPS, limita SSH
all’interfaccia Tailscale o alla tua rete fidata; la dashboard non va mai esposta.

## Configurazione del client

Installa Tailscale e OpenSSH anche sul dispositivo remoto, esegui `tailscale up` e
verifica l’host:

```bash
tailscale ping nome-host.tailnet.ts.net
```

Dal checkout del progetto, verifica i prerequisiti (dopo `bash scripts/install.sh`
lo stesso helper è in `~/.openclaw/secretary/remote_access.py`):

```bash
python3 remote_access.py --check
```

Poi avvia il tunnel:

```bash
python3 remote_access.py \
  --host nome-host.tailnet.ts.net \
  --user utente \
  --connect
```

In alternativa, senza helper:

```bash
ssh -N -T \
  -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:8787:127.0.0.1:8787 \
  utente@nome-host.tailnet.ts.net
```

Apri `http://127.0.0.1:8787` nel browser del client. Il token di accesso è in
`~/.openclaw/secretary/dashboard_token` **sull’host**: copialo nel campo di login
solo attraverso un canale fidato e non condividerlo.

Mantieni la stessa porta su entrambi i lati (`8787` per default). La dashboard
confronta l’`Origin` del browser con la propria porta: usare porte diverse può far
vedere la pagina di login ma far fallire le operazioni CSRF.

## Test e verifica

```bash
python3 remote_access.py --self-test
python3 remote_access.py --check
python3 -m unittest discover -s tests -p 'test_remote_access.py' -v
python3 -m unittest discover -s tests -p 'test_dashboard_http.py' -v
```

I test della dashboard verificano login, cookie `HttpOnly`/`SameSite=Strict`,
accettazione dell’`Origin` loopback usato dal tunnel e rifiuto di un `Origin`
esterno. Non provano una connessione Tailscale reale: quella dipende dai tuoi
account e dispositivi e va verificata con il comando `tailscale ping` e con un
login dal browser remoto.

## Limiti e regole di sicurezza

- Non usare `ssh -R`, non pubblicare `8787` e non impostare
  `DASHBOARD_HOST=0.0.0.0` per questo flusso.
- Non copiare `dashboard_token`, `secrets.env`, `google_bridge.json` o la sessione
  WhatsApp sul dispositivo remoto.
- Se perdi il dispositivo, revoca il nodo da Tailscale e rigenera il token dalla
  scheda Sicurezza della dashboard.
- Il piano gratuito di Tailscale ha limiti di utenti/dispositivi; non richiede
  carta, ma non è un servizio di hosting: la disponibilità dipende dall’host
  acceso e dalla rete.
- La dashboard non è un sito pubblico e non deve essere indicizzata o condivisa.
