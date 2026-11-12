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

## Due modalità di studio

Il comando `courses build` genera due documenti per ogni materia:

- **Completa** (`riassunto-unico.md` e `.pdf`): spiegazione progressiva a partire dalle dispense, con un confronto automatico dei singoli blocchi originali per recuperare possibili omissioni.
- **Essenziale** (`riassunto-studio.md` e `.pdf`): ripasso dei concetti principali per chi ha già studiato il documento completo. L'audit segnala eventuali differenze e tenta di recuperare i fatti rilevanti.

Il confronto è effettuato da un modello linguistico locale: può commettere errori o non riconoscere qualche omissione, e non certifica la correttezza scientifica delle fonti. I documenti con testo non estraibile richiedono OCR e verifica del risultato. Argomenti molto estesi possono richiedere la suddivisione della fonte.

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
- il riassunto completo verificato in `outputs\\courses\\<materia>\\riassunto-unico.md/.pdf`;
- una versione studio deduplicata e più compatta in `outputs\\courses\\<materia>\\riassunto-studio.md/.pdf`.

File temporanei, nascosti, output precedenti e formati non supportati vengono esclusi dalla pipeline e conteggiati nel preflight. Se una materia non è cambiata e l'output esiste, il build la riutilizza senza richiamare Ollama.

Dalla **0.9.1**, se la sola modifica consiste nell'aggiunta di nuovi documenti, BC Science usa un aggiornamento incrementale: indicizza e analizza soltanto i file aggiunti, li associa a un capitolo esistente tramite titolo normalizzato e, quando necessario, routing content-aware; poi fonde il nuovo materiale nel solo capitolo coinvolto. Il `Ripasso globale` e l'indice vengono ricostruiti deterministicamente, mentre la Mappa della materia viene rigenerata soltanto se nasce un nuovo capitolo. Un gate anti-perdita verifica che i punti d'esame già presenti non scompaiano; se il merge non è sicuro, il sistema esegue automaticamente un rebuild completo.

File già esistenti **modificati** o **rimossi** continuano volutamente a usare il rebuild completo della materia: un aggiornamento puramente additivo non può sapere quali informazioni della vecchia versione debbano essere eliminate.

## Confronto reale MiniCPM — falsi positivi logici e JSON

Primo test locale, **stesso capitolo di Anatomia, 12 affermazioni**:
Qwen3.5:4B → 12 `UNVERIFIED`, nessuna contraddizione.
MiniCPM5-2B → 11 `UNVERIFIED`, una `SUSPECTED_CONTRADICTION`.
La singola segnalazione MiniCPM riguardava una **falsa contraddizione
logica**: «A serve esclusivamente per X» non significa «solo A serve
per X»; l'uso di X da parte di B non nega la funzione esclusiva di A.
Nessuno dei due modelli ha quindi dimostrato di individuare gli errori
scientifici nuovi in questo test. Le citazioni non letterali e il JSON
incompleto causano molte astensioni.

Per proteggere i futuri audit il programma usa **JSON mode nativo di
Ollama** (solo per i verificatori, non per le generazioni di studio),
conforme alla documentazione ufficiale Ollama. Ogni prima etichetta
`CONTRADICTED` passa a un **secondo controllo logico separato**:
deve decidere se entrambe le proposizioni possono essere vere nello
stesso contesto, citare letteralmente una controprova sul medesimo
soggetto ed esplicitare la parte incompatibile. Un disaccordo, un
modello non verificabile o una citazione su un soggetto diverso
lasciano lo stato `UNVERIFIED`; i campi diagnostici spiegano lo scarto.
Questa verifica resta fallibile, anche se entrambi i turni del modello
concordano. Le contraddizioni segnalate sono **sospette**, mai certe.
Non pubblicare o riscrivere materiali senza validazione delle prove.

## Audit originale a ID — esperimento V7 senza citazioni inventate

Il V6 del capitolo **Fisiologia · Capacità polmonari** ha prodotto
10/12 `UNVERIFIED` con Qwen3.5:4B e 12/12 con MiniCPM5-2B;
nessuna contraddizione confermata. Le citazioni originali recitate
dal modello restano frequentemente inesatte o incomplete.

Per isolare questo problema, `auto-audit-claims` dispone ora della
**modalità sperimentale opzionale** `--evidence-mode spans`.
BC Science seleziona **passaggi brevi e letterali** dalle dispense
originali già verificate dal cache SHA256 e li numera. Il modello
seleziona `source_ids`, anziché inventare o riscrivere citazioni.
Il codice ricostruisce il testo esatto e la provenienza dell'estratto;
gli ID non sono prove di verità: la conferma resta provvisoria e passa
per il filtro conservativo di copertura, mentre le contraddizioni
richiedono una nuova verifica logica con citazioni testuali.
Assenza di passaggi, ID sconosciuti, risposta invalida o prove
insufficienti => `UNVERIFIED`. Nulla viene pubblicato.

Esempio locale (sostituire con il proprio percorso delle dispense):

~~~powershell
bc-science courses auto-audit-claims "C:\Studio\SCIENZE MOTORIE" --subject "ANATOMIA" --chapter "Apparato digerente (Faringe ed esofago)" --edition Completa --max-claims 12 --model qwen3.5:4b --evidence-mode spans --output-dir "C:\BC-Science1\reports\autonomous-claims\spans-qwen"
~~~

Ripetere con `--model openbmb/minicpm5-2b` in **un'altra nuova
directory**. Il vecchio percorso resta disponibile con
`--evidence-mode legacy`, che è ancora il default. Confrontare
`report.json`, in particolare `diagnostic`,
`evidence_spans_count`, `used_span_ids`, `quotes` e
`independent_logical_review`. La migliore formattazione delle prove
NON dimostra che il modello rilevi autonomamente più errori scientifici:
occorre una valutazione indipendente su materiali e capitoli nuovi.

## Audit V8 — passaggi contestuali e diagnosi delle astensioni

La prima fase sperimentale V8 migliora soltanto **la presentazione delle prove**:
se il testo PDF estratto non contiene punteggiatura, il selettore usa finestre
sovrapposte fino a 640 caratteri con confini tra parole, anziché tagliare
la spiegazione in frammenti di 420 caratteri. Per testi lunghi suddivisi
in frasi, tenta anche di offrire due frasi contigue senza riscriverle.
Ogni candidato deve comunque superare `validate_original_quote`.

In `report.json`, ogni affermazione contiene ora `span_candidates`,
ossia gli estratti **effettivamente offerti al modello**. Sono visibili
anche quando il modello risponde `UNVERIFIED` con `quotes: []`.
Questa aggiunta serve a distinguere fonti non pertinenti da astensioni
prudenziali; gli span proposti **non sono prove approvate**.

Il filtro lessicale conservativo e il secondo controllo sulle
contraddizioni non sono stati allentati. Continuano a essere necessarie
la verifica delle quantità, delle unità, del soggetto, delle condizioni
temporali e della relazione logica; il sistema può ancora non riconoscere
parafrasi vere o accettare un supporto solo provvisorio. I report restano
isolati e non modificano né pubblicano riassunti.

Per confrontare V7 e V8, eseguire nuovamente `courses auto-audit-claims`
con `--evidence-mode spans`, **lo stesso capitolo e lo stesso modello**,
sempre con una nuova `--output-dir`; valutare anche quali candidati
siano realmente completi e se eventuali supporti siano scientificamente
giustificati, non soltanto il numero di etichette positive.

## V8.2 — supporto provvisorio con doppio controllo locale

Nei report V8.1 di Fisiologia, entrambi i modelli hanno talvolta
assegnato `PROVISIONALLY_SUPPORTED` con citazioni incomplete e una
motivazione che ammetteva la mancanza di prove. Per questo, quando il
primo modello indica `SUPPORTED` e supera il controllo lessicale,
V8.2 richiede **un secondo giudizio semantico locale** sulle citazioni
già selezionate, con identificativi di fonte e parti mancanti.

Una risposta incompleta, invalida, contraddittoria o un dubbio esplicito
nella motivazione iniziale mantengono `UNVERIFIED`. Il secondo giudizio
proviene dallo **stesso modello**; non costituisce una verifica
scientifica indipendente e non autorizza correzioni/pubblicazioni.
Un esito positivo resta soltanto `PROVISIONALLY_SUPPORTED`.

Il report espone `support_challenge`, `lexical_coverage_passed`
e `model_schema_errors` per rendere diagnosticabili i blocchi
(soprattutto `MODEL_SCHEMA_INVALID`). Le vecchie diagnostiche V7/V8.1
non vengono retroattivamente modificate. Il secondo passaggio usa
un'ulteriore chiamata al modello locale solo sulle possibili conferme,
per cui può aumentare il tempo dell'audit.

Valutare questa versione confrontando gli stessi 12 casi e controllando
anche le false conferme: un minor numero di `PROVISIONALLY_SUPPORTED`
può essere un miglioramento quando le prove non bastano.

## Esito A/B V5 e recupero prudente delle prove

Due report Markdown dello stesso capitolo di Anatomia, 12 frasi ciascuno,
indicano **12 UNVERIFIED su 12** in entrambe le esecuzioni. Nel report
che conteneva il falso allarme sulla rinofaringe il controllo logico
ha riconosciuto che le affermazioni sono compatibili, ma **non** ha
individuato in modo dimostrato l'errore `dallo stomaco allo stomaco`.
Molte risposte iniziali sono state scartate perché citavano
passaggi che **non erano presenti letteralmente** nella dispensa;
la modalità JSON nativa non risolve l'allucinazione di citazioni.

Il nuovo tentativo di analisi, valido per qualunque materia, **non
accetta** tali citazioni inventate. Se il modello ha indicato un
`source_id` realmente recuperato, il codice può estrarre
automaticamente una breve frase letterale *candidata* dall'estratto
originale, confrontando genericamente le parole per la selezione.
Questa operazione **non dimostra alcuna affermazione**:
`source_pointer_is_proof: false`, `model_label: INVALID`.
Solo quando l'etichetta iniziale è `CONTRADICTED`, una seconda analisi
logica può produrre una `SUSPECTED_CONTRADICTION` a condizione di
confermare l'incompatibilità con passaggi letterali e sul medesimo
soggetto. Ogni altra risposta rimane `UNVERIFIED`.
Il report ora espone anche `raw_model_label`,
`source_pointer_candidates` e `source_pointer_review`, permettendo
di misurare le citazioni inaccurate senza promuoverle a prove.

Questo è un esperimento diagnostico read-only, non una correzione
autonoma convalidata. Non viene cambiato alcun riassunto o PDF
pubblicato, e non viene inviato nulla a Nemotron.

## MiniCPM5-2B e confronto con Qwen3.5-4B

**MiniCPM5-2B** è disponibile ufficialmente in Ollama con tag
`openbmb/minicpm5-2b` (GGUF Q4_K_M, circa 1,6 GB).
Benchmarks diffusi da OpenBMB: media 53,9 contro 51,1 del Qwen3.5-4B;
tuttavia Qwen mantiene vantaggio su benchmark di conoscenza come
MMLU-Pro e GPQA Diamond. Questo **non** dimostra la superiorità del
nuovo modello nella verifica dei fatti scientifici in italiano.

Per installarlo manualmente nel runtime locale:

~~~powershell
ollama pull openbmb/minicpm5-2b
ollama list
~~~

Il comando sperimentale di verifica delle affermazioni ora accetta
`--model openbmb/minicpm5-2b`, lasciando invariato il modello principale
delle dispense. Confrontare **stessa materia, capitolo, edizione e numero
di affermazioni** con `--model qwen3.5:4b`, producendo due report nuovi.
Misurare i falsi supporti, le astensioni, le contraddizioni, le
citazioni non valide e la qualità del ragionamento, non solo la quantità
di `PROVISIONALLY_SUPPORTED`.

È stato inoltre aggiunto `courses dual-agent-autofix`, che usa
**MiniCPM5-2B locale di default** e Nemotron NVIDIA come revisore
indipendente per proporre modifiche in cartelle isolate. Il modello
locale è sostituibile con `--local-model qwen3.5:4b`. L'uso cloud
**richiede** una chiave tramite variabile d'ambiente `NVIDIA_API_KEY`
e un consenso esplicito `--allow-cloud-excerpts`: brevi estratti
didattici vengono inviati a NVIDIA. Non usare per materiale privato o
sensibile senza autorizzazione. Endpoint di prototipazione con
disponibilità/limiti da verificare, nessuna promessa di gratuità
illimitata; nessun fallback a provider a pagamento.

Il coordinatore non lancia comandi, non modifica il codice dell'app e
non salva PDF o riassunti pubblicati. Crea proposte Markdown, diff,
hash e report non pubblicati. I modelli possono sbagliare entrambi.
L'assenza di prove originali verificabili non autorizza correzioni.
Le iterazioni sono limitate e le richieste cloud non vengono ripetute
automaticamente. PR di sviluppo lasciata in draft.

## Audit autonomo sperimentale delle affermazioni — Qwen locale

I quattro errori conosciuti (esofago, vie biliari, palmitato, spirometria)
hanno evidenziato il limite delle regole statiche: non insegnano al
sistema a riconoscere contraddizioni nuove in altre materie.

Il comando seguente è un **pilota generico offline**. Campiona
affermazioni del capitolo senza hardcodare risposte scientifiche,
con rappresentanza di quantità, negazioni e relazioni; recupera estratti
dalle dispense originali nel cache SHA256; Qwen 4B confronta affermazione
e fonti e deve motivare ogni giudizio con citazioni letterali. Il codice
verifica che testo, percorso e pagina/blocco citati esistano davvero.

**Correzione dopo il primo collaudo reale:** Qwen aveva etichettato come
`SUPPORTED` una frase di Anatomia con un percorso assurdo, citando solo
che l'organo era un tubo muscolare. Una citazione letterale non è di per sé
una prova del significato dell'intera affermazione. Adesso ogni risposta
`SUPPORTED` supera anche un controllo prudente e generico di copertura
delle ancore fattuali (valori, parole importanti, negazioni). Se mancano
elementi, è declassata a `UNVERIFIED` e viene avviata una seconda
verifica critica autonoma sul modello locale. Questa cerca componenti
non dimostrate e, solo con una controcitazione letterale verificata,
può registrare una `SUSPECTED_CONTRADICTION`. Anche i possibili percorsi
circolari vengono segnalati come indizio da rivedere, non come errore
scientifico certo. Il controllo prudente può produrre falsi negativi
quando la fonte usa sinonimi: non va interpretato come verifica semantica
completa, né può autorizzare pubblicazione o correzioni automatiche.


~~~powershell
bc-science courses auto-audit-claims "C:\Studio\SCIENZE MOTORIE" --subject "ANATOMIA" --chapter "Apparato digerente (Faringe ed esofago)" --edition Completa --max-claims 8 --output-dir "C:\BC-Science1\reports\autonomous-claims\pilot-anatomia"
~~~

Per analizzare una bozza non pubblicata, aggiungere
`--edition Anteprima --preview-dir "C:\BC-Science1\reports\essential-preview\sessione"`.

`PROVISIONALLY_SUPPORTED` indica solo sostegno nella fonte locale,
non verità scientifica. `SUSPECTED_CONTRADICTION` è una possibile
contraddizione da rivedere. `SOURCE_CONFLICT` significa che passaggi
originali sono discordanti. `UNVERIFIED` significa prove insufficienti,
citazioni inventate o risposta non validabile.

I PDF originali possono contenere errori: NON è validazione scientifica
indipendente né una soluzione generale per correggere le dispense.
Ollama deve girare solo su localhost con `qwen3.5:4b` già installato.
Nessuna chiamata cloud o API a pagamento, nessun modello scaricato.
Produce `report.json` e `report.md` in una directory nuova e isolata.
Il campione è limitato a 1–30 affermazioni per esecuzione. L'assenza di
flag in un campione **non** permette la pubblicazione. Per integrare
l'audit nella generazione occorre prima misurare sul corpus reale
richiamo degli errori, falsi positivi e qualità delle citazioni.

**Collaudo V2:** 12 affermazioni, due `PROVISIONALLY_SUPPORTED` e dieci
`UNVERIFIED`. Il caso di esofago è bloccato, ma molte parafrasi
legittime sono respinte dalla copertura lessicale (`dietro` vs
`posteriore`, `cm` vs `centimetri`). Il secondo revisore è stato
esteso a una lista di **componenti semantiche** (`facets`) con prove
attribuite e citazioni verificabili. L'eventuale
`semantic_support_candidate` è soltanto un'indicazione sperimentale:
non muta lo stato `UNVERIFIED` e non autorizza nuove pubblicazioni.

## Bozze di correzione scientifica con controllo SHA256 — NON pubblicate

Dopo aver prodotto il report `inspect-scientific-flags` sulle tre
edizioni, creare proposte **isolate** per le formulazioni errate documentate:

```powershell
bc-science courses draft-scientific-corrections "C:\Studio\SCIENZE MOTORIE" --audit-json "C:\BC-Science1\reports\scientific-flags\audit-con-anteprime.json" --preview-dir "C:\BC-Science1\reports\essential-preview\sessione" --output-dir "C:\BC-Science1\reports\scientific-correction-drafts\sessione"
```

Per ogni documento interessato il comando ricava il percorso dalla cartella
materie, non da un percorso arbitrario indicato nel report; verifica che SHA256
e testo corrente corrispondano all'audit e limita ogni sostituzione al **capitolo
indicato dall'audit**. Consente da una a cinque ripetizioni identiche dell'errore
nello stesso capitolo, non interviene negli altri capitoli e blocca i casi
ambigui o non riconoscibili.
Prepara file `proposta-NN.md`, `proposta-NN.diff`,
`review-report.md` e `review-report.json` in una cartella nuova.

Le proposte trattano soltanto quattro formulazioni accertate su questo
corpus (esofago, vie biliari, palmitato e volume corrente). Il caso di
spirometria sostituisce **l'intera frase errata**, senza lasciare in coda
un frammento di testo che ne contraddice il significato. Non sono una
correzione generica di tutta la fisiologia, anatomia e biochimica. Se il
documento è cambiato o il passaggio non è univoco, lo stato è `BLOCKED`.
Lo stato `REVIEW` significa **proposta da confrontare** con la dispensa
originale e una fonte scientifica indipendente, non validazione.

Il comando **non** scrive sopra riassunti, PDF, indici, manifest, cache,
né sulle fonti originali. Non elimina i warning degli audit esistenti.
Nessuna proposta può essere pubblicata automaticamente da questa CLI.

## Segnalazione di affermazioni scientifiche sospette (read-only)

Dopo un confronto tra i riassunti pubblicati e le dispense originali
è emerso che un'affermazione numerica errata può essere introdotta nella
**Completa** e propagata nell'**Essenziale** conservando tutti i numeri.
I gate di copertura numerica non possono certificarne la correttezza.

La CLI include un controllo prudente, offline e **non esaustivo** per un
piccolo elenco di errori già verificati con fonti scientifiche:

```powershell
bc-science courses inspect-scientific-flags "C:\Studio\SCIENZE MOTORIE" --output-json "C:\BC-Science1\reports\scientific-flags\manual-review.json"
```

Per includere anche **bozze non pubblicate** generate dal comando
`courses preview-essential`, indicare la directory che contiene il
`preview-report.json` originale:

```powershell
bc-science courses inspect-scientific-flags "C:\Studio\SCIENZE MOTORIE" --preview-dir "C:\BC-Science1\reports\essential-preview\20261110-155540" --output-json "C:\BC-Science1\reports\scientific-flags\complete-and-preview.json"
```

I risultati delle bozze sono etichettati `Anteprima` e **non vengono
confusi** con `Completa` o `Essenziale`. La CLI rifiuta anteprime esterne
alla cartella del report e manifest riferiti ad altri corsi. In particolare,
l'errore di spirometria riscontrato nella bozza di Fisiologia non era
nell'elenco precedente perché quel controllo leggeva soltanto i sei
documenti pubblicati: non equivale necessariamente a un falso negativo.

Il report contiene materia, edizione, capitolo, stringa sospetta, hash del
documento, regola e fonte esterna quando disponibile. Nessuna modifica a
`riassunto-unico.md`, `riassunto-studio.md`, ai PDF, alle dispense o al manifest.
L'assenza di segnalazioni **non** significa che un riassunto sia corretto.
Un alert richiede riscontro manuale, non è una correzione automatica.

Esempio scientifico: la sintesi del palmitato impiega **1 acetil-CoA
iniziale + 7 malonil-CoA + 14 NADPH** nella reazione FAS; includendo
la produzione dei malonil-CoA, il totale è **8 acetil-CoA + 7 ATP
+ 14 NADPH**. Riferimenti:
[Reactome](https://reactome.org/content/detail/R-HSA-75872),
[Lipid Metabolism (PMC)](https://pmc.ncbi.nlm.nih.gov/articles/PMC8411952/).
La discrepanza nella Completa è un caso da risolvere dopo aver confrontato
gli originali, la bibliografia scientifica e le modifiche proposte.
Non cambiare direttamente i documenti originari eCampus.

## Controllo di un'affermazione nelle dispense originali

Per verificare una possibile inesattezza presente nell'Essenziale o nella
Completa si può recuperare l'evidenza originale con la CLI:

```powershell
bc-science courses inspect-claim "C:\\Studio\\SCIENZE MOTORIE" --subject "ANATOMIA" --chapter "Midollo spinale" --claim "Dal midollo spinale si originano 31 paia di nervi spinali."
```

La ricerca riutilizza la cache delle fonti originali con i relativi SHA256
convalidati (FTS5/BM25 o fallback lessicale), restituendo nome del documento,
pagina se disponibile, oppure blocco e passaggio pertinente. Nessuna
chiamata a modelli o rete; non rigenera dispense, riassunti o PDF. La cache
dell'indice delle prove può essere popolata localmente.

Una corrispondenza lessicale **non** prova la verità scientifica del testo
recuperato, né certifica che la frase sia correttamente giustificata. Se non
emerge una corrispondenza, questo non dimostra che il fatto manchi nei PDF:
potrebbe essere espresso diversamente, oppure non disponibile nella cache.

## Anteprima Essenziale non distruttiva — audit del corpus reale

Quando una versione Essenziale è troppo lunga oppure non copre fatti critici,
non utilizzare subito `courses build` (può elaborare l'intero corso).
Usa la funzione **read-only rispetto ai materiali pubblicati**:

```powershell
bc-science courses preview-essential "C:\Studio\SCIENZE MOTORIE" --output-dir "C:\BC-Science1\reports\essenziale-preview-01"
bc-science courses preview-essential "C:\Studio\SCIENZE MOTORIE" --subject "ANATOMIA" --output-dir "C:\BC-Science1\reports\anteprima-anatomia-01"
```

Il comando confronta le bozze estrattive generate offline con i file
esistenti, riutilizza i capitoli della Completa **solo se gli hash e il
manifest risultano verificati**, segnala eventuali fatti numerici e punti
d'esame non coperti, e produce `preview-report.md`, `preview-report.json`
e `*-anteprima.md` in una directory di report **nuova e isolata**.

Le anteprime **non** sostituiscono `riassunto-studio.md` o i PDF, non
ricreano indicizzazioni/OCR, non attivano Ollama, e non modificano il manifest
né gli audit persistenti. `REVIEW` significa soltanto `da esaminare`, non
un'approvazione scientifica; `BLOCKED` significa che il materiale non ha
superato un gate tecnico. Una compressione maggiore non prova che tutte le
nozioni essenziali siano preservate: confrontare i passaggi segnalati con
la Completa e con i documenti originali prima di pubblicare.

## BC Science Agent Mesh — 100 ruoli di verifica (0.9.19)

Il registro `bc-science agents list` contiene **100 agenti verificatori diversi**,
organizzati in dieci aree: fonti, manifest, mappa, Completa, PDF, Essenziale,
PDF Essenziale, audit, ricerca e sicurezza. Sono verificatori deterministici,
NON 100 istanze di LLM: niente chiamate cloud, nessun modello aggiuntivo,
nessun costo di inferenza per questi controlli.

```powershell
bc-science agents list
bc-science agents plan --task study --limit 24
bc-science agents run "C:\\Studio\\SCIENZE MOTORIE" --task study
bc-science agents run "C:\\Studio\\SCIENZE MOTORIE" --task publish --all --strict
```

Il coordinatore seleziona per task `ingest`, `summarize`, `study` o
`publish`, dando priorità ai controlli di integrità. Lo stato per ciascuna
regola è `PASS`, `WARN`, `FAIL`, `SKIP` (non verificabile)
oppure `N/A` (controllo facoltativo esplicitamente disattivato).
I controlli opzionali disattivati non vengono conteggiati come PASS e non
penalizzano da soli gli obbligatori. Le anomalie `FAIL/WARN` vengono mostrate
nella console con i fatti numerici mancanti, le motivazioni dell'audit e
i primi nomi dei file identici per SHA256.
Un controllo obbligatorio mancante impedisce di dichiarare un PASS aggregato;
una selezione parziale non viene spacciata per una verifica completa.
Ogni esecuzione produce `agent-report.json` e `agent-report.md` sotto
`%LOCALAPPDATA%\\BCScience\\reports\\agent-mesh\\<TASK>`,
oppure nella directory passata con `--output-dir`.

Tutti i controlli sono **read-only**: non modificano le dispense o i riassunti.
Il catalogo offre un livello aggiuntivo di diagnostica, non certifica
scientificamente le spiegazioni. La modalità `--strict` termina con
codice diverso da zero per `FAIL` o `INCOMPLETE`.

L'architettura è costruita per introdurre in una fase successiva
solo quando necessario agenti ragionatori Qwen locali, checkpoint e
consenso umano per modifiche alle dispense.

## Verifica multi-materia offline — 0.9.18

Il nuovo audit strutturale è indipendente dalla disciplina: funziona anche
con materie di altre facoltà, senza avviare Ollama.

```powershell
bc-science courses verify "C:\Studio\SCIENZE MOTORIE"
bc-science courses verify "C:\Studio\SCIENZE MOTORIE" --strict
bc-science courses verify "C:\Studio\SCIENZE MOTORIE" --output-dir "C:\BC-Reports"
```

Produce una matrice documento → argomento → capitolo con SHA256 e segnala
nuove/modificate dispense, collegamenti mancanti, output non aggiornati e
audit in FAIL. I report `verification.md` e `verification.json` sono salvati
in `%LOCALAPPDATA%\BCScience\reports\courses-verify` se non si indica
`--output-dir`. `--strict` termina con errore anche in caso di WARN.

**Limite:** questo audit non equivale a una verifica scientifica dei contenuti.

Per testare installazione, lint, suite Python, diagnostica e materiale reale
su Windows, dalla repository:

```powershell
.\scripts\test-all.ps1 -CourseRoot "C:\Studio\SCIENZE MOTORIE" -Full
```

Il runner salva un `result.json` e log separati sotto
`reports\test-all\<timestamp>`. Senza `-Full` non rigenera dispense,
ma esegue la verifica strutturale del corpus esistente. Se il corpus non
è disponibile, i test del codice vengono comunque eseguiti e il controllo
reale viene segnalato come non eseguito. La modalità Full richiede Ollama
locale e usa il profilo Standard (`qwen3.5:4b`).

## Normalizzazione dispense eCampus — 0.9.4

La normalizzazione dei nomi file usa ora regole conservative basate sul corpus reale:

- `Argomento 2`, `Argomento 3` -> stesso argomento;
- `Argomento FAQ` e `Argomento quiz` -> stesso capitolo principale;
- `parte I`, `parte II`, `parte 2` -> stesso argomento;
- artefatti come `2pdf`, `1p` e suffissi attaccati come `esercizio2` vengono rimossi quando sono marcatori di variante;
- numeri scientificamente significativi come `tipo 2`, `fase 2`, `B12`, `CO2`, `pH 7`, `Omega 3` vengono preservati.

Questo evita capitoli duplicati o artificiali nei riassunti unici senza cancellare numeri che fanno parte del contenuto scientifico.

## Audit automatico della versione studio — 0.9.6

Dopo la creazione della versione studio, BC Science misura automaticamente quanto è stata compressa e se i fatti ad alta resa del riassunto completo risultano ancora rappresentati.

Il comando manuale è:

    bc-science courses audit "C:\\Studio\\SCIENZE MOTORIE"

Per usarlo come gate in script o CI:

    bc-science courses audit "C:\\Studio\\SCIENZE MOTORIE" --strict

Per ogni materia vengono calcolati:

- Fact Coverage pesata;
- fatti numerici e punti d'esame non coperti;
- compressione rispetto al riassunto completo;
- ridondanza globale stimata;
- chiarezza media per un principiante;
- stato finale `PASS`, `WARN` o `FAIL`.

I report sono salvati in:

    %LOCALAPPDATA%\\BCScience\\outputs\\courses\\<MATERIA>\\audit.md
    %LOCALAPPDATA%\\BCScience\\outputs\\courses\\<MATERIA>\\audit.json

Un valore numerico presente nel riassunto completo e non ritrovato nella versione studio forza `FAIL`. Il report non modifica le fonti: serve a indicare esattamente quali fatti devono essere controllati o reintegrati. `courses status` mostra anche la colonna `Audit`; se completo o studio cambiano, un vecchio report viene marcato `obsoleto`.

## Versione studio automatica — 0.9.5

Dopo aver verificato la copertura del riassunto completo, `courses build` crea anche una seconda copia pensata per lo studio rapido:

    %LOCALAPPDATA%\\BCScience\\outputs\\courses\\<MATERIA>\\riassunto-studio.md
    %LOCALAPPDATA%\\BCScience\\outputs\\courses\\<MATERIA>\\riassunto-studio.pdf

La versione studio parte esclusivamente dal riassunto completo verificato. Prima della compressione esegue un merge conservativo dei capitoli duplicati o quasi duplicati, rimuove ripetizioni e citazioni di pagina, ma conserva fatti distinti, numeri, definizioni, classificazioni, sequenze ed eccezioni. I riferimenti di pagina restano disponibili nel `riassunto-unico` completo.

La struttura didattica usa `In parole semplici`, `Concetti chiave`, `Spiegazione ordinata` e una sola sezione finale `Da ricordare per l'esame`. Il manifest v3 lega la versione studio all'hash esatto del riassunto completo, quindi non viene rigenerata quando nulla è cambiato.

`courses status` mostra la colonna `Studio` con `aggiornato`, `da aggiornare` o `manca`.

## Riassunto unico verificato + PDF — 0.9.3

`bc-science courses build` non considera più sufficiente la semplice presenza di un file chiamato `riassunto-unico.md`. Prima di riutilizzarlo verifica che sia realmente basato sulle dispense correnti e che tutti gli argomenti siano rappresentati.

Per ogni materia vengono mantenuti insieme:

    %LOCALAPPDATA%\BCScience\outputs\courses\<MATERIA>\riassunto-unico.md
    %LOCALAPPDATA%\BCScience\outputs\courses\<MATERIA>\riassunto-unico.pdf

Il manifest del workspace salva gli hash dei documenti, l'hash del Markdown, la mappa documento -> capitolo e l'hash del riassunto usato per creare il PDF.

Il gate di copertura controlla:

- che l'insieme dei documenti corrisponda esattamente alle dispense attuali;
- che il file abbia un vero indice BC Science e capitoli riconoscibili;
- che ogni documento sia associato a un capitolo realmente presente;
- che ogni gruppo/argomento delle dispense sia rappresentato;
- che il PDF corrisponda esattamente al Markdown verificato corrente.

Comportamento automatico:

- riassunto completo + PDF aggiornato: riuso totale, senza Ollama;
- riassunto completo ma PDF mancante/obsoleto: genera soltanto il PDF;
- nuove dispense additive: usa l'aggiornamento incrementale 0.9.1, rivalida la copertura e rigenera il PDF;
- documenti modificati/rimossi o copertura incompleta: rebuild del riassunto sui documenti attuali e nuovo PDF;
- vecchio `*-riassunto-unico.md` senza manifest verificabile: viene rilevato ma non considerato completo per fiducia; viene ricostruito una volta sulle dispense attuali;
- manifest precedenti alla 0.9.3: vengono migrati attraverso lo stesso controllo conservativo.

`courses status` mostra le colonne `Copertura`, `PDF` e, dalla 0.9.5, `Studio`.

## Approfondimento scientifico oltre eCampus — 0.9.2

BC Science può creare un **secondo documento separato** dal riassunto d'esame. Il riassunto eCampus resta source-only; l'approfondimento usa letteratura scientifica esterna recuperata da PubMed/NCBI.

Configurazione iniziale:

    bc-science research configure --email nome@example.com

L'email viene inviata a NCBI come identificativo richiesto dalle E-utilities. L'API key NCBI è facoltativa:

    bc-science research configure --email nome@example.com --api-key LA_TUA_KEY

Stato degli approfondimenti:

    bc-science research status "C:\Studio\SCIENZE MOTORIE"

Generazione manuale:

    bc-science research build "C:\Studio\SCIENZE MOTORIE"

Output:

    %LOCALAPPDATA%\BCScience\outputs\research\<MATERIA>\approfondimento-scientifico.md

Una volta abilitata con `research configure`, la ricerca diventa automatica dopo `courses build`: viene eseguita solo se il riassunto della materia è cambiato. Il relativo manifest impedisce ricerche e sintesi duplicate quando l'input è invariato.

Per ogni capitolo BC Science:

- genera localmente una query bibliografica PubMed basata sul contenuto eCampus;
- cerca prima systematic review, meta-analisi, review e practice guideline;
- recupera record PubMed tramite le E-utilities ufficiali;
- usa solo fonti con abstract disponibile per la sintesi;
- crea sezioni `Cosa aggiunge la letteratura`, `Per capire meglio` e `Limiti delle evidenze recuperate`;
- richiede citazioni `[1]`, `[2]`, ecc. valide in ogni sezione;
- aggiunge deterministicamente titolo, rivista, anno, PMID, DOI quando disponibile e link PubMed;
- non modifica mai il riassunto eCampus;
- non salva un approfondimento non verificabile se il quality gate delle citazioni fallisce.

Se PubMed/NCBI non è raggiungibile durante `courses build`, il riassunto eCampus continua normalmente e viene mostrato solo un avviso per l'approfondimento.

Nota metodologica: la modalità automatica lavora su titoli, metadati e abstract PubMed. È un supporto allo studio e non equivale a una revisione sistematica completa della letteratura.

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

Versione corrente: **0.9.17**.


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
- 0.9.1: aggiornamento incrementale sicuro per documenti aggiunti — implementato;
- 0.9.2: approfondimento scientifico separato PubMed/NCBI — implementato;
- 0.9.3: riassunto unico verificato + PDF per materia — implementato;
- 0.10: audit di copertura e coerenza;
- 0.10.5: Scientific Enrichment — nucleo anticipato in 0.9.2;
- 0.11: modalità esame;
- 0.12: aggiornamento incrementale completo per modifiche/rimozioni;
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

### Source hygiene e retrieval audit 0.9.17

BC Science distingue ora in modo esplicito le fonti primarie dagli artefatti prodotti
dalla propria pipeline:

- `*-dispensa-finale.*`, `*-riassunto-unico.*`, `*-riassunto-studio.*`,
  `approfondimento-scientifico.*`, audit e benchmark non vengono indicizzati;
- gli artefatti derivati gia presenti nel database semantico vengono rimossi
  automaticamente senza eliminare documenti appartenenti ad altri corsi;
- l'installer Windows aggiunge Tesseract al PATH, configura `TESSDATA_PREFIX`,
  installa `ita.traineddata` quando manca e imposta `BC_SCIENCE_OCR_LANG=ita+eng`;
- `bc-science retrieval-audit` misura Hit@K, Top-1 e MRR su un set fisso di domande
  di Anatomia/Fisiologia, salvando report Markdown e JSON;
- `bc-science retrieval-audit --strict` puo essere usato come quality gate locale.
