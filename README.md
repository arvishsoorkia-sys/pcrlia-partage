# PCRLia — réception des tubes PCR

*Une interface I.Arvish.*

PCRLia est une application web locale pour la paillasse de biologie moléculaire. Elle sert à :

- **réceptionner** chaque tube PCR en scannant son code-barres ;
- **signaler** les cas à problème : double scan, demande annulée, tube inconnu ou sans analyse ;
- **suivre les retards** : tubes attendus mais pas arrivés, tubes reçus mais sans résultat ;
- **relancer** les laboratoires préleveurs par mail, en quelques clics ;
- **communiquer** entre techniciens avec des commentaires à acquitter.

Cette copie est **vierge** : aucun laboratoire, aucune analyse, aucune adresse, aucune connexion et aucune donnée patient. Tout se paramètre dans l'application (Paramètres PCRLia) et dans un seul fichier d'en-tête.

## Essayer en 2 minutes (données fictives)

Prérequis : Windows, et [Python 3.12 ou plus récent](https://www.python.org/downloads/) (cocher « Add python.exe to PATH »).

```powershell
python -m pip install -r requirements.txt
```

Ensuite, double-cliquer sur **`Apercu-PCRLia.cmd`**. Un aperçu s'ouvre dans le navigateur, avec des données entièrement fictives effacées à la fermeture :

| Compte | Mot de passe | Rôle |
|---|---|---|
| `demo-admin` | `demo` | administrateur (paramètres, comptes) |
| `demo-pcr` | `demo` | technicien |

## Démarrer pour de vrai

1. Double-cliquer sur **`Lancer.cmd`**. L'application démarre sur ce PC (`http://127.0.0.1:8765`) et ouvre le navigateur.
2. **Première ouverture** : la clé demandée se trouve dans `premiere-ouverture.txt`, dans le dossier de données (`%LOCALAPPDATA%\PCRLia`). Créer le compte administrateur.
3. Mettre l'en-tête du laboratoire dans **`web/laboratoire.js`** (nom, sigle, signature des mails, logo).
4. Dans **Paramètres PCRLia**, renseigner les analyses, les laboratoires, les délais et la réception MCA : voir [docs/ADAPTER.md](docs/ADAPTER.md).
5. Créer les comptes des techniciens : bouton **Utilisateurs et affectations**.

## Documentation

| Document | Pour qui | Contenu |
|---|---|---|
| [docs/UTILISATION.md](docs/UTILISATION.md) | techniciens | le travail au quotidien, les cas à problème, les boutons Traiter et Acquitter |
| [docs/ADAPTER.md](docs/ADAPTER.md) | administrateur | tout ce qui se paramètre, et ce qui demande de modifier le code |
| [docs/HEBERGEMENT.md](docs/HEBERGEMENT.md) | informatique | **ce qui reste à faire** : PC hôte, accès des autres postes en HTTPS, liaison MCA, sauvegarde, démarrage automatique |
| [CLAUDE.md](CLAUDE.md) | Claude Code | consignes pour faire évoluer le code avec Claude Code |

## Tests

Double-cliquer sur **`Tester.cmd`**, ou :

```powershell
python -m unittest discover -s tests -t .
```

Les tests utilisent une configuration d'exemple fictive (variable `PCRLIA_DEMO=1`, posée automatiquement par `tests/__init__.py`).

## Organisation du code

```
app/            serveur Python (bibliothèque standard + cryptography)
  server.py       serveur HTTP/HTTPS, routes /api/…
  pcr.py          réception, demandes, retards, commentaires, traitements
  pcr_numbering.py  code-barres -> réseau de laboratoires, site, mail de relance
  pcr_mca.py      réception des demandes envoyées par le middleware MCA (ASTM)
  phadia_orders.py  lecture des messages de demandes au format Phadia
  storage.py      base SQLite, comptes, sessions, journal d'audit chaîné
  nas_storage.py  sauvegarde planifiée sur un partage réseau
  news.py         fenêtre « Nouveautés » après une mise à jour
web/            interface (HTML, CSS, JavaScript sans dépendance)
  laboratoire.js  EN-TÊTE DU LABORATOIRE (à modifier)
  pcr.js          écrans PCRLia
scripts/        aperçu fictif, données de démonstration, certificat HTTPS
tests/          tests automatiques
```

Les données (base SQLite, journaux) ne sont **jamais** dans ce dossier : elles sont dans `%LOCALAPPDATA%\PCRLia`, ou dans le dossier passé avec `--data-dir`. L'application refuse un dossier de données synchronisé (OneDrive, Dropbox, Google Drive).

---

*PCRLia — © 2026 I.Arvish. Tous droits réservés. Édition pcrlia-partage-2026.11-1085660743c0. Voir [LICENSE.txt](LICENSE.txt).*
