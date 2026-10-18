# Guide utilisateur - SDAI, gestion électronique des documents

Ce guide s'adresse à toutes les personnes qui consultent, déposent, valident, partagent ou signent des
documents dans SDAI. Il ne demande aucune connaissance technique. Les noms en **gras** sont ceux que vous
voyez à l'écran.

Pour l'installation et les réglages, voir le [Guide administrateur](./guide-administrateur.md).

---

## Sommaire

1. [Se connecter](#1-se-connecter)
2. [Ce que vous pouvez faire : rôles et confidentialité](#2-ce-que-vous-pouvez-faire--rôles-et-confidentialité)
3. [Rechercher un document](#3-rechercher-un-document)
4. [Consulter un document](#4-consulter-un-document)
5. [Déposer des documents](#5-déposer-des-documents)
6. [Valider les documents (file de révision)](#6-valider-les-documents-file-de-révision)
7. [Versions, extraction et réintégration](#7-versions-extraction-et-réintégration)
8. [Télécharger, partager, exporter](#8-télécharger-partager-exporter)
9. [Commenter et mentionner](#9-commenter-et-mentionner)
10. [Signer électroniquement](#10-signer-électroniquement)
11. [Notifications](#11-notifications)
12. [Questions fréquentes](#12-questions-fréquentes)

---

## 1. Se connecter

1. Ouvrez votre navigateur (Firefox, Chrome ou Edge récents) à l'adresse communiquée par votre
   administrateur, par exemple `https://ged.example.org` ou `https://serveur-ged:8443`.
2. La première fois, le navigateur peut afficher un avertissement de sécurité (certificat interne à
   l'organisation). Votre administrateur vous dira si vous pouvez l'accepter.
3. Saisissez votre adresse e-mail et votre mot de passe, puis **Se connecter**.

- **Langue** : les boutons **FR** / **EN** en haut de l'écran changent la langue. Votre choix est
  mémorisé sur ce navigateur.
- **Session** : elle dure 8 heures, ensuite il faut se reconnecter.
- **Se déconnecter** : en bas du menu de gauche. Faites-le toujours sur un poste partagé.
- **Première connexion** : le mot de passe reçu est temporaire. SDAI vous demande aussitôt d'en choisir
  un (au moins 8 caractères, différent de votre adresse e-mail) ; rien d'autre n'est accessible avant.
- **Mon profil** : cliquez sur votre nom ou votre photo en bas du menu. Vous y changez votre nom
  complet, votre fonction, votre téléphone et votre photo de profil (JPEG, PNG, WebP ou GIF, 5 Mo au
  plus, recadrée en carré ; vos collègues la voient). L'adresse e-mail sert à vous connecter : seule la
  personne qui gère les comptes peut la changer.
- **Changer mon mot de passe** : depuis **Mon profil**. Vos autres sessions ouvertes
  (autre poste, autre navigateur) sont alors fermées.
- **Mot de passe oublié** : demandez à un administrateur, ou à la personne qui gère les comptes, de le
  réinitialiser ; vous recevrez un nouveau mot de passe temporaire.

Selon l'installation, il peut y avoir deux adresses : le **serveur local** (dans vos locaux) et le
**serveur cloud** (accessible depuis l'extérieur). Ce sont les mêmes documents et le même compte ; le
badge en haut du menu indique sur lequel vous êtes.

---

## 2. Ce que vous pouvez faire : rôles et confidentialité

| Rôle | Peut |
|---|---|
| **lecteur** | consulter et rechercher les documents auxquels il a accès, lire les commentaires, télécharger si son accès le permet |
| **réviseur** | en plus : déposer, valider, corriger, classer, étiqueter, commenter, demander et apposer des signatures |
| **administrateur** | tout, y compris gérer les utilisateurs, les droits et les réglages |

Un administrateur peut aussi donner des permissions particulières : gérer l'accès aux documents,
modifier les données extraites, gérer la conservation (archiviste), créer des liens de partage.

**Accès aux documents.** Un document peut être réservé à certaines personnes ou à certains groupes,
directement ou par sa catégorie. Chaque accès a un niveau :

- **lecture** : voir le document ;
- **téléchargement** : voir et télécharger l'original ;
- **modification** : modifier ses données, ses versions, son classement.

**Confidentialité.** Chaque document a un niveau : **Public**, **Interne**, **Confidentiel** ou
**Secret**. Vous ne voyez que les documents dont le niveau ne dépasse pas votre **habilitation**.

Un document auquel vous n'avez pas accès n'apparaît nulle part : ni dans les listes, ni dans la
recherche, ni dans les comptes. C'est normal.

---

## 3. Rechercher un document

La zone **Rechercher dans tous les documents et pages…** est en haut de chaque page. Elle cherche dans
toutes les données extraites (numéro, objet, signataire…) **et** dans le texte de chaque page.

| Vous tapez | Résultat |
|---|---|
| `titre foncier` | les documents contenant les deux mots |
| `"titre foncier"` | l'expression exacte |
| `bail -résilié` | contient « bail » mais pas « résilié » |
| `bail or location` | l'un ou l'autre mot |

Les accents et les majuscules sont ignorés (`decret` trouve « Décret »), et les formes proches d'un
mot aussi (`contrats` trouve « contrat »).

**Les résultats** montrent un extrait avec les mots trouvés surlignés et les numéros de page
(**Trouvé en page 3**). Cliquez sur un numéro de page (**p. 3**) pour ouvrir le document directement à
cette page.

**Filtres** (page **Documents**) : catégorie (avec **Inclure les sous-catégories**),
étiquette, type de document, réviseur, dates. Les filtres se combinent avec la recherche.

---

## 4. Consulter un document

Cliquez sur un document dans une liste. À gauche, l'image des pages (molette pour zoomer, flèches
**← Préc.** / **Suiv. →** ou les touches ← → pour passer au document suivant de la liste). À droite,
les onglets :

- **Fiche** : les données extraites, le numéro d'enregistrement (à citer dans vos courriers, par exemple
  `ACTE-2026-000123`), la **Catégorie**, les **Étiquettes**, les documents liés, les
  **Restrictions d'accès**, le **Cycle de vie**.
- **Versions** : tous les fichiers successifs du document (voir [section 7](#7-versions-extraction-et-réintégration)).
- **Historique** : chaque modification d'un champ (avant / après, qui, quand) et chaque consultation
  ou téléchargement.
- **Commentaires** et **Signatures** (sections 9 et 10).

**Classer un document** : dans **Fiche**, **Catégorie** → **Modifier**, choisissez la catégorie. Un document
encore non classé est dans **À classer**.

**Étiqueter** : **Ajouter une étiquette…**, tapez le mot puis **Ajouter**. Les étiquettes servent de
filtre dans la liste.

**Lier deux documents** (par exemple un acte de vente et le titre qu'il transfère) : section des
documents liés, choisissez l'autre document et la relation. SDAI propose aussi des liens quand un
document cite le numéro d'un autre.

**Document archivé** : il est en lecture seule. Seuls les archivistes peuvent encore le modifier.

---

## 5. Déposer des documents

Menu **Téléverser des documents** (réviseurs et administrateurs).

1. Glissez-déposez des fichiers **JPG, PNG ou PDF** (50 Mo maximum par fichier), ou cliquez pour
   parcourir. Un PDF de plusieurs pages devient un seul document.
2. Choisissez la **Catégorie** où classer les documents, ou laissez **Classement automatique (selon le
   type de document)**. Pour déposer directement dans une catégorie, ouvrez-la dans la page
   **Documents** et cliquez sur **Déposer ici**.
3. **Comment classer ces documents ?** - **Classer tel quel (sans extraction)** : les documents sont
   disponibles tout de suite, consultables et retrouvables par le texte de leurs pages, et marqués
   **Non extrait**. **Extraire les données (Ollama)** : leurs champs sont lus par le modèle
   d'extraction. Depuis **Déposer ici**, *Classer tel quel* est choisi par défaut ; depuis le menu,
   *Extraire les données*.
4. **Démarrer l'ingestion**. Le lot apparaît avec l'avancement de chaque document, page par page.
5. Avec l'extraction, pour chaque page, SDAI redresse l'image, lit le texte et extrait les données. Comptez de quelques
   secondes à quelques minutes par page selon le serveur. Vous pouvez quitter la page : le travail
   continue, et vous retrouvez vos lots dans **Téléversements récents**.

À la fin, chaque document est :

- **validé automatiquement** si l'extraction est sûre ;
- **à réviser** sinon : il part dans la **File de révision** ;
- **Doublon** s'il a déjà été déposé (même fichier) : il n'est pas créé une deuxième fois ;
- **Hors périmètre** si ce n'est pas un type de document que l'organisation archive ;
- **Échoué** si une page n'a pas pu être lue : **Réessayer** la page, ou **Saisir manuellement** ses
  données.

Une page blanche ou non pertinente est **ignorée** automatiquement.

**Extraire plus tard** : un document **Non extrait** garde son fichier, ses pages, sa catégorie, ses
droits et tout ce qui lui a été ajouté. Pour lire ses données, ouvrez-le et cliquez sur **Extraire les
données** (onglet **Fiche**), ou cochez plusieurs documents dans la page **Documents** (filtre **Non
extraits**) puis **Extraire les données (n)**. À la fin de l'extraction, le document prend son type
(facture, acte...), garde son numéro et son historique, et part dans la file de révision si
nécessaire.

Si vous déposez sur le **serveur cloud**, le document est conservé immédiatement mais son extraction
est faite par le serveur local : il apparaît complet dès que les deux serveurs se sont synchronisés.

---

## 6. Valider les documents (file de révision)

Menu **File de révision**. Vous n'y voyez que les documents que vous avez le droit de modifier.

Pour chaque document, comparez l'image et les champs, corrigez si besoin (permission de modification
des extractions nécessaire), puis :

| Action | Raccourci | Effet |
|---|---|---|
| **Approuver** | Entrée | le document est validé avec vos corrections |
| **Rejeter** | | le document est écarté (il reste consultable, marqué rejeté) |
| **Signaler** | | à saisir à la main plus tard |
| **Ignorer** | Échap | passer au suivant sans rien changer |

Un champ vide signifie « non trouvé » : remplissez-le seulement si l'information est visible sur
l'image. Toutes les corrections sont gardées dans l'**Historique**.

Un document marqué **Conflit de synchronisation** a été modifié en même temps sur les deux serveurs :
un administrateur choisit la bonne valeur dans la page **Synchronisation**.

---

## 7. Versions, extraction et réintégration

**Ajouter une version** (onglet **Versions**, **+ Nouvelle version**) : choisissez le nouveau fichier,
indiquez ce qui a changé, cochez **Ré-extraire les champs depuis ce fichier** si les données doivent
être relues. Les anciennes valeurs restent dans l'historique ; rien n'est jamais effacé.

**Restaurer** une ancienne version : elle redevient le fichier courant sous forme d'une nouvelle
version. L'historique n'est jamais réécrit.

**Extraire / Réintégrer** (travailler sur un document sans être dérangé) :

1. **Extraire** verrouille le document pour vous (8 heures par défaut). Les autres le voient
   **Verrouillé par** votre nom et ne peuvent que le consulter.
2. Travaillez sur le fichier (téléchargé depuis **Versions**).
3. **Réintégrer** libère le document ; vous pouvez joindre le nouveau fichier, qui devient la version
   suivante.

Le verrou se libère tout seul à l'échéance. Un administrateur peut **Forcer le déverrouillage** ; vous
en êtes alors notifié.

---

## 8. Télécharger, partager, exporter

**Télécharger l'original** : le fichier intact de la version courante (menu à côté pour choisir une
autre version). Chaque téléchargement est enregistré. Selon le réglage de l'organisation, les PDF
**Confidentiel** et **Secret** reçoivent un filigrane avec votre nom et la date.

**Partager à l'extérieur** (permission de partage nécessaire) : **Partager** crée un lien qu'une
personne sans compte peut utiliser pour télécharger le document.

1. Choisissez la durée (**Valable**, 30 jours au plus), éventuellement un **Mot de passe**, un nombre
   maximal de téléchargements, la version, et le **Destinataire** (pour mémoire).
2. **Créer le lien**, puis **Copier** : le lien n'est affiché **qu'une seule fois**.
3. Envoyez le lien, et le mot de passe par un autre moyen (téléphone, SMS).

Vous pouvez **Révoquer** un lien à tout moment. Un lien cesse aussi de fonctionner si le document est
supprimé, si vous perdez l'accès au document ou si votre compte est désactivé. Les documents
**Secret** ne peuvent pas être partagés, les **Confidentiel** seulement par un administrateur.

**Exporter plusieurs documents** (page **Documents**) : cochez les documents (ou **Exporter tous les
résultats** d'une recherche), puis **Exporter la sélection**. Le ZIP se prépare en arrière-plan ;
récupérez-le dans **Mes exports** (disponible 24 heures). Il contient les originaux, leurs données et
un manifeste d'empreintes SHA-256 qui prouve que les fichiers n'ont pas été modifiés.

---

## 9. Commenter et mentionner

Onglet **Commentaires** : écrivez puis **Commenter** (ou Ctrl+Entrée). Tapez `@` suivi d'un nom pour
**mentionner** une personne : elle est notifiée (seulement si elle a accès au document).
**Répondre** crée une discussion ; vous pouvez **Modifier** ou **Supprimer** vos commentaires.

Les lecteurs peuvent lire les commentaires mais pas en publier. Les commentaires restent possibles sur
un document archivé ou verrouillé.

---

## 10. Signer électroniquement

**Demander une signature** (onglet **Signatures**) : ajoutez les signataires **dans l'ordre**, le motif
(par exemple « Approbation ») et un message. Chaque signataire est sollicité quand le précédent a
signé.

**Signer** : quand c'est votre tour, vous recevez une notification. Ouvrez le document, onglet
**Signatures**, **Signer**, saisissez **votre mot de passe** (c'est ce qui prouve que c'est bien vous),
un motif si besoin, puis validez. La version courante est signée avec le certificat de
l'organisation, avec un cachet visible, et enregistrée comme **nouvelle version**. Une image est
d'abord convertie en PDF.

Vous pouvez aussi **Refuser** (avec une note) ; le demandeur peut **Annuler la demande**.

**Vérification** : l'onglet montre, pour chaque signature, si elle est valide et si le document n'a pas
été modifié depuis, avec les preuves conservées (qui, quand, depuis quelle adresse).

Il s'agit d'une signature électronique **simple ou avancée**, pas d'une signature « qualifiée ». Pour
un acte qui l'exige légalement, renseignez-vous auprès de votre administrateur.

---

## 11. Notifications

La **cloche** en haut du menu affiche le nombre de notifications non lues : document à valider,
mention, réponse, signature demandée ou terminée, document partagé avec vous, verrou levé…

Page **Notifications** : la liste complète, **Tout marquer comme lu**, et vos préférences e-mail :
**À chaque notification**, **Une fois par jour (résumé)** ou **Jamais**, et la langue des e-mails. Les
e-mails ne contiennent jamais le contenu des documents, seulement un lien.

---

## 12. Questions fréquentes

**Je ne trouve pas un document que mon collègue voit.** Vous n'avez pas l'accès, ou votre habilitation
est inférieure à sa confidentialité. Demandez à un administrateur ou à la personne qui gère les accès.

**« Verrouillé par … »** : quelqu'un a extrait le document. Attendez sa réintégration, ou demandez-lui
(son nom est affiché).

**« Document en lecture seule »** : il est archivé ou sa conservation est échue. Seul un archiviste
peut le modifier.

**« Fichier en cours de synchronisation depuis l'autre serveur »** : le document a été déposé sur
l'autre serveur et son fichier n'est pas encore arrivé. Réessayez dans quelques minutes.

**J'ai supprimé un document par erreur.** Il est dans la **Corbeille** pendant 30 jours (par défaut) :
un archiviste ou un administrateur peut le restaurer.

**Le serveur est lent ou inaccessible.** Prévenez votre administrateur. Si l'internet est coupé, le
serveur local continue de fonctionner dans vos locaux.
