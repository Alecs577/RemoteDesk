# Sicurezza e distribuzione RemoteDesk

Le connessioni client-relay e il tunnel tra i due PC richiedono TLS 1.3. Il relay termina TLS solo sul proprio collegamento e inoltra il tunnel interno cifrato senza decifrarlo. I client verificano il certificato TLS del relay tramite il trust store del sistema; per il peer confrontano la chiave pubblicata dalla directory e, al primo collegamento, chiedono conferma dell'impronta.

## Installazione del relay Linux con systemd (modalità JSON per sviluppo)

Per il servizio pubblico PostgreSQL e attivo/standby, usare [production-readiness.md](production-readiness.md). Questa sezione descrive esclusivamente il percorso locale con archivio JSON.

Usa un hostname DNS stabile e un certificato CA valido per quel nome. Il client deve essere configurato con lo stesso `hostname:porta`. Il certificato e la chiave privata devono essere in PEM; il certificato deve includere la catena. Non salvare chiavi o archivio identità nel repository o nell'immagine del client.

1. Installa Python 3.11 o successivo e crea l'utente di servizio senza login. Copia il progetto in `/opt/remotedesk`, poi crea il virtualenv e installa il pacchetto:

   ```sh
   sudo useradd --system --home /var/lib/remotedesk --shell /usr/sbin/nologin remotedesk
   sudo python3 -m venv /opt/remotedesk/venv
   sudo /opt/remotedesk/venv/bin/pip install /percorso/al/progetto
   ```

2. Crea la directory di configurazione e installa l'unità e il file env di esempio:

   ```sh
   sudo install -d -o root -g remotedesk -m 0750 /etc/remotedesk/tls
   sudo install -o root -g root -m 0644 deploy/systemd/remotedesk-relay.service /etc/systemd/system/remotedesk-relay.service
   sudo install -o root -g root -m 0640 deploy/systemd/relay.env.example /etc/remotedesk/relay.env
   ```

   Modifica `/etc/remotedesk/relay.env` e configura il certificato TLS. La directory e la chiave privata devono essere leggibili dall'utente di servizio tramite il gruppo `remotedesk`; ad esempio usa `root:remotedesk` e permessi `0750` per la directory e `0640` per i file PEM. Non rendere la chiave accessibile agli altri utenti.

3. Avvia e controlla il servizio:

   ```sh
   sudo systemctl daemon-reload
   sudo systemctl enable --now remotedesk-relay
   sudo systemctl status remotedesk-relay
   sudo journalctl -u remotedesk-relay -f
   ```

4. Apri nel firewall solo la porta TCP configurata (`8770` di default). Il servizio non richiede chiamate a servizi esterni per avviarsi. I client non devono essere configurati con un indirizzo IP se il certificato è emesso per un hostname.

Aggiorna relay e client insieme: il protocollo TLS attuale usa `ALLOCATE` per emettere ID server-side. Le identità già presenti nell'archivio conservano il proprio ID; un'installazione con ID locale mai registrato riceve un nuovo ID quando si collega e deve condividere quello visualizzato dopo l'assegnazione.

Se aggiorni un relay esistente, controlla prima i suoi ID e i relativi proprietari: le registrazioni create dal vecchio modello first-claim restano nel database e non possono essere distinte automaticamente da una registrazione legittima.

Il servizio imposta un limite di 512 socket attivi per default, configurabile con `RELAY_MAX_CLIENTS` fino a 4096; ogni sessione occupa due socket. Ogni IP può creare al massimo 100 identità in un'ora; gli IP che accumulano 40 errori in 60 secondi vengono bloccati per 60 secondi. Questi limiti sono in memoria e si azzerano al riavvio. Il relay intercetta SIGTERM, chiude i socket attivi e scrive i log su journald. Dimensiona limite, memoria, file descriptor e firewall in base al VPS e al traffico atteso.

L'archivio identità predefinito è `/var/lib/remotedesk/owners.json`. Il relay scrive gli aggiornamenti in modo atomico e interrompe l'avvio se l'archivio inizializzato è mancante o malformato. Proteggi e includi nei backup sia `owners.json` sia `owners.json.initialized`, oltre alla chiave TLS. Conserva i backup cifrati e prova il ripristino. Mantieni una singola istanza del relay: lo stato degli host e degli abbinamenti è in memoria e l'archivio non è un database distribuito, quindi più repliche dietro un bilanciatore non sono supportate.

## Build del client Windows

Da Windows con Python 3.13 x64 e Tkinter installati (avviabile tramite `py -3.13` o `python.exe`), esegui dalla radice del repository:

```powershell
.\scripts\build-windows.ps1
```

Il risultato è `dist\RemoteDesk.exe`. Per una distribuzione pubblica, crea la build su una macchina Windows pulita e controllala su Windows supportati; firma l'eseguibile con Authenticode prima della pubblicazione e distribuisci hash SHA-256 tramite un canale separato. La pipeline supporta Authenticode e manifest Ed25519; il client scarica gli aggiornamenti dopo verifica. Vedi docs/production-readiness.md.

## Controlli di sicurezza presenti

- TLS 1.3 e verifica CA/nome del relay per le connessioni di controllo.
- TLS 1.3 end-to-end nel tunnel; la chiave del peer deve coincidere con quella della directory del relay.
- Prova di possesso della chiave privata per `JOIN`, peer authentication, registrazioni e inviti.
- Assegnazione casuale e persistente degli ID dal relay; la richiesta è firmata e idempotente, con limite di nuove identità per IP.
- Gli inviti sono cifrati per la chiave pubblica del destinatario e firmati dal mittente.
- Le chiavi host Windows e il segreto di registrazione sono protetti con DPAPI per l'utente Windows corrente; la chiave host è cifrata a riposo. Le installazioni legacy migrano al primo avvio.
- Nuove chiavi host RSA a 3072 bit; chiavi legacy RSA da almeno 2048 bit restano leggibili.
- Le nuove password sono salvate con PBKDF2-HMAC-SHA256 a 600.000 iterazioni; gli hash precedenti a 200.000 restano verificabili.
- Il relay limita socket attivi, applica blocchi temporanei agli IP che accumulano errori e si arresta in modo controllato.
- Le registrazioni host inattive vengono rimosse alla disconnessione; una nuova registrazione autenticata sostituisce la precedente. Le interrogazioni pubbliche della directory non azzerano il contatore degli errori.
- Il visualizzatore HTTP locale non parte insieme al client. Se avviato esplicitamente per sviluppo, accetta solo loopback e richieste dallo stesso origin, con una chiave di sessione casuale lunga. Non esporlo tramite proxy o inoltro di porta.
- I permessi di una risposta di sessione assente o malformata vengono negati; nuovi accessi non presidiati richiedono password di almeno 12 caratteri e i tentativi falliti sono limitati per identità remota.
- I file ricevuti vengono scritti in un file temporaneo nella cartella di destinazione e sostituiti nel percorso definitivo solo al completamento; un trasferimento interrotto elimina il temporaneo.
- Accettazione, rifiuto e invio di contenuti sono legati alla sessione peer corrente; i callback tardivi di una sessione chiusa non autorizzano quella successiva. Il tunnel interno serializza i record TLS prodotti da lettura e scrittura concorrenti.
- Le operazioni TCP approvate e il relativo thread di inoltro conservano la generazione della sessione che le ha avviate; una conferma o una risposta tardiva non viene inoltrata a un peer successivo.
- Le conferme dei file in ingresso, le risposte ai file in uscita e i blocchi inviati dal thread di trasferimento sono vincolati alla stessa generazione; i file temporanei incompleti vengono rimossi alla chiusura del tunnel.

## Preparazione alla distribuzione e limiti residui

La guida aggiornata è [production-readiness.md](production-readiness.md). Sono ora inclusi
account con vault cifrato, backup e ripristino dell'identità, profilo client incorporato,
manifest firmati Ed25519, pipeline Authenticode e lockfile con hash per Windows e Linux.
Il relay JSON descritto sopra rimane disponibile per sviluppo; per il servizio pubblico
usare PostgreSQL e la configurazione attivo/standby documentata nella nuova guida.
Lo stato delle connessioni resta in memoria: un guasto interrompe le sessioni e richiede
riconnessione. Non è un sistema attivo/attivo né un failover senza interruzioni.

I documenti del prodotto usano dati dimostrativi su richiesta del committente. Il rilascio
pubblico richiede titolare e contatti reali, approvazione legale, un certificato Authenticode,
un audit indipendente e prove su due PC con l'infrastruttura effettiva. La pipeline blocca
la modalità pubblica in assenza delle attestazioni e con i documenti dimostrativi.

Dominio, server, certificati TLS, PostgreSQL, bilanciatore, fencing, backup e monitoraggio
devono essere predisposti nell'ambiente di destinazione. I test sul codice non attestano
la sicurezza operativa di quell'ambiente. DPAPI protegge i segreti a riposo nell'account
Windows, ma non da malware o amministratori che controllano la sessione in esecuzione.
Le funzionalità protette da Windows richiedono i privilegi appropriati e, per desktop
UAC e Ctrl+Alt+Canc, un servizio Windows dedicato non incluso in questa versione.
