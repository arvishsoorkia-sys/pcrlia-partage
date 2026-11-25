# Adapter PCRLia à son laboratoire

La plupart des réglages se font **dans l'application**, avec un compte administrateur : bouton **Paramètres PCRLia**, en bas du menu de gauche. Seuls l'en-tête et les cas particuliers demandent de modifier le code.

## 1. L'en-tête (fichier `web/laboratoire.js`)

```js
const LAB = {
  name: 'Mon laboratoire',            // connexion et menu
  short: 'LABO',                      // barre du haut, objet des mails de relance
  service: 'Mon laboratoire - biologie moléculaire',   // signature des mails
  logo: '',                           // ex. '/logo-laboratoire.png'
};
```

Pour ajouter un logo :
1. Placer l'image dans `web/`.
2. Indiquer son chemin dans `logo`.
3. Ajouter ce même chemin à la liste `STATIC_FILES` de `app/server.py`.
4. Redémarrer, puis faire Ctrl+F5 dans le navigateur.

## 2. Les réglages dans l'application (Paramètres PCRLia)

### Codes et analyses

C'est le catalogue des analyses. Il est **vide** à l'installation. Pour chaque analyse :

- **Code** : le code de demande, tel qu'il arrive du middleware ou du fichier CSV (ex. `CTNG1`).
- **Type de PCR** : le classement dans les listes (PCR IST, PCR COVID, etc.).
- **Terminaison** : les 2 derniers chiffres du code-barres du tube pour cette analyse (ex. `88`).
- **Délai de réception / délai de résultat** : en heures. Au-delà, la ligne passe en retard (« ⧗ NON REÇU » ou « SANS RÉSULTAT »).
- **Code « analyse faite »** (facultatif) : le code envoyé par MCA quand l'analyse est faite. C'est lui qui allume la pastille verte.

Dans le même onglet se trouvent aussi :
- les **alertes de retard** (réception et résultat), qu'on peut activer ou couper ;
- les **jours fermés** : jours de la semaine, jours fériés, dates exceptionnelles.

Le bouton **Importer les analyses** charge une liste CSV de demandes attendues (séparateur `;`). Le modèle est téléchargeable depuis l'application. C'est utile si les demandes n'arrivent pas encore par le middleware.

### Codes ignorés

Ce sont les codes reçus du middleware qui ne concernent pas la PCR. Ils ne créent ni tube attendu ni erreur.

### Types de PCR

Pour chaque terminaison de code-barres, on choisit :
- le type de PCR ;
- le transcodage du numéro de dossier (combien de chiffres retirer à la fin du code-barres).

### Laboratoires et dossiers

PCRLia reconnaît le laboratoire préleveur **à partir du code-barres**. Il connaît trois formats, appelés « réseaux » :

| Réseau | Format du dossier | Ce qui identifie le site |
|---|---|---|
| **Réseau A** | 8 chiffres | les 2 premiers chiffres (chaque site apparaît séparément dans les listes) |
| **Réseau B** | 10 chiffres (année sur 1 chiffre, mois, jour, …) | les chiffres 6 et 7 |
| **Réseau C** | 2 chiffres de préfixe, puis N chiffres | le préfixe, qui correspond à 1 ou 2 lettres du dossier |

Pour chaque site, on saisit son code, son nom et son **mail de relance**. Les listes sont **vides** à l'installation.

> **Si vos codes-barres ne suivent aucun de ces formats**, la règle se change dans le code : fonction `classify()` de `app/pcr_numbering.py`, et ses tests `tests/test_pcr_numbering.py`. Les noms internes (`reseau_a`, `reseau_b`, `reseau_c`) et les libellés (`FAMILY_LABELS`, plus les textes de `web/pcr.js`) peuvent être renommés. Demandez-le à Claude Code (voir [CLAUDE.md](../CLAUDE.md)).

### Réception MCA

C'est la liaison qui reçoit automatiquement les demandes envoyées par le middleware MCA. Elle est **désactivée** à l'installation. Voir [HEBERGEMENT.md](HEBERGEMENT.md), section 4.

### Sauvegarde

C'est la copie automatique de la base sur un partage réseau (NAS). Voir [HEBERGEMENT.md](HEBERGEMENT.md), section 5.

## 3. Les comptes

Bouton **Utilisateurs et affectations** :
- créer les techniciens et les administrateurs ;
- désactiver un compte ;
- changer un mot de passe.

Un compte « Consultation » peut lire mais ne peut rien modifier.

## 4. Ce qui reste écrit dans le code (à revoir selon vos pratiques)

| Élément | Où |
|---|---|
| Libellés des réponses de « Traiter » (double scan, annulation, erreur) | `HANDLING_LABELS` dans `app/pcr.py`, et `web/pcr.js` |
| Texte des mails de relance | fonction de préparation des mails dans `web/pcr.js` (chercher `Nous n’avons pas encore reçu`) |
| Types de PCR (IST, COVID, PARASITO, Autres) | `GROUPS` dans `app/pcr.py` |
| Jours fériés (calendrier français) | `HOLIDAYS` dans `app/pcr.py` |
| Format des messages de demandes (Phadia) | `app/phadia_orders.py` |
| Fenêtre « Nouveautés » | `app/news.py` : ajouter une entrée à chaque nouvelle version |
| Lien « 📘 Guide du technicien » | page facultative : déposer `Guide-technicien.html` dans le dossier indiqué par `PCRLIA_GUIDE_DIR` |

---

*PCRLia — © 2026 I.Arvish. Tous droits réservés. Édition pcrlia-partage-2026.11-1085660743c0. Voir [LICENSE.txt](../LICENSE.txt).*
