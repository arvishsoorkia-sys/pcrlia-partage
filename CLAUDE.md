# Consignes pour Claude Code — PCRLia

PCRLia est une application de réception des tubes PCR pour un laboratoire de biologie médicale (voir README.md). Elle manipule des **données de santé** : la prudence passe avant la rapidité.

## Règles

- **Jamais de vraies données patients** dans le dépôt, les tests, les captures ou l'aperçu. Utiliser uniquement les données fictives (`scripts/pcr_demo_data.py`, `PCRLIA_DEMO=1`).
- **L'application en service est en lecture seule pour Claude** : on ne modifie ni ses données ni ses réglages pour essayer quelque chose. Les essais se font dans l'aperçu (`Apercu-PCRLia.cmd` ou `python scripts/preview_pcr.py --with-followup`).
- **Ne rien envoyer vers le middleware MCA** : PCRLia ne fait que recevoir des demandes.
- **Montrer avant de modifier** : pour un changement visible, proposer d'abord une maquette ou l'aperçu, puis faire valider.
- **Lancer les tests** après chaque modification : `python -m unittest discover -s tests -t .`

## Repères techniques

- Python 3.12+, bibliothèque standard, plus `cryptography`. Pas de framework web : `app/server.py` route les requêtes à la main.
- Front-end en JavaScript sans dépendance : `web/app.js` (noyau), `web/pcr.js` (écrans), `web/laboratoire.js` (en-tête).
- Base SQLite (`app/storage.py`). Toute modification est tracée dans un journal d'audit chaîné (`store.audit`). Les écritures PCR passent par `PCRService` (`app/pcr.py`) et contrôlent un numéro de révision.
- La numérotation des laboratoires (code-barres → réseau, site, mail) est dans `app/pcr_numbering.py`. Une installation réelle démarre vide ; `PCRLIA_DEMO=1` active l'exemple fictif.
- Réception MCA : `app/pcr_mca.py` (transport ASTM, liste blanche d'IP) et `app/phadia_orders.py` (format des messages).
- Fichiers servis : liste `STATIC_FILES` de `app/server.py`.
- Accès réseau : uniquement en HTTPS, sur une IP privée précise (`--vpn-address`, `--cert-file`, `--key-file`).

## Première tâche suggérée

Lire `docs/ADAPTER.md` et `docs/HEBERGEMENT.md`, puis aider l'administrateur à :
1. remplir `web/laboratoire.js` ;
2. vérifier que les codes-barres du laboratoire correspondent à l'un des trois réseaux de `app/pcr_numbering.py`. Sinon, adapter `classify()` et ses tests ;
3. préparer l'hébergement (PC hôte, HTTPS, tâche planifiée) en suivant la liste de `docs/HEBERGEMENT.md`.
