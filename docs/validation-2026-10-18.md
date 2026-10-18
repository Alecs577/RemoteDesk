# Verifiche eseguite il 18 ottobre 2026

Ambiente: Windows 11 x64, Python 3.13.3, cryptography 50.0.2, Pillow 13.0.0,
PyInstaller 6.22.0. Le dipendenze sono installate in ambienti virtuali del progetto.
Non è stata modificata l'installazione globale di Python.

## Test

La suite generale ha eseguito 52 test con esito positivo. I test PostgreSQL sono eseguiti
separatamente con un database portatile locale, autenticazione SCRAM e listener esclusivamente
su 127.0.0.1. PostgreSQL non è stato installato come servizio Windows; al termine viene arrestato.

I quattro test PostgreSQL verificano:

- account e limiti condivisi su database persistente;
- arresto richiesto quando la connessione al database è persa;
- esclusività della leadership, assegnazione idempotente e conservazione dell'ID;
- due relay TLS reali, promozione dello standby e registrazione dello stesso ID sul nuovo nodo.

I test generali comprendono consenso e rifiuto, permessi, inviti, TLS, password, file,
configurazione, ripristino cifrato su un altro profilo, rifiuto della passphrase errata e di
backup alterati, isolamento degli account, recupero con rotazione del codice e revoca delle
sessioni, certificati HTTPS non attendibili o con hostname errato, rifiuto di manifest
manomessi/scaduti/riutilizzati e rimozione degli aggiornamenti scaricati con hash errato.
Il parser PROXY verifica il mittente attendibile e non consuma i successivi byte TLS.
Un controllo Tkinter costruisce tutte le sottosezioni e le nuove finestre in modalità
nascosta; ha individuato e verificato la correzione di un errore causato da wraplength vuoto.
Sono verificati anche il blocco del profilo tra istanze, la conservazione dei file corrotti
e il passaggio al relay predefinito di una nuova build.

Comandi riproducibili:

```powershell
.venv-validation\Scripts\python.exe -m compileall -q remotedesk scripts tests
.venv-validation\Scripts\python.exe -m unittest discover -s tests -v
.venv-validation\Scripts\python.exe scripts/test-postgres-local.py --postgres-bin build/postgresql/pgsql/bin
```

Il secondo comando salta i test PostgreSQL se REMOTEDESK_TEST_DATABASE_URL non è configurato;
il terzo avvia il database locale e li esegue. Il binario portatile di prova proviene dalla
distribuzione PostgreSQL/EDB ufficiale 17.11; non fa parte della release di RemoteDesk.
La workflow CI contiene anche una verifica PostgreSQL su Linux; non è stata eseguita da
un servizio CI esterno durante questa sessione.

## Build

Eseguito scripts/build-windows.ps1 con deploy/client-profile.example.json. Risultati:
dist/RemoteDesk.exe, dist/RemoteDeskTools.exe, avvisi delle dipendenze, inventario e SHA-256.
RemoteDeskTools.exe diagnostics conferma relay dimostrativo incorporato, API account
dimostrativa e modalità public_release=false. Il comando identity --help funziona dal binario.
Il profilo temporaneo remotedesk/_distribution.py è stato rimosso al termine della build.

Le build sono di prova e non sono firmate Authenticode. Il dominio `.invalid` è intenzionale
e non offre un servizio online. La chiave pubblica degli aggiornamenti deve essere configurata
prima di utilizzare il download nella distribuzione reale.

## Verifiche ancora esterne

Non sono stati eseguiti un audit indipendente, prove su due PC fisici e reti diverse,
test di carico pubblici, verifiche del fencing in presenza di partizioni di rete,
validazione di HAProxy sul server reale, firma con un certificato del titolare o revisione
legale. Queste attività non si considerano completate dai test locali. Il desktop UAC e
Ctrl+Alt+Canc restano fuori dalle funzionalità dichiarate di questa versione.
