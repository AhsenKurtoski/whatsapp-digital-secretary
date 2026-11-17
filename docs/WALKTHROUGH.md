# Walkthrough da 60 secondi: storyboard e registrazione

Il video non è incluso nel repository perché mostra account e dati reali. Questo
file è il kit completo per registrarlo in una volta sola sul tuo computer.

## Requisiti

- installazione già funzionante (dashboard su `http://127.0.0.1:8787`)
- telefono di test con WhatsApp
- `ffmpeg` installato (`sudo apt install ffmpeg`)
- registratore schermo: OBS Studio, GNOME Screencast o `wf-recorder`

## Storyboard (60 s, 6 scene da 10 s)

| Tempo | Schermata | Cosa fare | Testo in sovrimpressione |
|---|---|---|---|
| 0–10 s | Terminale | `bash scripts/verify.sh` e `VERIFICA OK` | "Installazione verificata" |
| 10–20 s | WhatsApp | «Ciao, vorrei un preventivo per un sito» | "Gina risponde in pochi secondi" |
| 20–30 s | WhatsApp | il cliente conferma le voci | "Nessun prezzo inventato" |
| 30–40 s | Dashboard → Approvazioni | mostra la richiesta e il PDF | "Il titolare approva" |
| 40–50 s | Casella email | l'email con PDF allegato arriva | "Email solo dopo approvazione" |
| 50–60 s | Dashboard → Statistiche | KPI e quote AI | "Tutto sotto controllo" |

## Registrazione

```bash
# Registra lo schermo (Wayland/GNOME)
wf-recorder -f walkthrough-raw.mp4

# Oppure OBS: 1920x1080, 30 fps, salva in walkthrough-raw.mp4
```

Montaggio e GIF:

```bash
# Taglia e comprimi il video
ffmpeg -i walkthrough-raw.mp4 -t 60 -vf "scale=1280:-2,fps=30" -c:v libx264 -crf 23 \
  -pix_fmt yuv420p docs/media/walkthrough.mp4

# GIF ottimizzata (palette a 128 colori, ~6 MB per 60 s)
ffmpeg -i docs/media/walkthrough.mp4 -vf "fps=12,scale=960:-2:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse" \
  -loop 0 docs/media/walkthrough.gif
```

## Embed nel README

Dopo aver creato il file, aggiungi in `README.md` e `README.it.md`:

```markdown
![Walkthrough di Gina](docs/media/walkthrough.gif)
```

## Regole di privacy

- Usa solo il numero e la casella di test (vedi [`E2E.md`](E2E.md)).
- Non mostrare token, chiavi, numeri di clienti reali o il contenuto di `secrets.env`.
- Se registri la dashboard, apri un preventivo con dati inventati e cancella il video
  se contiene dati veri.
