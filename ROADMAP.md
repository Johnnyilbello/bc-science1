# BC Science Roadmap

## Principio guida

BC Science deve produrre materiale che sia contemporaneamente:

1. fedele alle fonti eCampus;
2. completo rispetto ai contenuti potenzialmente utili all'esame;
3. comprensibile anche a chi non ha mai studiato la materia;
4. verificabile: qualità, copertura e limiti devono essere visibili;
5. local-first e riutilizzabile per tutte le materie di Scienze Motorie.

La semplificazione non deve mai significare eliminare informazioni utili o inventare
spiegazioni esterne alle fonti.

## 0.8 — Novice-first comprehension

Obiettivo: nessun PDF finale viene prodotto se i capitoli richiedono conoscenze pregresse
non spiegate.

- sezione obbligatoria "In parole semplici";
- sezione "Parole chiave" facoltativa: utile solo quando migliora davvero la comprensione;
- termini tecnici introdotti e definiti usando solo le fonti;
- progressione quadro generale -> dettagli -> meccanismi -> ripasso;
- frasi e paragrafi controllati per densità;
- quality gate deterministico con score 0-100;
- normalizzazione automatica dei paragrafi troppo densi senza riscrivere il contenuto;
- autocorrezione iterativa del solo capitolo che non supera il gate, con più tentativi e rivalidazione;
- comando bc-science clarity;
- finalize bloccato se anche un solo capitolo non è novice-ready.

Criterio di uscita: tutti i capitoli del corso devono avere score >= 80/100 e nessun
problema hard di chiarezza.

## 0.9 — Multi-materia, cartella radice e workspace per corso ✅

Obiettivo: passare da una pipeline validata su Fisiologia a una piattaforma per tutto il corso
di laurea, senza obbligare l'utente a creare ZIP/RAR per ogni materia.

Comando principale pianificato:

    bc-science courses build "C:\Studio\SCIENZE MOTORIE"

La directory passata al comando è la cartella radice. Ogni sottocartella diretta viene
considerata una materia distinta, per esempio:

    SCIENZE MOTORIE\
      FONDAMENTI DI BIOLOGIA E CHIMICA\
      ANATOMIA\
      FISIOLOGIA UMANA E DELLO SPORT\

Per ogni materia BC Science dovrà:

- scansionare ricorsivamente tutte le sottocartelle;
- contare file totali, file supportati, file ignorati e cartelle visitate;
- usare direttamente PDF, DOCX, TXT e Markdown senza richiedere archivi;
- ignorare file temporanei, nascosti, output precedenti e formati non supportati,
  segnalandoli nel report iniziale;
- mostrare un preflight prima dell'elaborazione con numero di materie e documenti trovati;
- creare un workspace separato con cache, indice e output dedicati;
- produrre UN SOLO riassunto coerente per materia, non una raccolta di mini-riassunti;
- scrivere per un lettore che non ha mai studiato la materia;
- mantenere "In parole semplici" come principio editoriale, ma NON richiedere
  obbligatoriamente una sezione "Parole chiave";
- preservare definizioni, classificazioni, numeri, meccanismi, sequenze ed eccezioni;
- riutilizzare cache e risultati precedenti quando i file non sono cambiati;
- consentire in seguito finalize/audit separati per ogni materia.

Output previsto:

    outputs\courses\FONDAMENTI DI BIOLOGIA E CHIMICA\riassunto-unico.md
    outputs\courses\ANATOMIA\riassunto-unico.md
    outputs\courses\FISIOLOGIA UMANA E DELLO SPORT\riassunto-unico.md

Comandi pianificati:

    bc-science courses list "C:\Studio\SCIENZE MOTORIE"
    bc-science courses status "C:\Studio\SCIENZE MOTORIE"
    bc-science courses build "C:\Studio\SCIENZE MOTORIE"

`courses list` deve essere una scansione veloce senza usare Ollama.
`courses status` deve mostrare cosa è già indicizzato/generato e cosa è cambiato.
`courses build` deve eseguire la pipeline solo sulle materie necessarie.

Criterio di uscita: almeno tre materie differenti elaborate end-to-end partendo direttamente
da una cartella radice, con un riassunto unico e comprensibile per materia e senza regole
hardcoded specifiche del singolo corso.

Implementazione 0.9.0: motore multi-materia, scansione/preflight, workspace e DB separati,
manifest con hash, status incrementale e build selettivo sono disponibili. Il collaudo reale
su almeno tre materie complete resta il passaggio operativo da eseguire sul corpus utente.

## 0.9.1 — Aggiornamento incrementale additivo ✅

Obiettivo: quando in una materia vengono aggiunte nuove dispense, evitare la rilettura e la
rigenerazione dell'intero corso.

Implementato:

- confronto manifest/hash per identificare esattamente i documenti aggiunti;
- indicizzazione dei soli nuovi documenti;
- raggruppamento per argomento tramite nome normalizzato;
- routing content-aware conservativo verso un capitolo esistente quando il nome non basta;
- analisi source-only esclusivamente del nuovo materiale;
- merge del nuovo materiale con il solo capitolo coinvolto;
- gate anti-perdita sui punti gia presenti in "Da ricordare per l'esame";
- un secondo tentativo di repair se il primo merge non supera il gate;
- fallback automatico al rebuild completo se l'incrementale non e sicuro;
- inserimento di un nuovo capitolo quando il materiale introduce davvero un nuovo argomento;
- aggiornamento deterministico di Ripasso globale e indice;
- rigenerazione della Mappa della materia solo quando compare un nuovo capitolo;
- nessuna chiamata Ollama quando non esistono cambiamenti.

Limite intenzionale: file esistenti modificati o rimossi usano ancora il rebuild completo,
perche un merge additivo non puo determinare in sicurezza quali informazioni precedenti
debbano essere eliminate. La 0.12 estendera l'incrementale a questi casi tramite dipendenze
file -> argomento -> capitolo e invalidazione selettiva.

## 0.9.3 — Riassunto unico verificato e PDF ✅

Obiettivo: garantire che ogni materia abbia un solo riassunto realmente costruito sui documenti
correnti e un PDF sincronizzato, inclusi gli output creati con versioni precedenti.

Implementato:

- rilevamento del riassunto unico canonico e dei vecchi `*-riassunto-unico.md`;
- manifest schema 2 con hash delle dispense, hash del riassunto e mappa documento -> capitolo;
- gate documento -> argomento -> capitolo prima del riuso;
- rilevamento di argomenti/documenti non rappresentati;
- rebuild conservativo quando un vecchio output non puo dimostrare la propria copertura;
- riuso totale quando documenti, copertura, Markdown e PDF sono tutti sincronizzati;
- generazione del solo PDF quando il Markdown verificato e gia corrente;
- rigenerazione automatica del PDF dopo update incrementale o rebuild;
- output `riassunto-unico.pdf` accanto a `riassunto-unico.md` per ogni materia;
- stato CLI separato per copertura e PDF;
- nessuna aggiunta scientifica esterna al PDF eCampus: resta la versione source-only del riassunto.

Criterio di uscita: nessun PDF multi-materia viene considerato aggiornato se non e possibile
dimostrare tramite manifest e coverage map che deriva dal riassunto basato su tutte le dispense
correnti della materia.

## 0.9.4 — Normalizzazione corpus reale eCampus ✅

Obiettivo: evitare falsi capitoli separati causati da suffissi dei file eCampus.

Implementato:

- merge di varianti numerate dello stesso argomento;
- merge di FAQ e quiz nel capitolo principale;
- supporto a parti numeriche e romane;
- rimozione di artefatti `2pdf`, `1p` e numeri duplicato attaccati al titolo;
- protezione dei numeri scientificamente significativi (`tipo 2`, `fase 2`, `B12`, `CO2`, `pH 7`, `Omega 3`);
- test sul corpus reale emerso durante il build di Fondamenti di Biologia e Chimica.

## 0.9.5 — Doppio output completo + studio ✅

Obiettivo: mantenere la copertura totale verificata ma produrre anche una versione realmente studiabile,
più corta e senza duplicazioni semantiche.

Implementato:

- `riassunto-unico.md/pdf` resta la copia completa e tracciabile;
- nuovo `riassunto-studio.md/pdf` derivato dal riassunto completo verificato;
- semantic merge conservativo dei capitoli duplicati o quasi duplicati, con controllo del contenuto;
- merge delle varianti numerate solo quando esiste il capitolo base e il contenuto coincide sostanzialmente;
- pulizia dei titoli duplicati/refusi editoriali nella sola versione studio;
- rifinitura didattica con `In parole semplici`, `Concetti chiave`, `Spiegazione ordinata` e `Da ricordare per l'esame`;
- rimozione delle citazioni di pagina dalla copia studio, mantenute nella copia completa;
- manifest v3 con hash della sorgente completa e della versione studio;
- `courses status` espone anche lo stato della versione studio;
- test dedicati a deduplica semantica, preservazione dei numeri scientifici e doppio output.

## 0.9.6 — Atomic Fact Coverage + audit automatico ✅

Obiettivo: misurare in modo ripetibile se la versione studio è davvero più corta senza perdere i fatti ad alta resa del riassunto completo verificato.

Implementato:

- estrazione deterministica di fatti atomici dai capitoli completi e studio;
- categorie ad alta priorità: numeri, punti d'esame, definizioni, classificazioni, sequenze ed eccezioni;
- matching conservativo tra fatti sorgente e versione studio con vincolo esplicito sui valori numerici;
- Fact Coverage pesata e conteggio dei fatti mancanti;
- rilevazione globale delle ridondanze nella versione studio;
- metriche di compressione e chiarezza principiante;
- stati `PASS`, `WARN`, `FAIL`;
- nuovo comando `bc-science courses audit <RADICE>`;
- report persistenti `audit.md` e `audit.json` per ogni materia;
- `courses status` espone lo stato dell'audit e segnala report obsoleti;
- `courses build` aggiorna automaticamente l'audit dopo la generazione dello studio;
- modalità `courses audit --strict` per restituire errore quando almeno una materia è in `FAIL`.

## 0.10 — Audit di copertura e coerenza

Obiettivo: misurare ciò che oggi viene controllato principalmente tramite audit manuale.

- comando bc-science audit;
- matrice documento -> argomento -> capitolo;
- documenti indicizzati ma non rappresentati;
- capitoli troppo compressi rispetto alle fonti;
- mismatch titolo/contenuto;
- contraddizioni interne senza riconciliazione inventata;
- duplicati semantici;
- controllo di numeri, classificazioni, definizioni e sequenze;
- report Markdown/JSON con PASS/WARN/FAIL.

Criterio di uscita: nessuna finalizzazione quando esistono FAIL di copertura o struttura.

## 0.10.5 — Scientific Enrichment oltre eCampus ✅

Obiettivo: affiancare al materiale d'esame un secondo documento di approfondimento scientifico
che vada oltre eCampus senza modificare, correggere o contaminare il riassunto source-only.

Output separato previsto:

    outputs\research\<MATERIA>\approfondimento-scientifico.md

Principi:

- il riassunto eCampus resta invariato e continua a usare esclusivamente le dispense;
- la ricerca scientifica produce sempre un documento separato chiaramente marcato "oltre eCampus";
- ricerca automatica opt-in: dopo la configurazione viene aggiornata quando cambia il riassunto della materia;
- fonti primarie iniziali: PubMed/NCBI tramite API ufficiali;
- preferenza per systematic review, meta-analisi, review e linee guida quando disponibili;
- ogni affermazione aggiuntiva deve essere riconducibile alle fonti recuperate;
- citazioni con PMID, DOI quando disponibile, rivista e anno;
- nessuna fonte web generica deve essere trattata come equivalente alla letteratura scientifica;
- il modello locale sintetizza soltanto titoli/abstract recuperati, senza inventare risultati mancanti;
- se non esistono fonti sufficienti, il capitolo deve dichiararlo invece di riempire il vuoto;
- cache/manifest separato per evitare nuove ricerche quando il riassunto e le fonti non sono cambiate;
- un errore di rete o della ricerca non deve mai bloccare la generazione del materiale eCampus.

Comandi previsti:

    bc-science research configure --email <email>
    bc-science research status "C:\Studio\SCIENZE MOTORIE"
    bc-science research build "C:\Studio\SCIENZE MOTORIE"

Criterio di uscita: almeno tre materie devono produrre un approfondimento separato con fonti
scientifiche verificabili e nessuna modifica al corrispondente riassunto eCampus.

Implementazione anticipata in 0.9.2: PubMed/NCBI, output separato, ricerca automatica opt-in,
manifest/cache, preferenza per review/meta-analisi/linee guida, PMID/DOI, quality gate delle
citazioni e isolamento dagli output eCampus sono disponibili. Provider scientifici aggiuntivi
potranno essere aggiunti successivamente senza cambiare questo contratto.

## 0.11 — Modalità esame

Obiettivo: trasformare la dispensa in allenamento attivo.

- quiz source-grounded;
- domande aperte;
- simulazione orale;
- flashcard;
- "spiegamelo come se non lo sapessi";
- error log personale;
- sessioni di ripasso basate sui capitoli più deboli;
- nessuna domanda presentata come ufficiale eCampus se non proviene da una fonte esplicita.

Criterio di uscita: ogni domanda deve riportare il capitolo/fonte da cui è stata ricavata.

## 0.12 — Aggiornamento incrementale e performance

Obiettivo: aggiungere o modificare dispense senza rigenerare il corso intero.

- rilevamento file nuovi/modificati;
- mappa dipendenze file -> argomenti;
- rigenerazione dei soli capitoli impattati;
- invalidazione cache selettiva;
- benchmark automatico velocità/qualità;
- report del lavoro riusato vs rigenerato.

Criterio di uscita: aggiungere poche dispense a un corso esistente deve costare
proporzionalmente ai soli capitoli coinvolti.

## 1.0 — One-shot affidabile

Obiettivo: un solo comando per arrivare dai materiali alla dispensa verificata.

    bc-science build "C:\Studio\ANATOMIA.zip"

Pipeline:

    doctor
      -> ingest
      -> summarize
      -> refine
      -> clarity
      -> audit
      -> finalize

Il comando deve interrompersi con un messaggio utile se uno dei quality gate fallisce.

Criteri 1.0:

- multi-materia stabile;
- beginner-first PASS;
- audit copertura PASS;
- nessun troncamento o duplicazione strutturale;
- output Markdown + DOCX + PDF;
- fonti e note scientifiche separate dal testo eCampus;
- aggiornamento incrementale;
- test CI e smoke test end-to-end.
