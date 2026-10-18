# RemoteDesk — prossimi passi per il rilascio

Piano operativo aggiornato al 18 ottobre 2026. Le responsabilità riportate indicano ruoli da assegnare, non persone già incaricate. Le fasi seguono l'ordine delle dipendenze; acquisto del certificato e revisione legale possono iniziare mentre si prepara il server.

## 1. Definire il perimetro e i responsabili

**Responsabili:** direzione e referente tecnico.

1. Stabilire se il primo utilizzo riguarda dipendenti, clienti selezionati o pubblico esterno.
2. Approvare le funzionalità della prima versione e dichiarare i limiti: file di 8 MiB, registrazione JPEG senza audio, assenza di UAC remoto e Ctrl+Alt+Canc.
3. Assegnare responsabilità per codice, infrastruttura, sicurezza, assistenza, privacy e approvazione delle release.
4. Definire budget per dominio, server, backup, certificato di firma e audit, oltre alla gestione continuativa.
5. Concordare obiettivi misurabili per disponibilità, utenti simultanei, tempi di risposta e ripristino. Non sono ancora stati misurati né garantiti.

**Completamento:** perimetro del pilota, responsabili e budget approvati in un documento interno.

## 2. Versionare e proteggere il progetto

**Responsabile:** sviluppo.

1. Creare la repository GitHub privata e pubblicare sorgenti, test, configurazioni di esempio e documentazione.
2. Escludere ambienti virtuali, build, backup, chiavi, password, configurazioni reali e cartelle temporanee. Controllare i file preparati per il commit prima del push.
3. Invitare solo i collaboratori necessari e concordare revisione delle modifiche e protezione del ramo principale, secondo le funzionalità disponibili sull'account.
4. Controllare l'esito delle workflow CI dopo il primo push; correggere eventuali errori prima di considerare verificata la build in GitHub.
5. Definire una copia di sicurezza indipendente del repository e una procedura di recupero degli accessi.

**Completamento:** URL privato verificato, commit pubblicato e controlli CI esaminati. La presenza del codice su GitHub non equivale alla disponibilità del servizio.

## 3. Preparare dominio e ambiente server

**Responsabile:** infrastruttura. **Dipendenze:** fase 1.

1. Acquistare un dominio intestato al titolare e predisporre account del provider protetti con autenticazione multifattore.
2. Creare gli indirizzi DNS per relay, account, aggiornamenti e pagine di assistenza, condizioni e privacy.
3. Preparare Linux x86_64 compatibile con i lockfile e Python 3.13, un utente di servizio dedicato, firewall, accesso amministrativo controllato e aggiornamenti del sistema.
4. Predisporre PostgreSQL con ruoli separati usando `deploy/postgres-roles.sql`; mantenere il database su rete privata e verificare TLS e hostname con `sslmode=verify-full`.
5. Ottenere certificati TLS validi per i servizi e automatizzarne rinnovo e controllo della scadenza. Non usare i certificati dimostrativi locali.
6. Installare le dipendenze bloccate con hash e le unità systemd come indicato in [production-readiness.md](production-readiness.md).
7. Configurare il relay cluster e il servizio account, proteggendo credenziali e chiavi fuori dal repository. Definire il codice di registrazione account e il gruppo autorizzato a utilizzarlo.
8. Per continuità operativa predisporre un nodo standby su risorsa separata e, se usato, HAProxy TCP. Configurare gli IP attendibili per PROXY v1 e bloccare gli accessi diretti non previsti ai backend. Non usare transaction pooling per la connessione che mantiene il lock di leadership.

**Completamento:** due PC esterni raggiungono il relay e il servizio account con certificati verificabili; il database non è esposto pubblicamente; i servizi ripartono dopo riavvio. Un ambiente su singola VPS può servire al pilota, ma non dimostra alta disponibilità.

## 4. Preparare backup, monitoraggio e risposta agli incidenti

**Responsabile:** infrastruttura, con referente sicurezza. **Dipendenze:** fase 3.

1. Configurare backup PostgreSQL cifrati con `deploy/backup-postgres.sh`, copie fuori dal server e una retention approvata. Conservare separatamente chiavi di recupero, ruoli, certificati e configurazioni necessari al ripristino.
2. Ripristinare un backup su un database vuoto e verificare la conservazione delle identità e degli account. Misurare il tempo e la perdita massima di dati osservata.
3. Monitorare disponibilità, spazio disco, errori, scadenze TLS ed esito dei backup. Definire destinatari e tempi di intervento per gli allarmi.
4. Simulare arresto del relay, perdita del database, guasto di un nodo e partizione di rete. Verificare riconnessione dei client e isolamento del vecchio nodo prima della promozione quando necessario.
5. Definire conservazione dei log, minimizzazione dei dati, gestione degli abusi, revoca delle credenziali e procedura per un aggiornamento urgente.

**Completamento:** prova di ripristino registrata, allarmi ricevuti e procedura di intervento assegnata. Le sessioni aperte non vengono trasferite allo standby e devono riconnettersi.

## 5. Completare titolarità e documenti legali

**Responsabili:** titolare e consulente legale/privacy.

1. Sostituire i dati dimostrativi in `legal/` con denominazione, contatti e riferimenti reali.
2. Definire licenza proprietaria, utilizzi consentiti, assistenza e condizioni economiche, se previste.
3. Descrivere dati trattati, finalità, conservazione, fornitori, procedure di cancellazione e recupero, e responsabilità delle parti.
4. Verificare gli avvisi e le condizioni delle dipendenze effettivamente distribuite, inclusi Python, Tcl/Tk e OpenSSL.
5. Pubblicare condizioni e informativa agli URL definitivi e ottenere un'approvazione registrata prima del rilascio esterno.

**Completamento:** documenti approvati con dati reali e URL HTTPS funzionanti. I modelli attuali non costituiscono approvazione legale.

## 6. Configurare firma e aggiornamenti

**Responsabile:** referente delle release. **Dipendenze:** identità reale del titolare e URL definitivi.

1. Ottenere un certificato Authenticode intestato al titolare e attivare token, provider o servizio di firma richiesto dall'emittente. Verificare la compatibilità con SignTool e l'accessibilità del certificato tramite archivio utente, come previsto dallo script attuale; i servizi cloud possono richiedere un'integrazione specifica.
2. Installare gli strumenti di firma Windows SDK e verificare la selezione del certificato per impronta. Definire chi può firmare, come viene autorizzata una release e come gestire revoca e scadenza.
3. Generare la chiave Ed25519 degli aggiornamenti con `scripts/release.py keygen` su un ambiente protetto. Conservare la chiave privata cifrata fuori dal repository e prevedere un backup protetto. È distinta dalla credenziale Authenticode.
4. Copiare `deploy/client-profile.example.json` in `deploy/client-profile.json` e inserire dominio relay, API account, manifest aggiornamenti, chiave pubblica, titolare e URL reali. Non inserirvi segreti.
5. Preparare l'hosting HTTPS degli aggiornamenti e un processo che pubblichi prima l'eseguibile completo e poi il relativo manifest firmato. Il manifest va generato dopo la firma Authenticode, perché la firma modifica il file e il suo hash.
6. Provare una nuova versione e il rifiuto di manifest alterati, scaduti o con sequenza precedente. Documentare il recupero da una pubblicazione errata e la futura rotazione delle chiavi. L'installazione resta avviata esplicitamente dall'utente.

**Completamento:** firma verificabile su entrambi gli eseguibili e aggiornamento verificato da un client precedente. Queste prove si effettuano senza attestare anticipatamente il completamento dell'audit o dell'accettazione Windows.

## 7. Eseguire il pilota e l'audit indipendente

**Responsabili:** qualità, utenti pilota e revisore di sicurezza. **Dipendenze:** ambiente di prova operativo.

1. Eseguire la suite locale e le workflow CI registrando commit, piattaforme, versioni e risultati. Il [verbale esistente](validation-2026-10-18.md) descrive prove precedenti e non sostituisce l'accettazione della release candidata.
2. Provare almeno due PC Windows reali su reti differenti, NAT, perdita e ripresa della rete, timeout e latenza elevata.
3. Verificare consenso, rifiuto, permessi, whitelist, primo contatto, variazione dell'impronta, accesso con password e revoca. Dopo l'interruzione non devono proseguire input, trasferimenti o tunnel della sessione terminata.
4. Provare monitor multipli, DPI e risoluzioni differenti, finestra ridotta, leggibilità dell'ID e tutte le sottosezioni delle impostazioni. Registrare i limiti dovuti ai privilegi Windows.
5. Provare file interrotti, nomi in conflitto, limite dimensionale, cartelle non autorizzate, chat, clipboard e registrazioni.
6. Provare registrazione e recupero account, logout, cancellazione, backup alterati, passphrase errata e ripristino su un nuovo profilo vuoto. Non usare lo stesso ID simultaneamente su più PC.
7. Misurare connessioni simultanee, CPU, memoria e traffico del relay per scegliere il dimensionamento. Non attribuire al sistema una capacità prima di averla misurata.
8. Commissionare un audit su protocollo, identità, consenso, file, tunnel, account, aggiornamenti e dipendenze. Risolvere i problemi bloccanti e far verificare le correzioni.

**Completamento:** verbale di accettazione Windows, misure del pilota e rapporto di audit con esito dei problemi. Solo dopo l'effettiva esecuzione registrare le relative attestazioni nel profilo di rilascio.

## 8. Creare e distribuire la release

**Responsabile:** referente delle release. **Dipendenze:** fasi precedenti concluse.

1. Assegnare una versione coerente nei sorgenti e nel manifest e registrare il commit approvato.
2. Attivare `-PublicRelease` con profilo reale, impronta del certificato e URL timestamp RFC 3161 fornito dall'emittente. I controlli devono passare senza aggirare domini, documenti o attestazioni.
3. Verificare entrambi gli eseguibili, gli hash calcolati dopo la firma, gli avvisi delle dipendenze e l'inventario SBOM prodotti dalla build.
4. Pubblicare artefatti e manifest con sequenza crescente e scadenza appropriata. Conservare una copia della release, del verbale e delle istruzioni di ripristino.
5. Distribuire inizialmente a un gruppo limitato, controllare errori e richieste di assistenza, poi approvare l'estensione del servizio.

Comando previsto, da eseguire quando i prerequisiti sono soddisfatti:

```powershell
.\scripts\build-windows.ps1 -Profile deploy/client-profile.json -PublicRelease `
  -SigningThumbprint IMPRONTA_CERTIFICATO `
  -TimestampUrl URL_RFC3161_DEL_FORNITORE
```

**Completamento:** build firmata e verificata consegnata agli utenti autorizzati, monitoraggio attivo e responsabilità operative assegnate.

## Evoluzioni successive da valutare

Audio remoto, trasferimenti più grandi, servizio Windows per UAC e Ctrl+Alt+Canc, installer e portale di amministrazione richiedono progettazione e validazione dedicate. La gestione attuale degli account non offre recupero tramite email. Il relay non supporta più nodi attivi che distribuiscano contemporaneamente le sessioni. Ogni estensione deve essere pianificata con criteri di accettazione e revisione delle implicazioni di sicurezza.

Per configurazioni, comandi server e limitazioni operative vedere [Preparazione alla distribuzione](production-readiness.md) e [Sicurezza e distribuzione](security-deployment.md).
