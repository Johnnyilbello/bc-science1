# BC Science — Evidence Engine v1

## Obiettivo

Verificare frasi incomplete, ripetizioni e contraddizioni sospette confrontandole con **dispense originali**, non soltanto con la Completa. Mantenere il riassunto Essenziale quasi istantaneo e la revisione AI opzionale.

## Come funziona

1. `Genera Essenziale` crea il riassunto rapido, segnala anomalie strutturali e linguistiche ma NON avvia Ollama né OCR.
2. Apri un capitolo Essenziale e premi **Verifica sulle dispense**. BC Science legge le **copie già estratte e indicizzate delle dispense ORIGINALI** nel database SQLite del corso, non il documento Completa. Recupera i passaggi con FTS5/BM25, indicando **nome del file, pagina originale**, oppure numero del blocco se la pagina non è disponibile.
3. **Controlla con Qwen** richiede prove originali prima di inviare una richiesta al Qwen3.5 4B locale. Se BM25 non trova passaggi sufficienti, un singolo embedding locale può recuperare blocchi semantici già indicizzati. Un file modificato con hash non corrispondente NON viene utilizzato.
4. Qwen propone un JSON con riscrittura e citazioni della Completa e degli ORIGINALI. La correzione passa solo se le citazioni originali esistono nel file indicato alla pagina/blocco indicati e non peggiora i controlli numerici, sintattici e di leggibilità. In caso contrario il testo pubblicato resta invariato.

## Stati da non confondere

- **PASS tecnico:** provenienza e struttura dei file controllate, non accuratezza scientifica garantita.
- **Anomalie linguistiche e strutturali:** indicatori euristici. Un alert non equivale a dimostrare che una frase sia falsa.
- **Prove originali recuperate:** sono riferimenti da ispezionare; una frase originale può contenere a sua volta un errore OCR o scientifico.
- **Correzione assistita Qwen:** proposta vincolata a fonti; resta necessaria la verifica scientifica umana in caso di contraddizioni.

## Sicurezza e prestazioni

- Tutto locale; **nessun grounding web** e nessuna trasmissione dei PDF all'esterno.
- Usare preferibilmente SQLite FTS5 per parole esatte e BM25; se FTS5 manca è disponibile un recupero lessicale di emergenza.
- L'indice di evidenze è incrementale e usa solo file del corso con **SHA-256 identico** al manifest corrente. Le pagine OCR sono lette dalla cache: non vengono ricalcolate.
- La ricerca semantica è un ripiego **solo nella revisione Qwen del singolo capitolo** e riusa gli embedding già memorizzati.
- Nuovi paragrafi corretti non vengono inventati. Dove le dispense originali sono ambigue o contraddittorie, segnala la necessità di revisione.

## Riferimenti tecnologici

- [SQLite FTS5 e BM25](https://www.sqlite.org/fts5.html) — funzione built-in con ranking lessicale.
- [Ollama API, structured outputs e keep_alive](https://docs.ollama.com/api) — strumenti locali per una possibile futura interfaccia JSON schema nativa.


## Copertura delle due edizioni

**Completa** ed **Essenziale** hanno entrambe: avvisi separati di lingua e
struttura, pulsante **Verifica sulle dispense**, recupero dell'originale tramite
FTS5 + eventuali embedding locali, e revisione mirata con Qwen solo su richiesta.

- La Completa si può verificare e rivedere anche prima di creare Essenziale.
- La revisione della Completa agisce sul **solo capitolo scelto** e conserva una
  copia versionata della precedente Completa e del relativo PDF.
- Dopo una modifica alla Completa, l'Essenziale viene marcata come **da
  aggiornare**: i documenti precedenti restano su disco ma perdono lo stato
  corrente nel manifest. Usa Genera Essenziale per aggiornarla rapidamente.
- La revisione della Essenziale non altera la Completa.
- Le due edizioni restano riassunti narrativi, non vengono sostituite da
  lezioni lunghe né da un glossario ripetitivo.
- Nessun PASS tecnico o citazione originale certifica da sé la correttezza
  scientifica: il testo universitario può contenere errori ed OCR imperfetto.
