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

## Multi-materia e workspace — 0.9

BC Science può lavorare direttamente su una cartella radice che contiene una sottocartella per materia, senza creare ZIP separati.

Esempio:

    C:\\Studio\\SCIENZE MOTORIE\\
      ANATOMIA\\
      FISIOLOGIA UMANA E DELLO SPORT\\
      FONDAMENTI DI BIOLOGIA E CHIMICA\\

Scansione veloce, senza Ollama:

    bc-science courses list "C:\\Studio\\SCIENZE MOTORIE"

Stato dei workspace e differenze rispetto all'ultimo build:

    bc-science courses status "C:\\Studio\\SCIENZE MOTORIE"

La colonna Delta usa `+nuovi ~modificati -rimossi`.

Generazione o aggiornamento delle sole materie necessarie:

    bc-science courses build "C:\\Studio\\SCIENZE MOTORIE"

Per ogni materia vengono creati:

- un workspace separato in `%LOCALAPPDATA%\\BCScience\\workspaces\\courses\\...`;
- un database dedicato per indice semantico e cache di generazione;
- un manifest con hash dei documenti per rilevare modifiche;
- un solo riassunto coerente in `outputs\\courses\\<materia>\\riassunto-unico.md`.

File temporanei, nascosti, output precedenti e formati non supportati vengono esclusi dalla pipeline e conteggiati nel preflight. Se una materia non è cambiata e l'output esiste, il build la riutilizza senza richiamare Ollama. Se alcuni file sono cambiati, la materia viene aggiornata riutilizzando cache e indice dei documenti invariati.

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

La rifinitura e piu rapida di una nuova analisi dei PDF. La 0.6.3 normalizza prima la struttura Markdown e poi valida ogni capitolo: rimuove o declassa heading H2 duplicati e fonde deterministicamente più sezioni `Da ricordare per l'esame` senza perdere il testo. Poi rifiuta placeholder, recap mancanti e finali sospettamente tronchi. Solo se resta un problema sostanziale rigenera quel singolo capitolo. Inoltre il Ripasso globale viene costruito dai veri `Da ricordare` dei capitoli invece che dai soli titoli, e i mismatch fra titolo e contenuto vengono marcati come `Verifica materiale`. Non puo pero recuperare informazioni che il riassunto sorgente aveva omesso: per la massima copertura resta disponibile `summarize ... --single`.


### Controllare la comprensibilità per principianti

BC Science considera il lettore target una persona che **non ha mai studiato la materia**.

Dalla 0.8 ogni capitolo rifinito deve includere:

- In parole semplici: quadro mentale di base prima dei dettagli;
- Parole chiave: facoltative; possono essere usate quando aiutano davvero, ma non sono un requisito per superare il controllo;
- progressione dal concetto generale ai meccanismi e ai dettagli;
- frasi e paragrafi abbastanza brevi da non sovraccaricare la lettura;
- una sola sezione finale Da ricordare per l'esame.

Per misurare il risultato:

    bc-science clarity

Il comando mostra per ciascun capitolo score 0-100, parole medie per frase,
densità massima del paragrafo e gli eventuali motivi di FAIL.

bc-science refine corregge autonomamente i capitoli che non superano il controllo principiante. Prima spezza deterministicamente i paragrafi troppo densi senza cambiare le frasi; se restano problemi di comprensione, esegue fino a quattro autocorrezioni mirate del solo capitolo e rivalida dopo ogni tentativo. Dalla 0.8.7 `Parole chiave` e davvero facoltativa e non abbassa il punteggio di chiarezza quando manca. La 0.8.8 sincronizza anche il Ripasso globale quando `finalize` rinomina un capitolo etichettato erroneamente, incluso il caso reale `olfatto (titolo da verificare)`. La 0.8.9 aggiunge un proofreading finale deterministico per refusi e accordi ad alta confidenza e sincronizza titoli editoriali supportati dal contenuto, come `GUSTO E OLFATTO`, senza usare il modello e senza modificare silenziosamente fatti scientifici. Inoltre `refine` salva un checkpoint dopo ogni capitolo completato: se un capitolo fallisce il gate o il processo viene interrotto, basta rilanciare lo stesso comando per riprendere dai capitoli gia validati. Il checkpoint viene rimosso automaticamente quando la rifinitura termina con successo. bc-science finalize rifiuta di generare il PDF
se anche un capitolo non supera il gate.

Lo score è un controllo operativo, non una prova matematica di comprensione: BC Science
combina struttura obbligatoria, limiti di densità e revisione source-only invece di affidarsi
a una sola formula di leggibilità.

### Creare la dispensa finale

Dopo \`refine\`, BC Science può completare autonomamente il lavoro:

    bc-science finalize

Il comando seleziona automaticamente l'ultimo \`*-riassunto-rifinito.md\` e crea:

    %LOCALAPPDATA%\BCScience\outputs\final\<corso>-dispensa-finale.md
    %LOCALAPPDATA%\BCScience\outputs\final\<corso>-dispensa-finale.docx
    %LOCALAPPDATA%\BCScience\outputs\final\<corso>-dispensa-finale.pdf

\`finalize\` non rilegge i PDF e non usa il modello per riscrivere il corso. È una fase deterministica:

1. conserva il testo eCampus;
2. mantiene separati gli avvisi \`Verifica materiale\`;
3. corregge solo l'organizzazione di mismatch già identificati, senza inventare contenuti;
4. applica un proofreading deterministico limitato a refusi, accordi grammaticali e titoli editoriali ad alta confidenza;
5. aggiunge note scientifiche separate soltanto quando una regola verificata riconosce esattamente una formulazione problematica;
6. include la fonte NCBI/PubMed nella nota;
7. impagina il risultato in A4 e genera PDF/DOCX localmente.

Per Fisiologia il pacchetto 0.7 include note curate su ritorno venoso sistemico, gradiente del
trasporto passivo, organuli cellulari, parotidi, numero di neuroni/glia, lattato/fatica ed
emoglobina. Le frasi eCampus originali restano visibili: la nota non le sostituisce
silenziosamente.

Opzioni utili:

    bc-science finalize --no-docx
    bc-science finalize --no-pdf
    bc-science finalize --input "C:\Studio\riassunto-rifinito.md"
    bc-science finalize --output-dir "C:\Studio\Finale"

La generazione PDF usa ReportLab open-source e non richiede Microsoft Word o LibreOffice.

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

Versione corrente: **0.9.0**.


### Ripasso globale rapido

Dalla 0.6.3 il `Ripasso globale` non richiede più una lunga generazione AI. Viene costruito
deterministicamente dai veri blocchi `Da ricordare per l'esame` dei capitoli, con un richiamo
ad alta resa per ciascun argomento. La sola `Mappa della materia` resta generata dal modello,
con un budget breve e una cache stabile basata esclusivamente sui titoli.


### Finalizzazione coerente

Dalla 0.7.2 `finalize` sincronizza anche copertina, mappa, ripasso globale e indice con i fix organizzativi applicati ai capitoli. Le note scientifiche vengono inserite nei capitoli completi pertinenti, non nel front-matter sintetico.


## Roadmap

La roadmap completa è in ROADMAP.md. Le tappe principali sono:

- 0.8: comprensibilità per principianti;
- 0.9: multi-materia e workspace — implementato;
- 0.10: audit di copertura e coerenza;
- 0.11: modalità esame;
- 0.12: aggiornamento incrementale;
- 1.0: comando one-shot bc-science build.


### Chiarezza 0.8.2

Il gate di chiarezza distingue ora correttamente prosa, heading, note e liste Markdown.
Una lista di 7 elementi non viene più interpretata come un singolo paragrafo di 7 frasi.
Le liste continuano comunque a contribuire alle metriche globali di lunghezza delle frasi.


### Integrità del riassunto 0.8.3

Il gate di chiarezza controlla anche l'integrità editoriale del documento:
- nessun placeholder `rivedi il capitolo` nel Ripasso globale;
- `Parole chiave` facoltative e mai usate come blocco rigido della pipeline;
- heading Markdown sempre su una riga propria;
- `In parole semplici` come prima sottosezione e `Da ricordare per l'esame` come ultima;
- `Verifica materiale` ammessa solo per mismatch titolo/contenuto e mai come certificazione scientifica;
- il front matter (mappa + ripasso globale) partecipa al gate prima di `finalize`.

La cache di refine usa una nuova versione di prompt, quindi il primo refine dopo l'aggiornamento
rigenera i capitoli invece di riutilizzare output 0.8.2 che potrebbero contenere difetti già noti.


### Autocorrezione qualità 0.8.4

Il controllo editoriale non si ferma più dopo una sola rigenerazione. Se restano problemi
come sovraccarico di parole chiave, certificazioni scientifiche non ammesse o struttura
non conforme, `refine` esegue fino a tre autocorrezioni qualità mirate prima di arrendersi.

Il `Ripasso globale` limita inoltre la lunghezza di ogni richiamo: anche quando
`Da ricordare per l'esame` è scritto come un lungo paragrafo invece che come elenco,
il front matter non può più essere invaso da centinaia di parole per un singolo capitolo.


### Chiarezza 0.8.5

La sezione `Parole chiave` non è più obbligatoria e il numero di termini non è più un
quality gate. Il controllo valuta invece ciò che conta davvero per un principiante:
introduzione semplice, ordine logico, lunghezza delle frasi, densità dei paragrafi,
integrità Markdown e completezza strutturale.

Questo evita rigenerazioni inutili come un capitolo valido che produce 9 parole chiave
invece di 8.

La roadmap 0.9 introduce inoltre la modalità multi-materia da cartella radice:
`bc-science courses build "C:\Studio\SCIENZE MOTORIE"` dovrà rilevare automaticamente
le sottocartelle-materia e produrre un riassunto unico per ciascuna senza richiedere ZIP/RAR.


### Normalizzazione deterministica 0.8.6

Il gate principiante e il normalizzatore condividono ora gli stessi confini strutturali.
Prima di richiamare il modello, BC Science:

- stacca automaticamente gli heading Markdown rimasti alla fine di una frase;
- tratta heading, liste e blockquote come confini separati;
- spezza soltanto i blocchi di prosa realmente troppo densi;
- conserva ordine e contenuto delle frasi;
- evita loop di quattro riscritture quando il difetto è soltanto di layout.

Questo corregge il caso reale in cui un capitolo continuava a segnalare contemporaneamente
un heading inline e un paragrafo da 7 frasi nonostante le autocorrezioni AI.
