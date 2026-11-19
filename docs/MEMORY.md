# Memoria per cliente

La memoria di Gina è un file JSON per contatto sotto `memory/contacts/`
(`memory.py`). Non contiene segreti, i transcript sono redatti prima del salvataggio
(email, numeri di telefono e IBAN diventano `[email]`, `[phone]`, `[iban]`), e ogni
file ha permessi `0600` con scrittura atomica.

## Cosa contiene

| Campo | Uso |
| --- | --- |
| `name`, `language`, `tone` | Come chiamare il cliente e in che lingua rispondere |
| `facts` | Fatti duraturi confermati (`servizio`, `budget`, `email`, …) |
| `fact_meta` | Data dell'ultimo aggiornamento di ogni fatto |
| `intents` | Conteggio degli intenti riconosciuti |
| `max_score` | Punteggio lead più alto osservato |
| `handoff` | Presa in carico umana con operatore e data |
| `transcript` | Ultimi 300 messaggi redatti |
| `messages_in`, `messages_out`, `first_seen`, `last_seen` | Statistiche del contatto |

## Come viene usata nel prompt

`memory.context(phone, query=...)` produce un blocco compatto con lingua, nome,
intenti prevalenti e i fatti **più pertinenti** al messaggio corrente, non i primi
salvati. L'ordinamento è:

1. sovrapposizione di parole tra il messaggio del cliente e `chiave + valore`;
2. data di ultimo aggiornamento del fatto (più recente prima).

Seguono gli ultimi sei scambi. Il contesto è limitato a 900 caratteri per non
consumare token inutilmente.

## Aggiornamento dei fatti

`remember()` normalizza la chiave (`[a-z0-9_]`, massimo 40 caratteri), aggiorna il
valore, sposta il fatto in cima e registra `fact_meta[chiave].updated_at`. Un fatto
aggiornato non può quindi essere scartato al posto di uno vecchio quando si supera il
limite di 40: il limite elimina sempre i fatti meno recenti.

Un messaggio identico consecutivo (stesso ruolo e stesso testo) non viene duplicato:
aggiorna solo l'orario. Questo evita di gonfiare il transcript con i retry del
gateway.

## Ricerca

```bash
python3 memory.py --search "e-commerce"
```

Cerca in tutte le memorie, in fatti e messaggi, con i dati personali redatti e i
risultati ordinati dal più recente. La stessa vista è disponibile nella dashboard:
nella scheda **Conversazioni** il pannello del transcript mostra i fatti memorizzati
del cliente (`/api/conversations/<numero>` restituisce `facts`).

## Diritto all'oblio

```bash
python3 memory.py --forget +393331112222 --yes
```

Elimina il file del contatto: fatti, transcript, handoff e statistiche. L'operazione è
idempotente e restituisce `False` se il contatto non esiste più.

## Test

```bash
python3 memory.py --self-test
python3 -m unittest discover -s tests -p 'test_gina_platform.py' -v
python3 -m unittest discover -s tests -p 'test_cli_surfaces.py' -v
```

I test coprono: ciclo di vita completo, aggiornamento di un fatto con spostamento in
cima, pertinenza del contesto rispetto alla domanda, ricerca su più contatti,
collasso dei doppioni consecutivi, redazione di email e numeri, handoff e oblio.
