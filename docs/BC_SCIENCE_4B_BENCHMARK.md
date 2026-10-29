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


## Prestazioni della generazione finale (Anatomia: 77 capitoli)

La modalità **Genera Essenziale** assembla l'intera materia. Quando possibile,
riutilizza i capitoli già validati nel database e nei checkpoint, e pubblica
previsualizzazioni locali per i capitoli terminati, prima del PDF finale.

Con Qwen3.5 4B il programma può provare lotti di due capitoli *brevi* per
ridurre il numero di chiamate al modello. Controlla ogni risultato per titolo,
struttura, corrispondenza lessicale con la fonte e numeri d'esame. Se un lotto
non supera i controlli, ripiega sul metodo singolo e non riprova altri lotti
nella stessa esecuzione. I capitoli lunghi vengono sempre generati singolarmente.

**Confronto A/B locale consigliato:** prova la generazione su una materia di
prova con e senza lotti, controlla i tempi e confronta i capitoli con le
dispense originali. Per disattivare i lotti e usare solo il percorso singolo:

\`\`\`powershell
$env:BC_SCIENCE_ESSENTIAL_BATCH = "1"
& "$repo\.venv\Scripts\python.exe" -m bc_science.ui --folder $materiali
\`\`\`

Per ripristinare il comportamento automatico, chiudi BC Science e rimuovi
la variabile dalla stessa sessione PowerShell:

\`\`\`powershell
Remove-Item Env:\BC_SCIENCE_ESSENTIAL_BATCH -ErrorAction SilentlyContinue
\`\`\`

La modalità a lotti non assicura un miglioramento costante: il tempo dipende
dalla lunghezza dei capitoli, dalla riuscita della validazione e dalla GPU.
Non confondere i test unitari con una misura della qualità scientifica reale.
