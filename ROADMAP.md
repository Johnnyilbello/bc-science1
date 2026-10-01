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
- sezione obbligatoria "Parole chiave";
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

## 0.9 — Multi-materia e workspace per corso

Obiettivo: passare da una pipeline validata su Fisiologia a una piattaforma per tutto il corso
di laurea.

- workspace separato per materia;
- cache, indice, output e configurazione isolati per corso;
- rilevamento automatico del dominio;
- profili di stile per Anatomia, Fisiologia, Biomeccanica, Psicologia, Pedagogia,
  Nutrizione, Statistica e materie affini;
- gestione esplicita di figure, tabelle e pagine visuali quando il testo estratto non basta;
- comandi courses list, courses status, courses build.

Criterio di uscita: almeno tre materie differenti elaborate end-to-end senza regole hardcoded
specifiche del singolo corso.

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
