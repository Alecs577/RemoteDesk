# RemoteDesk — presentazione del progetto

Documento per la direzione · 18 ottobre 2026 · versione applicativa 0.1.0

## Che cos'è

RemoteDesk è un'applicazione per fornire assistenza a distanza sui PC Windows. Un operatore può vedere il desktop di un altro computer e, con i permessi previsti, usare mouse e tastiera, scambiare messaggi e trasferire file. Il progetto è destinato a una distribuzione proprietaria e comprende sia il programma desktop sia il software necessario per gestire le connessioni sui nostri server.

L'obiettivo è realizzare uno strumento di assistenza controllato dall'organizzazione, con un'interfaccia semplice e una gestione esplicita degli accessi. La disponibilità di un'infrastruttura propria permette di gestire indirizzi del servizio, distribuzione e procedure operative. Richiede anche di sostenere e presidiare hosting, aggiornamenti, sicurezza e assistenza.

## A cosa serve in azienda

- Supportare un collega o un cliente senza essere fisicamente davanti al suo computer.
- Guidare un utente durante la risoluzione di un problema mediante schermo condiviso e chat.
- Trasferire piccoli documenti o file necessari all'assistenza.
- Accedere a un PC non presidiato quando il proprietario ha configurato password e permessi appropriati.
- Recuperare l'identità di un dispositivo sostituito mediante un backup cifrato.

Il progetto non dispone ancora di misurazioni che dimostrino risparmi economici o prestazioni superiori ad altri prodotti. Questi risultati dovranno essere valutati nel pilota.

## Come funziona per l'utente

Il PC che riceve assistenza mostra un ID numerico di nove cifre. L'operatore inserisce l'ID e richiede il collegamento. La persona davanti al PC remoto può accettare o rifiutare e stabilire quali operazioni autorizzare. Durante la sessione rimane disponibile un comando locale per interrompere la condivisione.

Sono previsti anche accesso con password e inviti a scadenza con permessi specifici. La rubrica e le connessioni recenti facilitano i collegamenti successivi. Il servizio account, quando configurato, consente di gestire backup cifrati dell'identità; avere un account non autorizza automaticamente il controllo di un PC.

Nella distribuzione prevista, l'indirizzo del relay è incorporato nell'applicazione: l'utente può collegarsi usando l'ID senza configurare manualmente il server. Il profilo attuale contiene indirizzi dimostrativi e deve essere completato prima dell'uso via Internet.

## Funzionalità implementate

- Visualizzazione del desktop, scelta del monitor, schermo intero e profili di qualità.
- Controllo di mouse e tastiera subordinato ai permessi concessi.
- Chat e scambio di appunti testuali.
- Invio e ricezione di file, con limite attuale di 8 MiB per singolo file, e navigazione remota limitata alla cartella home.
- Schermate JPEG e registrazione come sequenza di immagini, senza audio.
- Rubrica, connessioni recenti, inviti a scadenza, lista degli ID consentiti e profili di permesso.
- Informazioni di sistema e azioni remote autorizzate, soggette ai privilegi Windows.
- Tunnel TCP con autorizzazione e conferma locale della destinazione.
- Impostazioni di connessione, sicurezza, schermo, file, privacy e registrazione.
- Account e backup cifrati dell'identità, disponibili dopo la configurazione del relativo servizio.
- Download di aggiornamenti verificati tramite firma, versione, scadenza, sequenza, dimensione e hash.

Queste funzioni sono presenti nel codice; la loro affidabilità complessiva su PC e reti reali deve ancora essere validata. Audio di sistema, file tramite clipboard, controllo del desktop UAC e Ctrl+Alt+Canc non sono disponibili nella build attuale. Non viene dichiarata parità funzionale con AnyDesk.

## L'infrastruttura necessaria

Il relay è il servizio che mette in contatto i dispositivi e inoltra il traffico delle sessioni. Serve soprattutto quando i PC si trovano dietro router o su reti differenti. Le connessioni del prodotto attuale dipendono dalla disponibilità del relay; non viene dichiarato un percorso diretto automatico alternativo.

Il progetto include una directory PostgreSQL per conservare le identità, un servizio account e una configurazione con relay attivo e standby. In caso di guasto le sessioni aperte si interrompono e i client devono riconnettersi. Un solo server rimane un punto di guasto; la continuità effettiva richiede un ambiente con risorse separate e procedure operative verificate.

## Sicurezza prevista

Il programma verifica il certificato del relay e usa un ulteriore canale TLS 1.3 tra i due PC. Le identità dei dispositivi vengono controllate mediante chiavi e impronte, con conferma al primo collegamento. Il relay inoltra il contenuto cifrato della sessione. Segreti locali e chiavi private sono protetti tramite il meccanismo DPAPI di Windows; i backup dell'identità sono cifrati con una passphrase separata.

Sono implementati permessi di sessione, limiti ai tentativi, controlli sui trasferimenti e verifica degli aggiornamenti. La build pubblica richiede anche la firma Authenticode per identificare l'editore degli eseguibili. Questa firma è distinta dalla chiave che autentica gli aggiornamenti.

Queste misure costituiscono una base tecnica. Il protocollo non ha ancora ricevuto un audit indipendente e non si può presentare il prodotto come certificato o privo di vulnerabilità.

## Stato attuale

Il verbale locale del 18 ottobre 2026 riporta 52 test generali superati e quattro test PostgreSQL separati, oltre alla generazione di due eseguibili Windows dimostrativi. Le prove documentate coprono anche scenari negativi di autenticazione, backup, aggiornamenti e failover locale. Si tratta di risultati già registrati, non di una nuova campagna di test effettuata per questa presentazione.

Non sono ancora disponibili dominio e VPS del servizio pubblico. Le build attuali non sono firmate Authenticode e usano un profilo dimostrativo. Restano da svolgere prove su due PC e reti differenti, audit indipendente, verifiche di carico e ripristino sull'ambiente reale. Titolare, condizioni e informativa contengono dati di prova e richiedono sostituzione e revisione.

Anche le cartelle web `app/` e `dashboard/` presenti nel progetto non costituiscono un portale di amministrazione di produzione dichiarato per questa release.

## Proposta per procedere

La prossima fase è un pilota interno su un'infrastruttura di prova controllata. Occorre assegnare un responsabile tecnico e uno operativo, scegliere dominio e hosting, definire chi può utilizzare il servizio e approvare i costi di esercizio, firma e revisione.

Il pilota dovrà misurare riuscita delle connessioni, qualità su reti diverse, tempi di ripristino, consumo di banda e facilità d'uso. Il passaggio alla distribuzione esterna richiederà la chiusura dei problemi bloccanti, l'approvazione legale, un audit indipendente e una build firmata e verificata.

Il percorso operativo e i criteri di completamento sono descritti in [Prossimi passi](prossimi-passi.md). Le caratteristiche tecniche sono approfondite in [Applicazione e funzionalità](applicazione-e-funzionalita.md), mentre le prove già eseguite sono elencate nel [verbale di validazione](validation-2026-10-18.md).
