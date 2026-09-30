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

Output predefinito:

    %LOCALAPPDATA%\BCScience\outputs\

### Indicizzare i materiali

    bc-science ingest "C:\Studio\FISIOLOGIA UMANA E DELLO SPORT.zip"

I file invariati vengono saltati automaticamente.

### Fare domande sui propri materiali

    bc-science ask "Spiegami in modo semplice il ruolo del calcio nella contrazione muscolare"

La risposta viene mostrata in streaming. Alla fine BC Science indica token/s, token generati,
dimensione del prompt e tempi di caricamento/generazione restituiti da Ollama.

Per una risposta più lunga e con più fonti:

    bc-science ask "Spiegami tutta la contrazione muscolare" --deep

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

Definizioni, classificazioni, meccanismi, sequenze, eccezioni e valori numerici vengono preservati. Eventuali chiarimenti generali devono rimanere separati dal contenuto della fonte.

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

Versione iniziale: **0.1.0**.
