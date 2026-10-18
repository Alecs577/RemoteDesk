# Documenti del prodotto

Il prodotto è destinato alla distribuzione proprietaria. I documenti terms.md e privacy.md
usano dati dimostrativi su richiesta del committente e non sono approvati per il pubblico.
La pipeline impedisce una release pubblica finché contengono il marcatore DIMOSTRATIVI.

Gli avvisi delle dipendenze si generano dalle wheel effettivamente installate con
scripts/collect-notices.py; vengono inclusi in entrambi gli eseguibili e accanto alla release.
Le licenze dei runtime nativi distribuiti (OpenSSL, librerie Pillow, Tcl/Tk e Python)
devono essere conservate: il collector include i file forniti dalle distribuzioni installate.
Il file sbom.json è un inventario di dipendenze, non un attestato di assenza di vulnerabilità.

Non modificare i titolari o le licenze delle componenti di terzi. La distribuzione proprietaria
non modifica i diritti previsti dalle rispettive licenze. La firma Authenticode e la firma
Ed25519 degli aggiornamenti sono due credenziali distinte e vanno custodite separatamente.
