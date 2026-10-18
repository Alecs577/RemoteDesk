# RemoteDesk: applicazione e funzionalità

Aggiornamento: 18 ottobre 2026. Per una lettura destinata alla direzione consultare la [presentazione del prodotto](presentazione-remotedesk.md); per il percorso di rilascio consultare i [prossimi passi dettagliati](prossimi-passi.md).

Questo documento descrive **RemoteDesk 0.1.0 come implementato nel repository**. È una guida al prodotto e alla sua architettura, non una certificazione di sicurezza o una dichiarazione di parità con AnyDesk. Il client desktop è progettato principalmente per Windows; il relay può essere eseguito come servizio Linux. Le funzioni segnalate come incomplete non vanno presentate come disponibili in una release.

## Scopo e componenti

RemoteDesk consente a una persona di vedere e, se autorizzata, controllare un altro PC per assistenza remota. La stessa applicazione desktop può essere il **PC host**, che condivide la propria scrivania, o il **client**, che apre la scrivania di un altro dispositivo.

I componenti principali sono:

- **Applicazione Windows** (`remotedesk/host.py`): interfaccia Tkinter per ID locale, collegamento a un ID remoto, consenso, sessione, contatti e impostazioni.
- **Cattura e visualizzazione** (`remotedesk/capture.py`, `remotedesk/host.py`): cattura dello schermo host in JPEG e rendering sul client, con selezione del monitor e profili di qualità.
- **Relay** (`remotedesk/relay.py`): mantiene la directory delle identità, riceve le registrazioni host, autentica il client e mette in comunicazione i due socket. Lo stato delle sessioni attive è in memoria.
- **Protocollo di sessione** (`remotedesk/relay_session.py`, `remotedesk/tunnel.py`, `remotedesk/tls_channel.py`): trasporta messaggi di immagine, input, chat, appunti, file e azioni dentro un canale TLS tra i due PC.
- **Configurazione e identità** (`remotedesk/config.py`, `remotedesk/identity.py`, `remotedesk/secure_store.py`): salva preferenze, ID, chiavi, password e impronte dei dispositivi conosciuti.

Il relay richiede un indirizzo `hostname:porta` configurato nei client e un certificato TLS valido per l'hostname. Senza relay configurato l'ID non viene assegnato e le sessioni via Internet non funzionano. Il visualizzatore HTTP locale presente nel codice non viene avviato dall'applicazione desktop.

## Primo avvio e identità

Al primo avvio l'app genera una chiave privata e un certificato per il PC e un segreto di registrazione. Il relay assegna un **ID numerico di nove cifre** a una nuova identità; l'interfaccia mostra “In assegnazione” finché l'operazione non riesce. È possibile scegliere un alias leggibile, copiare l'ID e usarlo per farsi contattare.

L'impronta SHA-256 del certificato serve a verificare l'identità crittografica del dispositivo. È consultabile in **Impostazioni → Informazioni** e compare quando si conferma per la prima volta un peer remoto. Non è mostrata nel riquadro principale “Questa scrivania”. L'impronta è pubblica: nasconderla dalla schermata iniziale riduce l'ingombro visivo, ma non sostituisce la verifica del peer.

La configurazione Windows è in `%APPDATA%\RemoteDesk\config.json`. Il segreto e la chiave privata sono protetti tramite DPAPI per l'utente Windows corrente; il file di configurazione conserva anche impostazioni, contatti, ID consentiti e impronte già accettate. La password di accesso è salvata come hash, non come testo. Per un backup o una migrazione occorre considerare che i dati DPAPI sono legati all'account Windows.

## Avvio di una sessione

### Con consenso manuale

1. L'host si collega al relay e rende disponibile il proprio ID.
2. Il client inserisce l'ID o un alias della rubrica e chiede la connessione.
3. Il relay autentica le identità e inoltra i dati tra i PC.
4. L'host riceve una richiesta e decide se accettare o rifiutare. Può scegliere i permessi per quella sessione.
5. Solo dopo l'accettazione vengono trasmessi i fotogrammi e applicati i permessi accordati.

L'host può terminare la sessione con **Interrompi**. L'interruzione chiude il tunnel attivo, annulla le azioni pendenti e ferma la condivisione.

### Con password

L'host può impostare una password per l'accesso non presidiato. La password nuova deve avere almeno 12 caratteri e non superare 128 byte UTF-8. Chi accede con password riceve il **profilo di permessi predefinito** configurato dall'host; non appare una nuova finestra di consenso per ogni collegamento. I tentativi falliti sono limitati temporaneamente per ID remoto. La lista degli ID consentiti continua ad applicarsi.

### Con invito

Un utente può creare un invito destinato a un altro ID, scegliere i permessi e una scadenza di 1, 2, 5 o 10 minuti. Il token dell'invito è firmato dal mittente e cifrato per il destinatario. Un invito valido autorizza la sessione con il proprio profilo di permessi e può essere usato anche quando l'host ha una password configurata. Il relay deve confermare l'invio dell'invito perché questo venga registrato come attivo.

### Contatti e recenti

La rubrica associa alias a ID di nove cifre. La schermata principale mostra i contatti salvati e fino a 12 connessioni recenti. Da queste liste si può avviare un nuovo collegamento.

## Funzioni durante la sessione

Le funzioni sono soggette ai permessi concessi dall'host. Alcune dipendono anche dai privilegi che Windows concede al processo.

- **Schermo remoto:** visualizzazione, scelta del monitor o di tutti i monitor, adattamento dell'immagine, qualità “fluida”, “bilanciata” o “nitida”, vista a schermo intero e indicazione del puntatore remoto.
- **Controllo:** mouse e tastiera del client possono agire sull'host quando il permesso di controllo è attivo.
- **Chat e appunti testuali:** scambio di messaggi e testo della clipboard se abilitati.
- **File:** invio e ricezione tramite offerte, conferma opzionale dell'host, scelta della cartella e regola per nomi già esistenti. Il file manager remoto elenca percorsi sotto la cartella home dell'host. Il limite per singolo file nel protocollo attuale è **8 MiB**. I file in ingresso sono scritti in un temporaneo e diventano visibili al percorso definitivo solo al completamento.
- **Informazioni di sistema:** richiesta di dati sul sistema remoto se autorizzata.
- **Azioni sul PC remoto:** blocco della workstation, richiesta di riavvio o disconnessione dell'utente, blocco dell'input locale, modalità privacy e disegno sullo schermo, ciascuna secondo il relativo permesso e i vincoli Windows. Riavvio e disconnessione utente hanno un conto alla rovescia locale annullato alla fine della sessione. Ctrl+Alt+Canc non è supportato in questa build.
- **Tunnel TCP:** su permesso esplicito, l'host chiede anche conferma locale della destinazione `host:porta` prima di aprire il collegamento. Il tunnel viene chiuso con la sessione.
- **Schermata e registrazione:** il client può salvare un fotogramma JPEG. La registrazione, se autorizzata, salva una sequenza di immagini JPEG nella cartella configurata; non produce un file video con audio.

I permessi comprendono inoltre opzioni presenti nell'interfaccia che **non sono implementate in questa build**: acquisizione e riproduzione dell'audio di sistema, trasferimento di file tramite clipboard, Ctrl+Alt+Canc e protezione delle finestre di sistema. Restano disattivate nell'interfaccia; Ctrl+Alt+Canc e il desktop UAC richiedono un servizio Windows dedicato.

## Impostazioni disponibili

Le impostazioni sono organizzate in sezioni:

- **Applicazione → Generale:** alias locale, avvio automatico con Windows.
- **Applicazione → Connessione:** indirizzo del relay, durata degli inviti e tempo massimo di attesa di una risposta.
- **Sicurezza → Accesso:** richieste con conferma manuale, impostazione o rimozione della password, lista degli ID consentiti e rimozione delle impronte salvate.
- **Sicurezza → Profili di permesso:** valori proposti durante il consenso manuale e usati per l'accesso con password; l'invito può usare permessi specifici.
- **Sicurezza → Privacy:** indicatore locale della condivisione con pulsante Interrompi.
- **Sessione → Schermo:** qualità, adattamento, puntatore, schermo intero e cartella delle schermate salvate.
- **Sessione → Audio:** mostra lo stato di indisponibilità dell'audio in questa build.
- **Sessione → File:** conferma alla ricezione, scelta della cartella, cartella predefinita e gestione dei conflitti di nome.
- **Sessione → Registrazione:** avvio automatico se consentito, intervallo tra le immagini e cartella di destinazione.
- **Informazioni:** versione, ID assegnato e impronta del certificato locale.

## Sicurezza delle connessioni

Il client verifica il certificato del relay e il nome DNS. Il traffico tra i due PC usa un ulteriore canale TLS 1.3; il relay inoltra i byte cifrati di questo canale. La chiave pubblica del peer deve corrispondere alla directory del relay. Al primo contatto viene chiesta conferma dell'impronta e quella accettata viene ricordata per i collegamenti successivi. Le registrazioni, gli inviti e l'accesso al tunnel richiedono prove crittografiche di possesso della chiave privata.

La libreria Python **`cryptography`** implementa la gestione delle chiavi RSA, dei certificati X.509, delle firme e della cifratura dei token d'invito; è una dipendenza dichiarata in `requirements.txt` e `pyproject.toml`. Non è il relay e non è un programma separato che l'utente finale deve aprire: viene inclusa nell'ambiente Python o nella build dell'applicazione.

Il relay limita il numero di socket e applica limiti temporanei per IP. I permessi di sessione assenti o malformati vengono interpretati in modo restrittivo. Le operazioni asincrone di file e tunnel sono associate alla sessione che le ha avviate, per evitare che una risposta tardiva venga inoltrata a un peer successivo.

Questi controlli non dimostrano da soli che un servizio pubblico sia sicuro. Per dettagli su deploy, backup, rischi e verifiche necessarie si veda [Sicurezza e distribuzione](security-deployment.md).

## Limiti attuali e stato del rilascio

- È necessario predisporre un dominio, un VPS e un certificato TLS per il relay pubblico. Non sono inclusi nel repository.
- Account, vault cifrato per dispositivo e recupero dell'identità sono disponibili con servizio account configurato. Il recupero richiede la passphrase del backup; il codice account reimposta solo la password account.
- Gli aggiornamenti vengono scaricati dopo verifica di firma Ed25519, scadenza, versione, sequenza, dimensione e hash. La build pubblica richiede anche Authenticode; non viene eseguita un'installazione silenziosa.
- Il relay supporta una directory PostgreSQL condivisa e nodi attivo/standby dietro bilanciatore TCP attendibile. Le sessioni interrotte richiedono riconnessione; non è un sistema attivo/attivo. Il percorso JSON locale rimane disponibile per sviluppo.
- Alcuni controlli dipendono dai privilegi Windows e potrebbero essere rifiutati dal sistema.
- Le dipendenze hanno lockfile con hash per Windows x64 e Linux x86_64 con Python 3.13. La pipeline raccoglie avvisi e inventario delle dipendenze e può firmare la build con un certificato reale. Il protocollo non ha ricevuto un audit indipendente.
- Condizioni e informativa sono predisposte con dati dimostrativi su richiesta del committente. Prima della distribuzione pubblica occorrono dati reali e revisione legale. Le licenze effettive delle dipendenze vengono raccolte nella build.

Il dettaglio operativo, le configurazioni e le limitazioni rimaste sono in [production-readiness.md](production-readiness.md).

Una release pubblica richiede prove end-to-end con due PC Windows e un relay esterno, inclusi rifiuto, revoca, perdita di rete, trasferimenti interrotti e ripristino del database delle identità. La guida [Sicurezza e distribuzione](security-deployment.md) elenca i passaggi operativi.
