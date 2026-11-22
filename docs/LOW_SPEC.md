# Macchine leggere: meno di 8 GB di RAM, nessun Docker

Questa pagina risponde con numeri a una domanda sola: **la segreteria gira su una
macchina modesta?** Sì. Il progetto è Python puro più OpenClaw (Node.js), non usa
container, non compila nulla e non tiene in memoria i modelli AI: le risposte arrivano
dalle API remote, quindi la RAM locale serve solo per i processi.

## Numeri misurati

Misurazioni reali su Linux, Python 3.12, novembre 2026 (macchina di sviluppo, nessuna
personalizzazione), con `python3 -X importtime` e `/proc/<pid>/status`:

| Processo | RAM (RSS) | Avvio |
|---|---|---|
| `dashboard.py` in esecuzione | **31,5 MB** | — |
| `dashboard.py` dopo 10 richieste alla pagina | 31,6 MB (stabile) | 108 ms |
| `healthcheck.py` (processo breve) | 21,4 MB | 79 ms |
| `watchdog.py` (processo breve) | 20,6 MB | 89 ms |
| `doctor.py` (processo breve) | 20,7 MB | 80 ms |
| `memory.py` / `conversations.py` / `identity.py` | 17,7–19,2 MB | 52–65 ms |

Il costo dominante **non** è il codice Python: è OpenClaw, cioè il processo Node che
tiene la sessione WhatsApp. Su una installazione reale sta tipicamente tra **250 e
450 MB**, con picchi quando arrivano molte chat insieme. Il totale atteso a regime è
quindi **350–700 MB**, più il sistema operativo.

| Macchina | Verdetto |
|---|---|
| 512 MB | No: il solo Node non ci sta |
| **1 GB + 1 GB swap** | Minimo tecnico: funziona, lento sotto carico |
| **2 GB** | **Consigliato per Raspberry Pi 4/5 e mini-PC usati** |
| 4 GB | Comodo, margine per più conversazioni |
| 8 GB | Nessun problema, il profilo `low_memory` si disattiva da solo |

## Cosa fa il progetto per stare leggero

- **Nessun Docker**: niente layer, niente daemon, niente doppia copia dei dati. Un
  `git pull` + `bash scripts/install.sh` aggiorna tutto.
- **Limiti systemd già installati**: dashboard `MemoryHigh=384M` / `MemoryMax=512M`, i
  processi periodici (watchdog, healthcheck, promemoria, monitor, digest)
  `MemoryMax=512M`. I limiti sono larghi perché il cgroup include anche i **figli**
  Node/OpenClaw lanciati da questi processi (per esempio l'invio WhatsApp): 256 MB
  avrebbe potuto uccidere un job mentre il figlio era in memoria. Se un bug gonfia un
  processo, systemd lo contiene invece di far finire la macchina in swap, e la
  dashboard ha `Restart=always` con `RestartSec=3`.
- **Profilo `low_memory`**: si attiva da solo sotto i 4 GB di RAM (o con
  `"low_memory": true` in `config.json`) e:
  - limita `max_concurrent` a 2 chiamate AI in parallelo;
  - accorcia lo storico per contatto (300 → 120 messaggi, transcript 200 → 80);
  - aggiunge al gateway un limite morbido `MemoryHigh=1200M` (rallenta e recupera
    memoria, non uccide il bot).
- **Cache misurate**: una sola pagina renderizzata in cache, gzip, TTL brevi; la
  dashboard non ricarica i dati a ogni tab.
- **Nessun modello locale**: Gemini/Groq rispondono via rete, la RAM serve solo al
  contesto della singola conversazione.

## Attivare o forzare il profilo

```bash
# automatico: sotto i 4 GB si attiva da solo, sopra resta spento
python3 -c 'import config; print(config.low_memory())'

# forzato (utile su una macchina da 6 GB condivisa con altro software)
python3 - <<'PY'
import json, pathlib
p = pathlib.Path.home() / ".config" / "digital-secretary" / "config.json"
cfg = json.loads(p.read_text())
cfg["low_memory"] = True
p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
PY

bash scripts/install.sh        # applica il limite al gateway
systemctl --user restart openclaw-gateway.service openclaw-dashboard.service
```

Per tornare indietro: `"low_memory": false` e rilancia `install.sh` (rimuove il drop-in).

## Tuning del sistema (Linux / Raspberry Pi)

```bash
# 1. zram: swap compresso in RAM, molto più veloce della microSD
sudo apt install zram-tools            # Debian/Ubuntu/Raspberry Pi OS
#   /etc/default/zramswap:  ALGO=zstd   PERCENT=50
sudo systemctl restart zramswap

# 2. swappiness basso: usa lo swap solo quando serve davvero
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-secretary.conf
sudo sysctl --system

# 3. swapfile da 1 GB se non hai zram (SSD o USB, non microSD)
sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 4. log di sistema limitati: su 2 GB fanno la differenza
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=100M\n' | sudo tee /etc/systemd/journald.conf.d/99-secretary.conf
sudo systemctl restart systemd-journald
```

Su Raspberry Pi usa un **SSD USB** per `~/.openclaw` se puoi: la microSD si consuma con
le scritture della memoria clienti e dei log. In alternativa monta `/tmp` in tmpfs e
tieni i backup su disco esterno.

Su Windows con WSL2 limita la VM invece di lasciarle prendere tutta la RAM
(`%UserProfile%\.wslconfig`):

```ini
[wsl2]
memory=4GB
processors=2
swap=2GB
```

## Verifica sulla tua macchina

```bash
python3 scripts/footprint.py                 # tabella RAM reale + verdetto sul budget
python3 scripts/footprint.py --json          # stessa cosa, per script
python3 scripts/lowmem_check.py              # verifica sul campo (vedi sotto)
python3 healthcheck.py --json | grep -A5 '"ram"'
systemctl --user show openclaw-dashboard.service -p MemoryCurrent
```

### Verifica sul campo: `scripts/lowmem_check.py`

I test automatici girano in una sandbox: non possono dire se **questa** macchina supporta
i limiti di systemd, se un job è già stato ucciso dall'OOM killer o se lo swap basta.
Questo script legge lo stato reale e non modifica nulla.

```bash
python3 scripts/lowmem_check.py                     # tabella leggibile, exit 1 se c'è un FAIL
python3 scripts/lowmem_check.py --json              # per cron o monitoraggio
python3 scripts/lowmem_check.py --since "48 hours ago"
python3 scripts/lowmem_check.py --budget-mb 1200    # budget più stretto per 2 GB
```

| Controllo | Cosa verifica | Quando è `fail` |
|---|---|---|
| `profile` | `low_memory` attivo e RAM totale | mai: su < 4 GB senza profilo è `warn` |
| `cgroup` | cgroup v1/v2 e controller `memory` | cgroup assente, o v2 senza controller memory |
| `unit:<nome>` | `MemoryMax` e picco reale di ogni unit | nessun limite, o picco ≥ 90% del massimo |
| `oom[:unit]` | righe OOM/kill nei log (`journalctl --user`) | almeno una riga trovata |
| `footprint` | RAM dei processi rispetto al budget | oltre il budget |
| `swap` | swap totale, dispositivi, zram | su < 4 GB con meno di 512 MB di swap |
| `swappiness` | `vm.swappiness` | mai: sopra 20 è `warn` |

Le unit non installate sono `skip`, non errori: su una macchina senza la segreteria
installata lo script resta utile per profilo, cgroup e swap. I `warn` non fanno fallire
l'exit code; i `fail` sì, così può essere usato in un timer o in CI su hardware reale.

Interpretazione rapida:

- `unit:…` con picco vicino al massimo → alza `MemoryMax` nella unit o riduci il lavoro
  parallelo; non alzarlo oltre 1 GB su una macchina da 2 GB.
- `oom:…` → il processo è già stato ucciso almeno una volta: guarda `journalctl --user
  -u <unit> -n 100` e confronta con `MemoryPeak`.
- `swap` rosso su 2 GB → aggiungi zram (sezione precedente) **e** uno swapfile: senza
  swap l'OOM killer interviene appena due processi crescono insieme.
- `swappiness=60` su 2 GB → la macchina passa il tempo a spostare pagine: imposta 10.

Cosa resta comunque manuale, perché nessuno script può dimostrarlo:

1. 24–48 h di funzionamento reale con WhatsApp connesso e `MemoryPeak` che si stabilizza.
2. Un invio WhatsApp dalla dashboard e un job `healthcheck --notify` **mentre** guardi
   `MemoryCurrent`: è lo scenario in cui il cgroup include il figlio Node.
3. Sospensione/ripresa del PC: la dashboard riparte (`Restart=always`), la sessione
   WhatsApp può richiedere il QR.
4. Backup e ripristino su un'altra macchina (`backup.py create` / `restore`).
5. 10 conversazioni in parallelo e 50 messaggi in sequenza su gateway reale.

`healthcheck` aggiunge un controllo **Memoria RAM**: sotto 400 MB disponibili avvisa,
sotto 200 MB segnala critico con il rimedio, e il timer ogni 30 minuti manda l'avviso
su WhatsApp se qualcosa peggiora.

## Cosa non promette

- Non può far stare il processo Node in 100 MB: la sessione WhatsApp ha un costo fisso.
- Non può rispondere mentre la macchina è spenta o sospesa (vedi
  [`ALWAYS_ON.md`](ALWAYS_ON.md)).
- I numeri qui sopra sono misurati su questa macchina: il consumo del gateway varia con
  numero di chat, cronologia e versione di OpenClaw. `scripts/footprint.py` ti dà i
  tuoi, non quelli di una brochure.
