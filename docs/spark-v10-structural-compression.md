# Spark V10 — pilot di compressione strutturale

L'Essenziale V9 espande i capitoli su Krebs e proteine/lipidi e copia quello su
chimica organica. V10 introduce un comando separato, `courses
structural-compression-preview`, per produrre tre sintesi redatte specificamente
su queste versioni della Completa. Non cambia la generazione ordinaria, la UI,
i file del corso, la configurazione, i PDF o la pubblicazione.

Il piano contiene la sintesi progressiva, un ripasso breve, gli ID dei passaggi
della Completa rappresentati in ciascun paragrafo e la motivazione degli
accorpamenti. Il solo commento editoriale sulla corrispondenza fra titolo e
contenuto viene trasferito nel dossier, con la motivazione. Non vengono
aggiunte spiegazioni scientifiche esterne. I dati energetici dubbi della Completa
rimangono attribuiti alla fonte e segnalati per la revisione.

## Confini delle garanzie

- SHA256 degli allegati Completa/Essenziale devono coincidere con il report V9;
  il report V9 originale ha a sua volta un hash fissato nel comando Windows.
- Manifest v3 e fingerprint delle dispense vengono verificati prima e dopo.
- Il testo di ciascuno dei tre capitoli deve coincidere con il suo hash nel piano.
  Un documento aggiornato richiede un nuovo piano revisionato; non attiva un modello.
- Il piano ha un hash fissato nel codice. Testo prodotto e mappa devono coincidere
  con esso: modificare un numero, una formula, una negazione o un qualificatore
  dopo la sintesi blocca l'anteprima. Questo garantisce l'integrità del testo
  redatto, non dimostra la correttezza scientifica delle sue equivalenze.
- La copertura di tutti gli ID è necessaria ma non sufficiente: le unità sono
  frasi, talvolta contenenti più fatti. La valutazione delle equivalenze e della
  loro completezza resta obbligatoria prima del merge.
- Il ripristino dei soli tre capitoli deve ricostruire i byte dell'Essenziale
  originale, inclusi BOM e newline. Titoli fuori dal pilot restano intatti.
- Un nuovo FAIL nell'audit blocca la scrittura; WARN non significa approvazione.
- Si scrive solo in una cartella nuova esterna a corso e input. Non esiste un
  comando V10 per approvare, pubblicare, fare merge o modificare gli originali.

Il range 30–45% è indicativo e subordinato alla conservazione dei contenuti. Il
piano chiuso fornito rientra nel range. Non c'è un ciclo che elimini altri fatti
per raggiungere una quota. Il conteggio V10 usa parole separate da spazi, incluse
le intestazioni Markdown: non coincide con il tokenizer dell'audit V9.

## Artefatti e verifica

`v10-report.json` contiene inventario completo con righe della Completa,
destinazioni, motivazioni, token protetti e contesto estratto dalle dispense.
`v10-dossier.md` rende leggibile la stessa revisione. I due diff confrontano la
candidata con Essenziale e Completa. Il diff sulla Completa comprende anche le
differenze preesistenti negli altri capitoli: il diff sull'Essenziale è quello
che delimita l'intervento V10. `SHA256SUMS.json` copre tutti gli altri artefatti.

La PR rimane bozza sul ramo V9 `fix/spark-v9-whole-chapter-context-review-20261103`.
Prima del merge occorre esaminare ogni riga dell'inventario e ogni equivalenza,
controllare numeri e qualificatori sulle dispense e valutare la leggibilità.
Questa revisione non è sostituita dai test né dai risultati del detector.

`scripts/test-spark-v10.ps1 -ExpectedCommit <SHA>` prepara una copia isolata,
esegue lint, regressione e pilot reale su Python 3.10/3.12, verifica gli hash
degli input e degli output e confronta le due candidate. Non pubblica nulla.
