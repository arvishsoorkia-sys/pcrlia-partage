# Héberger et connecter PCRLia — ce qui reste à faire

Cette copie **fonctionne telle quelle sur un seul PC**. Pour la mettre en service au laboratoire, il reste les étapes ci-dessous. Chacune est à faire **une seule fois**.

| # | Étape | Indispensable ? |
|---|---|---|
| 1 | Choisir et préparer le PC hôte | oui |
| 2 | Démarrage automatique | oui |
| 3 | Accès depuis les autres postes (HTTPS) | oui, si plusieurs postes |
| 4 | Liaison avec le middleware MCA | non (on peut importer des CSV) |
| 5 | Sauvegarde sur le NAS | fortement conseillé |
| 6 | Mises à jour | plus tard |

---

## 1. Le PC hôte

- Un PC Windows **allumé en permanence**, sur le réseau du laboratoire, avec une **adresse IP fixe** (à demander à l'informatique).
- Installer Python 3.12 ou plus récent ([python.org](https://www.python.org/downloads/)). Cocher « Add python.exe to PATH ».
- Copier ce dossier sur le PC, par exemple dans `C:\PCRLia`. Éviter un dossier OneDrive.
- Installer la dépendance :
  ```powershell
  cd C:\PCRLia
  python -m pip install -r requirements.txt
  ```
- Premier lancement : `Lancer.cmd`. Ensuite, création du compte administrateur avec la clé de `%LOCALAPPDATA%\PCRLia\premiere-ouverture.txt`.

**Les données** (base `chlas.sqlite3` et journal d'audit) sont dans `%LOCALAPPDATA%\PCRLia` du compte Windows qui lance l'application. Pour les mettre ailleurs : `python -m app.server --data-dir D:\PCRLia-donnees`. Le dossier ne doit pas être synchronisé (OneDrive, etc.).

> Données de santé : le PC hôte doit être protégé comme tout poste du laboratoire (session Windows, antivirus, mises à jour). Les mots de passe sont stockés hachés, les sessions expirent après 30 minutes d'inactivité, et chaque action est tracée dans un journal chaîné.

## 2. Démarrage automatique

Dans le **Planificateur de tâches** Windows, créer une tâche :
- **Déclencheur** : « Au démarrage », ou « À l'ouverture de session » du compte dédié.
- **Action** : programme `python`, arguments `-m app.server --no-browser` (et les options HTTPS de l'étape 3), dossier de départ `C:\PCRLia`.
- Cocher « Exécuter même si l'utilisateur n'est pas connecté ».

Toujours utiliser le même compte Windows, puisque les données sont dans son `%LOCALAPPDATA%`.

## 3. Accès depuis les autres postes (HTTPS obligatoire)

Sans option, l'application n'écoute que sur le PC lui-même (`http://127.0.0.1:8765`). Pour les autres postes, elle **exige HTTPS** sur l'adresse IP privée du PC hôte.

1. Générer un certificat pour l'adresse du PC hôte (exemple : `192.168.1.50`) :
   ```powershell
   python scripts\prepare_vpn_tls.py --address 192.168.1.50 --folder C:\PCRLia-certificat
   ```
   Cela crée `server-cert.pem` et `server-key.pem`, et affiche l'empreinte SHA-256 du certificat.
2. Lancer l'application avec ce certificat :
   ```powershell
   python -m app.server --no-browser --vpn-address 192.168.1.50 --https-port 8443 --cert-file C:\PCRLia-certificat\server-cert.pem --key-file C:\PCRLia-certificat\server-key.pem
   ```
3. **Pare-feu Windows** du PC hôte : autoriser l'entrée TCP sur le port **8443**, limitée au réseau du laboratoire.
4. Sur chaque poste, ouvrir `https://192.168.1.50:8443`.
   - Le certificat est auto-signé : l'informatique peut l'installer comme certificat de confiance sur les postes (fichier `server-cert.pem`, magasin « Autorités de certification racines de confiance »).
   - Sinon, accepter l'avertissement du navigateur après avoir vérifié l'empreinte SHA-256.
   - Un certificat émis par l'autorité interne de l'établissement convient aussi.
5. Le premier compte administrateur se crée **uniquement en local** sur le PC hôte (`http://127.0.0.1:8765`), jamais à distance.

Remarques :
- Le nom de l'option `--vpn-address` est historique : il s'agit simplement de l'adresse IP privée du PC sur laquelle écouter. Seules les adresses privées sont acceptées (10.x, 172.16-31.x, 192.168.x).
- Le certificat ne vaut que pour cette adresse : si l'adresse IP change, il faut en générer un nouveau.

## 4. Liaison avec le middleware MCA (facultative)

PCRLia peut **recevoir automatiquement les demandes PCR** envoyées par le middleware MCA. Ce sont des messages ASTM E1381 au format Phadia « Full download » (H/P/O/L). PCRLia ne renvoie **jamais** de résultat à MCA.

**Principe :**
- PCRLia **écoute** sur l'adresse du PC hôte, port **5511**.
- **MCA se connecte** à cette adresse et envoie les demandes.
- Seule l'adresse IP de MCA déclarée dans PCRLia est acceptée.

**À faire :**
1. Dans PCRLia, ouvrir Paramètres PCRLia → **Réception MCA** et renseigner :
   - l'**adresse d'écoute** : l'IP du PC hôte ;
   - l'**adresse MCA** : l'IP du serveur MCA ;
   - le **port** (5511 par défaut) ;
   - l'**encodage** (cp1252 en général).

   Activer la liaison seulement quand MCA est prêt.
2. **Pare-feu** du PC hôte : autoriser l'entrée TCP sur le port 5511, **uniquement depuis l'IP du serveur MCA**.
3. **Côté MCA** (avec le fournisseur ou l'administrateur MCA) : créer une connexion sortante TCP/ASTM vers `IP-du-PC-hôte:5511`, qui envoie les demandes des analyses PCR dans ce format.
4. Faire un **essai** avec quelques demandes fictives, et vérifier qu'elles apparaissent dans « Non réceptionnés ». L'écran Réception MCA affiche l'état de la liaison et les messages refusés.
5. Facultatif, pour la **pastille verte** : faire créer dans MCA des codes « analyse faite » (règles d'expertise), puis renseigner ces codes dans Codes et analyses.

Si le middleware n'est pas MCA, ou si le format des messages est différent, il faut adapter `app/phadia_orders.py` à des exemples de messages réels. Dans ce cas, utiliser en attendant l'**import CSV** des demandes (voir [ADAPTER.md](ADAPTER.md)).

## 5. Sauvegarde sur le NAS

Paramètres PCRLia → **Sauvegarde** :
- indiquer un dossier réseau précis (ex. `\\NAS\Sauvegardes\PCRLia`) ;
- choisir la fréquence ;
- cliquer **Vérifier l'accès**, puis **Sauvegarder maintenant**.

Le compte Windows qui exécute PCRLia doit avoir le droit d'écrire dans ce dossier. Chaque sauvegarde est une copie complète et vérifiée de la base.

## 6. Mises à jour

Pour mettre à jour l'application :
1. Arrêter l'application (tâche planifiée, ou fermer la fenêtre).
2. Remplacer les dossiers `app/` et `web/` par la nouvelle version. **Ne pas toucher** au dossier de données.
3. Relancer l'application, puis faire Ctrl+F5 sur les postes.

Le bouton « Mises à jour » (sous Déconnexion) sert à installer depuis le navigateur des paquets signés. Il suppose un installateur qui **n'est pas fourni** dans cette copie : sans lui, il indique simplement qu'aucune mise à jour n'est disponible.

---

## Récapitulatif

- [ ] PC hôte avec une IP fixe, Python installé, `pip install -r requirements.txt`
- [ ] Premier lancement et compte administrateur
- [ ] `web/laboratoire.js` : nom, sigle, logo
- [ ] Paramètres : analyses, délais, jours fermés, laboratoires et mails de relance
- [ ] Comptes des techniciens
- [ ] Certificat HTTPS, pare-feu (port 8443), test depuis un poste
- [ ] Tâche planifiée au démarrage
- [ ] Sauvegarde NAS vérifiée
- [ ] (facultatif) Liaison MCA : pare-feu (port 5511), configuration côté MCA, essai

---

*PCRLia — © 2026 I.Arvish. Tous droits réservés. Édition pcrlia-partage-2026.11-1085660743c0. Voir [LICENSE.txt](../LICENSE.txt).*
