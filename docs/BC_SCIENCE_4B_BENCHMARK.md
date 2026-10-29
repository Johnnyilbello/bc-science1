# BC Science · Benchmark locale Qwen3.5 4B

L'obiettivo è distinguere **sintesi leggibile** da **omissione scientifica**.
Nessun contenuto delle dispense viene inviato a servizi cloud.

## Misurazione (1, 2 e 5)

Da PowerShell, nella cartella della repository, eseguire (sostituire i percorsi reali):

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_study.py --complete "C:\...\riassunto-unico.md" --essential "C:\...\riassunto-studio.md"
```

Misure prodotte: numero parole, compressione, target indicativo >=25% su testi lunghi,
numero di fatti critici potenzialmente omessi, segnalazioni su valori numerici e punti
d'esame. La corrispondenza dei fatti si basa su euristiche testuali e **non equivale
a una verifica scientifica certa**: controllare le segnalazioni e le dispense originali.

La compressione è un obiettivo *soft*: i capitoli brevi non vengono forzati.
La pipeline non reinserisce più automaticamente generici dettagli non essenziali
solo per raggiungere una copertura lessicale elevata.

## Prova thinking / non-thinking (3)

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_study.py --complete "C:\...\riassunto-unico.md" --essential "C:\...\riassunto-studio.md" --ab-thinking
```

Il test utilizza lo **stesso estratto** e lo **stesso modello installato** con
`think=False` e `think=True`. Stampa tempi di caricamento, token al secondo,
numero di token e lunghezza dell'output, ma non altera la configurazione di BC Science
né scrive il contenuto delle dispense in log. Non misura da solo l'accuratezza:
confrontare manualmente i testi per individuare errori e omissioni.

## Test didattico reale (5)

Scegliere almeno tre capitoli differenti (anatomia, fisiologia, biologia), inclusi
uno con formule/valori e uno con classificazioni. Per ognuno verificare:

- che non vengano eliminati definizioni e passaggi causali indispensabili;
- che Essenziale sia più scorrevole e breve di Completa senza diventare criptica;
- che le parole tecniche siano spiegate in modo semplice;
- che formule, unità e numeri rimangano fedeli alle dispense;
- che il testo non inventi dettagli assenti nelle fonti.

Registrare tempo e qualità prima di modificare parametri di inferenza.
**Non aumentare automaticamente `num_ctx` o attivare thinking di default**
sulla GPU da 8 GB senza confrontare VRAM, velocità e precisione.
