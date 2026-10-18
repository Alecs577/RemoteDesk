# Preparazione alla distribuzione RemoteDesk

## Stato e confini

Il progetto include ora client con profilo incorporato, account con vault cifrato per dispositivo,
recupero account tramite codice, backup/ripristino identità, download aggiornamenti verificati,
directory PostgreSQL e relay attivo/standby. Le sessioni tra i PC mantengono il tunnel TLS esistente.
Il verbale delle verifiche locali è in [validation-2026-10-18.md](validation-2026-10-18.md).

Non viene dichiarata una certificazione di sicurezza. La verifica sul codice e i test locali non
costituiscono un audit indipendente. Per una release pubblica restano necessarie credenziali reali,
revisione dei documenti legali e prove su Windows e sull'infrastruttura effettivamente scelta.
I dati del titolare sono dimostrativi su richiesta del committente.

## Client con relay incorporato

`deploy/client-profile.example.json` contiene valori dimostrativi su domini `.invalid`.
Copiarlo in `deploy/client-profile.json` e compilare gli indirizzi reali. Il campo relay è
`hostname:porta`; accounts_url è l'origine HTTPS dell'API senza `/v1`; updates_url è l'URL
HTTPS del manifest firmato. Inserire la chiave **pubblica** Ed25519, il titolare e gli URL
di assistenza, condizioni e privacy. Le chiavi private e le password non appartengono al profilo.

```powershell
.\scripts\build-windows.ps1 -Profile deploy/client-profile.json
```

Il profilo viene generato temporaneamente dentro il pacchetto, incorporato in entrambi gli
eseguibili e rimosso dai sorgenti al termine. Le installazioni nuove e quelle con relay vuoto
usano il relay della build. Un relay personalizzato esplicito rimane valido. Non viene contattato
un server di terzi in assenza di un profilo configurato. Il profilo dimostrativo non è raggiungibile.
Il relay predefinito viene salvato come preferenza vuota: una build successiva può aggiornare
il dominio senza lasciare il client vincolato al server precedente. I valori personalizzati
vengono invece conservati. Il profilo è bloccato tra processi per impedire due istanze o un
ripristino concorrente; una configurazione corrotta viene preservata e causa un errore esplicito.

I controlli pubblici si attivano con `-PublicRelease`: rifiutano domini dimostrativi, chiave
mancante, titolare dimostrativo, documenti incompleti e attestazioni mancanti. Le attestazioni
sono registrazioni dell'operatore: impostarle a true non esegue né sostituisce audit o prove.

## Account e recupero identità

In Impostazioni → Informazioni → Account e backup identità sono disponibili registrazione,
accesso, recupero account, esportazione e caricamento del backup cifrato, download e logout.
Le sessioni account durano un'ora, rimangono in memoria nel client e sono salvate solo come
hash sul servizio. Le password account hanno almeno 16 caratteri e sono derivate con
PBKDF2-HMAC-SHA256 a 600.000 iterazioni. Le registrazioni sono su invito, con codice
configurabile sul server; un codice vuoto disabilita la registrazione.

L'account raccoglie fino a 20 backup di dispositivi. Non conferisce accesso remoto automatico
e non sostituisce il consenso/whitelist/password del PC. Gli ID di sessione restano assegnati
dal relay al dispositivo. Ogni PC attivo deve avere un ID distinto. Il ripristino serve a
**sostituire** un PC dismesso, non a clonare contemporaneamente l'identità su più computer.
Una nuova registrazione dello stesso ID sostituisce la connessione precedente.

Il backup contiene ID, relay, segreto di registrazione, chiave privata e certificato, cifrati
con AES-256-GCM e una chiave derivata mediante scrypt (N=131072, r=8, p=1) da una passphrase
separata di almeno 16 caratteri. Il server riceve solo il contenitore cifrato. La passphrase
non è recuperabile dal servizio. Il recupero password account usa un codice casuale mostrato
una volta: dopo l'utilizzo viene ruotato e tutte le sessioni account vengono revocate.
Non è previsto un recupero via email o l'identificazione anagrafica dello username.

Per ripristinare su un nuovo PC **prima del primo avvio**, usare lo strumento console incluso:

```powershell
.\RemoteDeskTools.exe identity restore --file C:\Backup\mio-pc.rdbackup
.\RemoteDeskTools.exe identity cloud-restore --username alex --device pc-principale
```

Le password sono richieste interattivamente. Il ripristino richiede il profilo `%APPDATA%\RemoteDesk`
vuoto e l'app chiusa; rifiuta la sovrascrittura. Su un PC già usato, chiudere il programma,
archiviare il profilo esistente in una cartella sicura e preparare un profilo vuoto prima
di riprovare. Chiave e segreto vengono protetti con la DPAPI del nuovo utente. Password
di accesso non presidiato, impronte attendibili e permessi non vengono trasferiti: devono
essere autorizzati di nuovo. Un ripristino interrotto lascia un marcatore che blocca l'avvio
fino all'ispezione del profilo, per evitare un'identità parzialmente sostituita.

## Server: PostgreSQL e standby

Sono disponibili due percorsi: relay locale legacy con JSON per sviluppo; `relay --cluster`
con PostgreSQL per persistenza condivisa e selezione di un unico nodo attivo. Usare il secondo
per la distribuzione prevista. PostgreSQL mantiene gli ID; la connessione dedicata del leader
mantiene un advisory lock di sessione. Lo standby attende prima di aprire la porta. La perdita
del database interrompe il relay attivo. Non usare PgBouncer in transaction pooling per questa
connessione: il lock appartiene alla sessione PostgreSQL.

Questo modello supporta più processi avviati ma **un solo relay attivo**. Non è un relay
attivo/attivo e non distribuisce contemporaneamente sessioni tra nodi. Le sessioni aperte
si interrompono in caso di guasto: dopo promozione dello standby i client si riconnettono.
Il watchdog controlla il collegamento al database ogni secondo; timeout SQL/connessione
possono aggiungere alcuni secondi. Un ambiente con partizioni di rete richiede fencing
operativo del vecchio nodo: il solo lock non è una garanzia di esclusione istantanea di
socket già aperti su una macchina isolata. Anche PostgreSQL e il bilanciatore devono avere
una strategia di disponibilità; una singola VPS resta un punto di guasto.

Installazione target: Linux x86_64, glibc compatibile manylinux_2_28, Python 3.13:

```sh
python3.13 -m venv /opt/remotedesk/venv
/opt/remotedesk/venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements/linux-relay.lock
/opt/remotedesk/venv/bin/python -m pip install --no-deps --no-build-isolation .
```

Configurare `deploy/systemd/cluster.env.example` e installare
`deploy/systemd/remotedesk-relay-cluster.service` su entrambi i nodi. Usare lo stesso
PostgreSQL, certificati validi per il dominio del relay e una rete privata per database/backend.
Il certificato CA del database deve essere attendibile per il servizio; usare `sslmode=verify-full`.
`deploy/postgres-roles.sql` predispone ruoli e schemi separati per relay e account;
impostare le password interattivamente e usare il ruolo corrispondente in ciascun env.
Proteggere i file env con permessi 0600 root:root e i PEM con root:remotedesk 0640.
Non abilitare contemporaneamente le unità legacy e cluster sulla stessa porta.

`deploy/haproxy.cfg` è un esempio di bilanciamento TCP passante: il TLS termina nel relay.
La porta TCP aperta identifica il nodo eletto. Con `send-proxy` configurare gli IP esatti
del bilanciatore in `REMOTEDESK_TRUSTED_PROXIES` e bloccare via firewall ogni altro accesso
ai backend. Il parser accetta soltanto PROXY v1 completo da tali indirizzi; il limite per IP
usa l'indirizzo originale. Senza bilanciatore lasciare la variabile vuota: nessun header PROXY
viene interpretato. Non usare un proxy TCP generico che nasconda tutti i client dietro un IP.

Per migrare la directory JSON fermare il vecchio relay, eseguire un backup verificato e:

```sh
python -m remotedesk relay --import-owners --owners-file /var/lib/remotedesk/owners.json \
  --certfile /etc/remotedesk/tls/fullchain.pem --keyfile /etc/remotedesk/tls/privkey.pem
```

L'importazione è transazionale e rifiuta un database già popolato. Non viene eseguita
automaticamente. Non ripristinare una copia vecchia della directory sopra il servizio vivo.
Per il backup cifrato è disponibile `deploy/backup-postgres.sh`: richiede pg_dump, age,
credenziali protette tramite PGSERVICE/.pgpass e chiave pubblica AGE_RECIPIENT. Usare un
ruolo di backup con accesso in lettura a entrambi gli schemi. Conservare la chiave privata
offline, copiare i backup fuori dalla VPS e provare age/pg_restore su un database vuoto
prima della messa online. Le chiavi TLS, le credenziali e i ruoli PostgreSQL vanno salvati
separatamente; pg_dump non include i ruoli globali. Definire retention e monitoraggio degli
esiti: lo script non cancella automaticamente copie precedenti.
La rimozione di un'identità va gestita solo dopo verifica del proprietario; non esiste una
API pubblica che consenta di cancellare ID arbitrari.

## API account

`python -m remotedesk accounts --certfile ... --keyfile ...` richiede PostgreSQL e TLS 1.3.
L'unità e l'env sono in deploy/systemd/remotedesk-accounts.service e accounts.env.example.
La porta di esempio è 8771, con certificato per il dominio accounts. L'API espone solo
POST JSON sotto `/v1/` e rifiuta richieste con Origin, body eccessivi, redirect e framing
ambiguo. I client inviano bearer token nell'header Authorization, mai nell'URL. I percorsi
autenticati sono devices, upload, download, delete-device, delete-account e logout.
Le operazioni del vault sono sempre vincolate all'account della sessione.

Il servizio usa al massimo 32 worker e timeout per i socket. I limiti account sono nel
database condiviso: 60 richieste/IP in 10 minuti, 5 registrazioni/IP/ora, 10 login/username
in 10 minuti e 5 recuperi/username/ora. Per iniziare esporre direttamente HTTPS; un proxy
non configurato nasconde gli IP e altera i limiti. Non interpretare X-Forwarded-For arbitrari.
Per mitigare abusi volumetrici servono comunque firewall e protezioni del provider.

I contatori del relay restano locali al nodo attivo e si azzerano al failover; la directory
persistente non viene persa. Non presentare questa mitigazione come autenticazione obbligatoria
per l'allocazione degli ID: il relay conserva l'assegnazione anonima con prova di possesso.

## Aggiornamenti e firma

Generare una chiave di aggiornamento offline, fuori dal repository:

```powershell
.venv-build\Scripts\python.exe scripts/release.py keygen --private-key D:\Offline\update-private.pem --public-key D:\Offline\update-public.txt
```

La chiave privata è PKCS8 cifrata; la password viene chiesta interattivamente. Inserire
solo la chiave pubblica nel profilo. Il certificato Authenticode è una credenziale distinta,
da ottenere dal titolare; non viene generata una firma dimostrativa per fingere una release.

```powershell
.\scripts\build-windows.ps1 -Profile deploy/client-profile.json -PublicRelease `
  -SigningThumbprint IMPRONTA_CERTIFICATO -TimestampUrl URL_TIMESTAMP_DEL_PROVIDER
```

Entrambi gli eseguibili vengono firmati SHA-256 e verificati con SignTool. Non vengono
pubblicati o caricati automaticamente. Il manifest va generato dopo la firma:

```powershell
.venv-build\Scripts\python.exe scripts/release.py manifest --private-key D:\Offline\update-private.pem `
  --artifact dist\RemoteDesk.exe --version 0.2.0 --url https://updates.DOMINIO/RemoteDesk-0.2.0.exe `
  --sequence 1 --expires-days 14 --output dist\manifest.json
```

La versione deve coincidere con quella realmente compilata e superare la versione installata.
Incrementare sequence a ogni pubblicazione. Distribuire artefatto e manifest su HTTPS:
deploy/Caddyfile.example mostra una configurazione statica. Il client verifica firma Ed25519,
piattaforma, scadenza, sequenza, versione, dimensione e SHA-256 prima di consegnare il file.
I file parziali vengono rimossi. Il pulsante nelle Informazioni scarica la nuova versione;
l'avvio resta una scelta esplicita dell'utente. Non è un updater silenzioso né un installer.
La sequenza massima è salvata localmente; la scadenza riduce la finestra di riuso dei manifest
su una nuova installazione. Mantenere l'orologio del PC corretto. La rotazione della chiave
pubblica richiede una release fidata che incorpori la nuova chiave.

## Dipendenze, licenze e privilegi

I lockfile in requirements bloccano versioni e SHA-256 delle wheel, separati per Windows
x64/Python 3.13 e Linux x86_64/Python 3.13. L'installazione di release usa --require-hashes
e solo wheel; non esegue risoluzioni libere. Cambiare piattaforma o versione di Python
richiede rigenerare e verificare il lock. scripts/generate-locks.py usa wheel già scaricate;
non scegliere automaticamente nuove versioni durante una release.

La build raccoglie gli avvisi dalle dipendenze effettive, Python, Tcl/Tk e OpenSSL. Le licenze
native originali e i loro hash sono in legal/runtime-licenses. Se cambia il runtime, eseguire
scripts/fetch-runtime-licenses.py, controllare le fonti e aggiornare i documenti. La build
include THIRD_PARTY_NOTICES.txt e un inventario sbom.json, anche accanto agli eseguibili.

Il client usa i privilegi dell'utente Windows corrente. Un input rifiutato ora viene segnalato
senza chiudere la sessione; gli input malformati vengono ignorati. L'assenza di privilegi non
viene aggirata. Desktop UAC, Ctrl+Alt+Canc e alcune operazioni protette richiedono un servizio
Windows con progettazione e distribuzione dedicate: non dichiarare queste funzioni disponibili
in questa versione. Il blocco input rifiutato viene già riportato tramite risposta dell'azione.

## Attività esterne ancora necessarie

Dominio/DNS, VPS, PostgreSQL operativo, certificati relay/account, hosting degli aggiornamenti,
fencing/backup/monitoraggio e accessi al provider appartengono all'ambiente server. Per la
pubblicazione servono anche certificato Authenticode reale, custodia delle chiavi, audit
indipendente e approvazione dei documenti con dati veri. Nessuno di questi fatti può essere
attestato soltanto modificando il repository.

Prima di abilitare -PublicRelease eseguire prove su due PC, consenso/rifiuto, password,
revoca della sessione, trasferimenti interrotti, backup e ripristino su profilo vuoto,
logout/reset account e perdita del relay/database. Verificare il passaggio allo standby
con firewall e bilanciatore reali e misurare i tempi di riconnessione. Annotare versione,
hash, piattaforme, esiti e problemi residui nel verbale della release.
