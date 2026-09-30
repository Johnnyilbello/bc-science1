# BC Science

Assistente di studio **local-first** per Scienze Motorie eCampus 2026/2027.

BC Science importa PDF, DOCX, TXT, Markdown, cartelle e ZIP, crea riassunti semplici ma completi, indicizza semanticamente i materiali e permette di fare domande usando i propri documenti come fonte primaria.

## Installazione con un solo comando

Apri **PowerShell** su Windows 10/11 ed esegui:

    irm https://raw.githubusercontent.com/Johnnyilbello/bc-science1/main/install.ps1 | iex

L'installer:

- rileva Python e lo installa tramite winget se manca;
- rileva Ollama e usa l'installer ufficiale se manca;
- avvia e verifica il servizio locale Ollama;
- rileva RAM, CPU e GPU NVIDIA/VRAM;
- sceglie automaticamente il modello più adatto;
- installa il modello di generazione necessario;
- installa `qwen3-embedding:0.6b` per la ricerca semantica multilingue;
- prepara ambiente virtuale e dipendenze Python;
- prova a installare Tesseract OCR per PDF scannerizzati;
- configura cache, indice locale e cartelle dati;
- crea il comando globale `bc-science`;
- esegue una diagnostica finale.

L'installer può essere rilanciato per aggiornare BC Science: conserva configurazione, indice e cache.

## Primo utilizzo

### Riassumere un intero corso o ZIP in un unico file

    bc-science summarize "C:\Studio\FISIOLOGIA UMANA E DELLO SPORT.zip" --single

La modalita `--single` crea un vero **riassunto unico del corso**:

1. riconosce automaticamente lezioni numerate dello stesso argomento;
2. fonde le dispense duplicate o complementari per argomento;
3. genera un capitolo semplice ma completo per ogni argomento con il modello Standard 4B;
4. conserva definizioni, numeri, classificazioni, meccanismi, sequenze ed eccezioni;
5. costruisce una mappa della materia e un ripasso globale;
6. unisce tutto in un solo Markdown senza ricomprimere i capitoli finali.

Questo evita l'effetto "127 mini-riassunti incollati" e riduce il rischio che un'ultima
compressione perda contenuti utili all'esame.

Output predefinito:

    %LOCALAPPDATA%\BCScience\outputs\<corso>-riassunto-unico.md

Durante la generazione BC Science mostra l'argomento in elaborazione. La versione 0.5, quando un capitolo raggiunge il limite token, lo rigenera interamente con un budget maggiore invece di concatenare continuazioni. Questo evita loop e duplicati. Alla fine mostra documenti coperti, argomenti, generazioni, cache, tempo totale, tempo Ollama, token generati, token/s medi e retry anti-troncamento.

### Rifinire rapidamente l'ultimo riassunto

Dopo una generazione completa puoi migliorare struttura, chiarezza e duplicati senza rileggere
tutti i PDF:

    bc-science refine

Il comando seleziona automaticamente l'ultimo `*-riassunto-unico.md`, usa il modello Standard
4B e riscrive i capitoli uno per uno usando **il riassunto esistente come unica fonte**.

Output:

    %LOCALAPPDATA%\BCScience\outputs\<corso>-riassunto-rifinito.md

Per scegliere manualmente un file:

    bc-science refine --input "C:\percorso\riassunto.md"

La rifinitura e piu rapida di una nuova analisi dei PDF e corregge ripetizioni, heading incollati,
frasi poco chiare e refusi. Non puo pero recuperare informazioni che il riassunto sorgente aveva
omesso: per la massima copertura resta disponibile `summarize ... --single`.

### Indicizzare i materiali

    bc-science ingest "C:\Studio\FISIOLOGIA UMANA E DELLO SPORT.zip"

I file invariati vengono saltati automaticamente.

### Fare domande sui propri materiali

    bc-science ask "Spiegami in modo semplice il ruolo del calcio nella contrazione muscolare"

Per impostazione predefinita BC Science usa il routing automatico:
- domanda rapida: `qwen3.5:2b` (Turbo);
- `--deep`: `qwen3.5:4b` (Standard);
- `summarize`: `qwen3.5:4b` (Standard).

La risposta viene mostrata in streaming. Alla fine BC Science indica token/s, token generati,
dimensione del prompt e tempi di caricamento/generazione restituiti da Ollama.

Per una risposta più lunga e con più fonti:

    bc-science ask "Spiegami tutta la contrazione muscolare" --deep

Per forzare manualmente un profilo:

    bc-science ask "Spiegami il sarcomero" --profile standard

### Benchmark locale

Per confrontare sul proprio PC il profilo Turbo (2B) con Standard (4B) sulla stessa domanda:

    bc-science benchmark "Spiegami la contrazione muscolare"

BC Science installa il modello Turbo solo se manca, esegue entrambi i test, mostra token/s,
token generati e tempo totale, e salva anche le due risposte complete in:

    %LOCALAPPDATA%\BCScience\benchmarks\

### Diagnostica

    bc-science doctor

## Profili di velocità

BC Science seleziona automaticamente il profilo standard in base all'hardware.

| Profilo | Modello tipico | Uso |
| --- | --- | --- |
| Turbo | `qwen3.5:2b` | massima velocità |
| Standard | `qwen3.5:4b` | equilibrio velocità/qualità |
| Quality | `qwen3.5:9b` su hardware adeguato | argomenti più complessi |

Su macchine con meno di 6 GB di RAM può essere usato `qwen3.5:0.8b` per evitare blocchi o swap eccessivo.

Per installare un modello di un profilo non ancora presente:

    bc-science models install quality

## Motore Scienze Motorie

Il knowledge pack integrato riconosce e adatta il riassunto a domini come:

- Anatomia umana
- Fisiologia umana e dello sport
- Biomeccanica e chinesiologia
- Teoria e metodologia dell'allenamento
- Controllo e apprendimento motorio
- Psicologia dello sport
- Pedagogia e attività motoria
- Nutrizione e metabolismo
- Medicina e prevenzione nello sport
- Metodologia della ricerca e statistica

La regola centrale è: **semplificare senza eliminare contenuti potenzialmente utili all'esame**.

Definizioni, classificazioni, meccanismi, sequenze, eccezioni e valori numerici vengono preservati.
Il riassunto del corso usa le dispense importate come fonte primaria e non aggiunge correzioni
scientifiche esterne in modo silenzioso: eventuali verifiche scientifiche vanno tenute separate
dal contenuto da studiare per l'esame.

## Perché è veloce

- PyMuPDF estrae direttamente il testo dai PDF.
- OCR viene tentato solo sulle pagine con pochissimo testo estraibile.
- I documenti vengono divisi in blocchi controllati.
- Ogni riassunto intermedio viene salvato in SQLite.
- Se un blocco non cambia non viene rigenerato.
- L'indice semantico viene aggiornato solo per i file modificati.
- Ollama mantiene il modello caricato tra richieste ravvicinate.
- Il reasoning del modello viene disattivato durante i riassunti.

La prima elaborazione di una materia è quindi quella più costosa; le elaborazioni successive possono riutilizzare gran parte del lavoro già fatto.

## Privacy

Dopo il setup iniziale, generazione, embedding, indice e cache funzionano in locale tramite Ollama. I materiali di studio non devono essere inviati a un servizio cloud per essere riassunti.

## Formati supportati

- PDF
- DOCX
- TXT
- Markdown
- ZIP contenenti i formati sopra
- cartelle con sottocartelle

## Dati locali

Per impostazione predefinita:

    %LOCALAPPDATA%\BCScience

La cartella contiene configurazione, ambiente virtuale, database/cache, import ZIP e output.

## Sviluppo

    python -m venv .venv
    .\.venv\Scripts\activate
    python -m pip install -e ".[dev]"
    pytest -q
    ruff check .

## Stato

Versione corrente: **0.5.0**.
