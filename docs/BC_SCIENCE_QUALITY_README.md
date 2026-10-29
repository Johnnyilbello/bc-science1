# BC Science · Qualità dei riassunti — fedeltà e comprensibilità

## Cosa è cambiato

**Essenziale** resta estrattivo e rapido: usa la Completa già verificata, senza richiamare Qwen 77 volte. Il selettore conserva numeri d'esame e dà priorità a definizioni, meccanismi, classificazioni, cause-effetti ed eccezioni. I dettagli secondari possono essere omessi.

**Audit tecnico** (PASS, WARN, FAIL) riguarda soltanto l'integrità dei file, la copertura dei capitoli, la provenienza della Completa e i numeri espressamente controllati.

**Contenuto** resta NON_VERIFICATO_SCIENTIFICAMENTE fino a controllo umano sulle dispense. Non viene mai presentato come verità certificata perché ha passato un confronto lessicale. Il report audit.json / audit.md indica per capitolo la priorità, possibili omissioni ed esempi di frasi della Completa da confrontare.

**Qwen3.5 4B** è una funzione separata: seleziona un capitolo nella modalità Essenziale e premi **Controlla con Qwen**. Non viene chiamato nel percorso rapido. La revisione è strettamente limitata al capitolo scelto; richiede citazioni letterali dalla Completa, non può aggiungere numeri nuovi, perdere punti d'esame o peggiorare i controlli di leggibilità. Se i controlli non passano, il riassunto esistente resta invariato. Se passano, la modifica resta **assistita da AI**, non scientificamente certificata.

## Verifica di qualità reale

Su tre capitoli diversi (uno di anatomia, uno di fisiologia, uno ricco di numeri/classificazioni), confrontare il nuovo Essenziale e la Completa con i PDF originali. Verificare separatamente:

1. **Fedeltà:** niente affermazioni incompatibili con le dispense originali; confrontare sempre le citazioni sospette.
2. **Rilevanza:** definizioni, processi, eccezioni, formule e valori utili all'esame restano nel testo.
3. **Coerenza:** gli argomenti seguono un filo logico, senza frasi isolate o collegamenti inventati.
4. **Fluidità/comprensibilità:** frasi e paragrafi brevi, termini spiegati e assenza di sezioni ripetitive.

Non dedurre una garanzia di accuratezza da un punteggio automatico né dalla sola conservazione di numeri. La Completa è una fonte derivata dalle dispense, non una revisione accademica indipendente. La versione rapida v5 è volutamente diversa dalla revisione AI opzionale.

## Riferimenti di ricerca

- SummEval (Fabbri et al., 2021): coerenza, consistenza, fluidità e rilevanza, da valutare separatamente. https://doi.org/10.1162/tacl_a_00373
- FACTSCORE (Min et al., EMNLP 2023): granularità dei fatti atomici e confronto con prove sorgente. https://aclanthology.org/2023.emnlp-main.741/
- QAFactEval (Fabbri et al., NAACL 2022): importanza di answerability e controlli su sorgenti per valutare la consistenza. https://aclanthology.org/2022.naacl-main.187/

BC Science **non implementa integralmente** FACTSCORE né QAFactEval: applica principi di controllo granulari con euristiche locali dichiarate, senza scaricare modelli esterni.
