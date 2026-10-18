# RemoteDesk

RemoteDesk è un'applicazione desktop per assistenza remota e un relay TLS per mettere in contatto i dispositivi dietro NAT.

## Presentazione e percorso di rilascio

- [Presentazione per la direzione](docs/presentazione-remotedesk.md): scopo, casi d'uso, funzionalità e stato effettivo del prodotto.
- [Prossimi passi dettagliati](docs/prossimi-passi.md): attività, responsabilità, dipendenze e criteri di completamento.
- [Descrizione tecnica delle funzionalità](docs/applicazione-e-funzionalita.md).

La versione attuale è una base di sviluppo con verifiche locali documentate. Il servizio pubblico non è ancora operativo e le build dimostrative non sono firmate Authenticode.

## Avvio in sviluppo

Richiede Python 3.11 o successivo e Tkinter. Installa le dipendenze e avvia il client:

```powershell
python -m pip install -r requirements.txt
python -m remotedesk
```

Il relay si avvia separatamente e richiede un certificato TLS valido per l'hostname che verrà configurato nei client:

```sh
python -m remotedesk relay --host 0.0.0.0 --port 8770 \
  --certfile /etc/remotedesk/tls/fullchain.pem \
  --keyfile /etc/remotedesk/tls/privkey.pem \
  --owners-file /var/lib/remotedesk/owners.json
```

Per installazione del relay come servizio, build Windows, backup e limiti di sicurezza, consulta [la guida di distribuzione](docs/security-deployment.md).

Per una descrizione delle schermate, dei flussi di connessione, dei permessi e delle funzionalità disponibili, consulta [la documentazione dell'applicazione](docs/applicazione-e-funzionalita.md).

## Build Windows

Con Python 3.13 x64 e Tkinter installati (avviabile tramite `py -3.13` o `python.exe`), esegui:

```powershell
.\scripts\build-windows.ps1
```

La build produce `dist\RemoteDesk.exe` e `dist\RemoteDeskTools.exe` per il ripristino dell'identità. Account, backup cifrati, verifica degli aggiornamenti e relay PostgreSQL attivo/standby sono disponibili come descritto nella guida alla distribuzione. La firma pubblica richiede un certificato Authenticode reale e l'opzione `-PublicRelease`. Il visualizzatore HTTP locale rimane un percorso di sviluppo; il client usa la connessione cifrata tramite relay.
# Preparazione alla distribuzione

La configurazione del client pubblico, gli account, il recupero identità, gli aggiornamenti
firmati e il relay PostgreSQL attivo/standby sono descritti in
[docs/production-readiness.md](docs/production-readiness.md).
Il profilo in deploy/client-profile.example.json usa dati dimostrativi e deve essere
sostituito per un servizio reale. I documenti legali in legal/ sono bozze dimostrative.
