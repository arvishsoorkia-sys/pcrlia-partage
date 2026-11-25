/* PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt */
/* Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0 */
'use strict';
// EN-TÊTE DU LABORATOIRE — seul fichier à modifier pour l'affichage.
// name    : nom complet, affiché à la connexion et dans le menu.
// short   : sigle, affiché dans la barre du haut et l'objet des mails de relance.
// service : signature des mails de relance (ex. « Mon labo - biologie moléculaire »).
// logo    : chemin d'une image placée dans web/ (ex. '/logo-laboratoire.png'), ou '' pour afficher le nom.
//           Toute nouvelle image doit aussi être ajoutée à la liste STATIC_FILES de app/server.py.
const LAB = {
  name: 'Mon laboratoire',
  short: 'LABO',
  service: 'Mon laboratoire - biologie moléculaire',
  logo: '',
};
