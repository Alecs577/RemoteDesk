# RemoteDesk — informativa sui dati

BOZZA CON DATI DIMOSTRATIVI — NON UTILIZZARE PER UNA DISTRIBUZIONE PUBBLICA.

Titolare dimostrativo: RemoteDesk Demo S.r.l., Via Esempio 1, 00100 Roma, Italia.
Contatto privacy dimostrativo: privacy@example.invalid. Questi dati non identificano un soggetto reale.

Sul PC RemoteDesk conserva ID, preferenze, rubrica, chiavi, impronte attendibili e,
se impostata, l'impronta derivata della password di accesso. I segreti Windows sono protetti
con DPAPI. Registrazioni, screenshot e file ricevuti rimangono nelle cartelle scelte dall'utente.

Il relay tratta ID, chiavi pubbliche, hash dei segreti di registrazione, indirizzi IP e
informazioni tecniche necessarie a instradare le connessioni. Schermo, input, chat, appunti,
file e contenuto degli inviti sono cifrati tra i dispositivi. La cifratura non nasconde
al servizio gli indirizzi IP, gli orari e il volume delle connessioni.

Il servizio account conserva username, hash della password, hash del codice di recupero,
hash delle sessioni con scadenza, nomi dei dispositivi e backup cifrati. La passphrase dei
backup non è inviata al servizio. La password account e il codice di recupero account
non permettono di decifrare il backup senza la relativa passphrase. I token di sessione
scadono dopo un'ora e vengono rimossi dal database durante gli accessi successivi.

I contatori di abuso hanno finestre di dieci minuti o un'ora. Gli username non sono indirizzi
email verificati e non viene fornito un recupero tramite email. I log applicativi dell'API
non devono contenere credenziali, token o contenuto dei backup.

La cancellazione dell'account elimina credenziali, sessioni e backup dal database attivo.
Non elimina automaticamente l'identità del dispositivo dalla directory del relay o le
copie presenti nei backup operativi. Le procedure di cancellazione della directory e
scadenza dei backup devono essere definite dal titolare prima dell'apertura pubblica.

Prima della pubblicazione occorre definire basi giuridiche, finalità contrattuali e di
sicurezza, tempi di conservazione, provider e localizzazione, responsabili del trattamento,
eventuali trasferimenti extra SEE, modalità di esercizio dei diritti, gestione delle
violazioni e autorità di controllo competente. La configurazione server deve applicare
quanto dichiarato. Questa bozza non sostituisce una revisione legale.
