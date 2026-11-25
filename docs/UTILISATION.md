# Utilisation — guide du technicien

## Trois réflexes, à chaque poste

1. **Scanner chaque tube.** Code-barres complet, puis Entrée. L'analyse est retrouvée toute seule.
2. **Lire les alertes.** Rouge, violet ou orange : on s'arrête et on lit.
3. **Finir à zéro.** On utilise « Traiter » et « Acquitter » jusqu'à ce que les compteurs reviennent à 0.

## L'écran

- **À scanner** (menu de gauche) : le champ du haut est toujours prêt à recevoir un scan.
- **Non réceptionnés** : les tubes attendus. Le menu affiche « x en retard ».
- **Commentaires à acquitter** : le compteur orange en haut à droite.
- **Traiter** : un bouton rouge sur une ligne signale une action à faire.

## Scanner un tube

1. Cliquer dans « Scanner l'échantillon ».
2. Scanner le code-barres **complet**, puis Entrée.
3. Lire le message sous le champ :
   - **vert** : tube suivant ;
   - **rouge ou violet** : on s'arrête et on lit (voir les cas ci-dessous).

## Les cas à problème

### Double scan (bandeau rouge « DOUBLE SCAN »)

Ce code-barres a déjà été scanné. Le bandeau affiche l'heure de chaque scan.

1. Heures proches : c'est sûrement le même tube, scanné deux fois.
2. Heures éloignées : retrouver le tube scanné avant.
3. Sur la ligne, cliquer **Traiter**, choisir l'explication, puis cocher **Confirmer**.

### Tube reçu, mais demande annulée (bandeau violet « ATTENTION — DEMANDE ANNULÉE »)

1. Mettre le tube **de côté**.
2. Vérifier la demande sur la prescription.
3. Cliquer **Traiter**, puis choisir l'un des deux cas :
   - **vraiment annulée** : on retire le tube ;
   - **annulée par erreur** : on garde le tube pour l'analyse, et la demande est à rétablir dans le SIL.
4. Cocher **Confirmer**.

### Tube en erreur (bandeau « RETIRER CE TUBE DU LOT »)

Le bandeau s'affiche pour un tube inconnu, sans analyse attendue, ou d'un laboratoire non reconnu.

1. **Retirer le tube** du lot.
2. Vérifier la demande, ou prévenir un biologiste.
3. Si c'est utile, écrire un commentaire.
4. Cliquer **Traiter**, puis **Confirmer**.

Si la demande arrive plus tard, le tube se reclasse tout seul : pas besoin de le rescanner.

## Le bouton « Traiter », en 3 gestes

1. Cliquer sur le bouton rouge **Traiter**.
2. Choisir la réponse qui correspond à ce que vous avez vu.
3. Cocher **Confirmer**. La décision et votre nom restent sur la ligne.

Les compteurs « Échantillons en erreur » et « Échantillons annulés » comptent ce qu'il **reste à traiter**. Objectif : 0.

## Les retards (à regarder à chaque poste)

- **Non réceptionnés** : le tube n'est pas arrivé à temps.
  - Chercher le tube (paillasse, secteurs).
  - S'il reste introuvable, relancer le laboratoire par mail.
- **Réceptionnés sans résultat** : le tube est reçu, mais le résultat n'est pas sorti à temps. Vérifier où en est l'analyse.

Les dimanches, les jours fériés et les jours de fermeture ne comptent pas dans les retards.

## Relancer un laboratoire par mail

1. Dans **Non réceptionnés**, cliquer « ENVOYER MAILS AUX LABORATOIRES ».
2. Cocher les lignes à relancer, ou cliquer « Sélectionner les retards de réception ».
3. Cliquer « Préparer les mails ». Un mail est préparé par laboratoire, avec l'adresse déjà remplie.
4. Cliquer « Générer le brouillon ». Votre messagerie s'ouvre : **relisez le mail, puis envoyez-le vous-même**.
5. Les tubes relancés portent une enveloppe et l'heure de la relance.

## Commentaires

- **« + Commenter »** dans la colonne de droite. Tout le monde voit le commentaire, avec votre nom et l'heure.
- **Message important ?** Cochez « À transmettre » : le commentaire passe dans le compteur orange « Commentaires à acquitter ».
- **Vous vous êtes trompé ?** Votre dernier commentaire peut être modifié, supprimé ou rendu « à acquitter ».

### Acquitter

1. Cliquer sur le compteur orange en haut à droite.
2. Lire le message.
3. Cliquer **Acquitter**, puis cocher **Confirmer**. Le compteur baisse. Objectif : 0.

## La pastille devant l'analyse

- **Grise** : pas encore faite.
- **Verte** : résultat remonté. Passez la souris dessus pour voir la date et l'heure.
- **Vide** : pas de suivi pour cette analyse.

## Retrouver un tube

- **Rechercher** : nom, n° d'échantillon, dossier, ou un mot d'un commentaire.
- **Période** : les listes montrent par défaut les 2 dernières semaines. « Afficher tous les dossiers » remonte plus loin.
- **Clic sur un patient** : affiche tous ses tubes.
- **« Suivi »** sur une ligne : l'historique complet (qui a fait quoi, et quand).

## Routine

| Moment | À faire |
|---|---|
| **Début de poste** | Non réceptionnés : y a-t-il des retards ? Lire les commentaires à acquitter. |
| **Pendant** | Scanner chaque tube. Bandeau rouge ou violet : s'arrêter, lire, Traiter. |
| **Fin de poste** | En erreur et annulés : 0 à traiter. Commentaires à acquitter : 0. Retards : relancés. |

---

*PCRLia — © 2026 I.Arvish. Tous droits réservés. Édition pcrlia-partage-2026.11-1085660743c0. Voir [LICENSE.txt](../LICENSE.txt).*
