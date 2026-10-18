# Guide administrateur - SDAI, gestion électronique des documents

Ce guide s'adresse aux administrateurs de SDAI : la personne qui installe et maintient les serveurs, et
celles qui gèrent les comptes, les droits, les catégories et la conservation. Les parties
**Installation**, **Sauvegardes**, **Restauration** et **Mises à jour** demandent un accès en ligne de
commande au serveur ; les autres se font dans l'application.

Voir aussi : [Guide utilisateur](./guide-utilisateur.md) et
[Fiche d'exploitation](./fiche-exploitation.md) (contrôles réguliers, incidents).

Dans les commandes, `docker compose ...` se lance **depuis le dossier d'installation** (par exemple
`/opt/sdai`). Le fichier `.env` de ce dossier contient tous les réglages ; après une modification,
appliquez-la avec `docker compose up -d`.

---

## Sommaire

1. [Architecture en bref](#1-architecture-en-bref)
2. [Installation](#2-installation)
3. [Utilisateurs](#3-utilisateurs)
4. [Droits d'accès](#4-droits-daccès)
5. [Catégories](#5-catégories)
6. [Conservation](#6-conservation)
7. [Élimination](#7-élimination)
8. [Synchronisation local / cloud](#8-synchronisation-local--cloud)
9. [Sauvegardes](#9-sauvegardes)
10. [Restauration](#10-restauration)
11. [Certificats](#11-certificats)
12. [E-mails](#12-e-mails)
13. [Intégrité et journal d'audit](#13-intégrité-et-journal-daudit)
14. [Mises à jour](#14-mises-à-jour)
15. [Réglages (.env)](#15-réglages-env)

---

## 1. Architecture en bref

| Serveur | Rôle |
|---|---|
| **Serveur local** (dans vos locaux) | serveur principal : il reçoit les dépôts, fait l'extraction (modèle d'IA, de préférence avec une carte graphique) et fonctionne sans internet |
| **Serveur cloud** (facultatif) | copie accessible depuis l'extérieur (télétravail, partenaires, liens de partage). Pas d'extraction : les documents déposés là sont traités par le serveur local |

Les deux serveurs se synchronisent automatiquement dès que la connexion le permet. Une installation
sans cloud (rôle **single**) est aussi possible.

Chaque serveur fait tourner les mêmes services Docker :

| Service | Fonction |
|---|---|
| `frontend` | site web (nginx), HTTPS |
| `api` | application |
| `db` | base de données PostgreSQL |
| `redis` | cache |
| `ollama` | modèle d'extraction (serveur local seulement) |
| `sync` | synchronisation avec l'autre serveur |
| `backup` | sauvegarde chiffrée chaque nuit |

Les données sont dans des volumes Docker (base, fichiers déposés, certificats) et dans le dossier
`documents/` de l'installation. **Les secrets sont dans `.env`** : protégez ce fichier (mode 600) et
gardez-en une copie hors des serveurs.

---

## 2. Installation

### 2.1 Ce qu'il faut

| | Serveur local | Serveur cloud |
|---|---|---|
| Système | Ubuntu Server 24.04 LTS (64 bits) | Ubuntu 24.04 (VM chez un hébergeur) |
| Processeur / mémoire | 8 cœurs, 32 Go (16 Go minimum) | 2 à 4 cœurs, 8 Go |
| Carte graphique | NVIDIA 12 Go+ recommandée (sans elle, l'extraction est lente : jusqu'à 15-30 minutes par page dense) - ou un Mac Apple Silicon, ou une machine du réseau équipée (section 2.6) | aucune |
| Disque | 100 Go + la taille des archives à numériser, de préférence en RAID 1 | 50 Go + archives |
| Logiciels | Docker Engine et Docker Compose v2 (paquets `docker.io` et `docker-compose-v2`) | idem |
| Électricité | onduleur (voir la fiche d'exploitation) | - |
| Heure | NTP actif sur les deux serveurs (`timedatectl`) : la synchronisation refuse un écart de plus de quelques minutes | idem |

Le serveur cloud a besoin d'un **nom de domaine** (par exemple `ged.example.org`) pointant vers lui et
des ports **80** et **443** ouverts.

### 2.2 Préparer le paquet d'installation (sur un poste avec internet)

Le serveur local n'a pas besoin d'internet : on prépare un paquet sur un autre poste.

```bash
./setup.sh                                # serveurs Intel / AMD (linux/amd64)
TARGET_PLATFORM=linux/arm64 ./setup.sh    # serveurs ARM
OLLAMA_MODE=docker-amd ./setup.sh         # selon l'emplacement du modèle (section 2.6)
```

Le script télécharge le modèle d'extraction (environ 5 Go), construit les images et écrit dans
`models/` : `ollama-models.tar.gz`, `docker-images.tar.gz` et `SHA256SUMS` (empreintes de
contrôle). Comptez 10 Go et de 15 à 60 minutes selon la connexion.

Copiez ensuite **tout le dossier** (sauf `.git` et `node_modules`) sur le serveur, par exemple sur un
disque USB ou avec :

```bash
rsync -av --exclude .git --exclude node_modules . admin@serveur:/opt/sdai/
```

### 2.3 Installer le serveur local

```bash
cd /opt/sdai
./install.sh
```

`install.sh` vérifie que le paquet n'a pas été abîmé pendant la copie (`SHA256SUMS`), charge les
images, puis lance `scripts/first-run.sh`, qui pose les questions :

| Question | Réponse habituelle |
|---|---|
| Rôle | `local` s'il y aura un serveur cloud, sinon `single` |
| Nom du nœud | `site-main` (unique, ne change plus ensuite) |
| Langue | `fr` |
| Emplacement du modèle d'extraction | proposé selon la machine : `docker-nvidia` (carte NVIDIA), `host` (Mac), `docker` sinon - voir section 2.6 |
| Fuseau horaire | par exemple `Africa/Ndjamena`, `Europe/Paris`, `UTC` |
| Certificat HTTPS | `self_signed` (réseau interne) |
| Nom ou adresse IP du serveur | ce que les utilisateurs taperont, par exemple `serveur-ged` ou `192.168.1.10` |
| Adresse du serveur cloud | `https://ged.example.org` (rôle `local` seulement) |
| Sauvegarde hors site | `sftp:backup@backup.example.net:/sdai-local`, ou vide pour plus tard |
| Nom de l'organisation | le nom affiché dans l'application et sur les procès-verbaux |
| E-mail et mot de passe du premier administrateur | votre compte |

Le script génère tous les secrets (mots de passe de la base, de Redis, des sauvegardes, clé des
sessions, jeton de synchronisation), démarre les services, crée l'organisation et l'administrateur, et
teste la connexion au serveur cloud (un échec est normal tant que le cloud n'est pas installé).

À la fin, il affiche l'adresse du site, par exemple `https://serveur-ged:8443`.

> **À conserver hors des serveurs** (gestionnaire de mots de passe, coffre) : le fichier `.env`, et au
> minimum `RESTIC_PASSWORD` (sans lui, les sauvegardes sont illisibles), `AUTH_SECRET_KEY` et
> `SYNC_NODE_TOKEN` (demandés pour installer le serveur cloud).

Le script peut être relancé sans risque : il garde les secrets existants et ne fait que mettre à jour
les réponses changées.

Le premier démarrage du service `ollama` décompresse le modèle (quelques minutes) ; les dépôts sont
traités dès qu'il est prêt.

### 2.4 Installer le serveur cloud

**Installez d'abord le serveur local.** Sur la VM cloud (Docker installé, dossier copié comme
ci-dessus) :

```bash
./install.sh
```

Répondez `cloud` au rôle, un nom de nœud différent (`cloud-01`), le **nom de domaine**, l'e-mail pour
le certificat, puis collez `AUTH_SECRET_KEY` et `SYNC_NODE_TOKEN` du serveur local (dans son `.env`).
Ne créez **pas** d'administrateur sur le cloud : les comptes arrivent du serveur local à la première
synchronisation (mêmes comptes, mêmes mots de passe).

Obtenez ensuite le certificat (le DNS doit déjà pointer vers la VM) :

```bash
docker compose exec frontend certbot --nginx -d ged.example.org \
  --email admin@example.org --agree-tos --non-interactive
docker compose restart frontend
```

Puis, sur le **serveur local**, testez la liaison :

```bash
docker compose exec sync python -m sync.check
```

La première synchronisation envoie toute la base, puis les fichiers : suivez-la dans
**Synchronisation** (section 8). Sur une grosse archive, l'envoi des fichiers peut durer des heures ou
des jours ; il reprend tout seul après une coupure.

### 2.5 Installation sans questions

Pour une installation scriptée, donnez les réponses par variables d'environnement :

```bash
SDAI_ROLE=local SDAI_NODE_ID=site-main SDAI_LANGUAGE=fr SDAI_TZ=UTC \
SDAI_HTTPS_MODE=self_signed SDAI_DOMAIN=serveur-ged SDAI_OLLAMA_MODE=docker-nvidia \
SDAI_CLOUD_URL=https://ged.example.org SDAI_ORG_NAME="Mon organisation" \
SDAI_ADMIN_EMAIL=admin@example.org SDAI_ADMIN_PASSWORD='...' \
./install.sh --non-interactive
```

(cloud : `SDAI_ROLE=cloud SDAI_DOMAIN=... SDAI_LE_EMAIL=... SDAI_AUTH_SECRET=... SDAI_SYNC_TOKEN=...`)

(modèle sur un autre poste : `SDAI_OLLAMA_MODE=remote SDAI_OLLAMA_HOST=http://192.168.1.20:11434`)

### 2.6 Modèle d'extraction (Ollama) : où il tourne

L'extraction utilise un modèle d'IA (`qwen2.5vl:7b`, servi par Ollama). Il est beaucoup plus rapide sur
une carte graphique. L'emplacement est un réglage de l'installation, `OLLAMA_MODE` dans `.env`, choisi
par `scripts/first-run.sh` ; pour en changer, relancez simplement le script (`./scripts/first-run.sh`).
**Ne modifiez jamais `docker-compose.yml`** pour cela.

| Machine | Réglage | Effet |
|---|---|---|
| Serveur Linux avec carte NVIDIA | `docker-nvidia` | Ollama dans Docker, sur la carte graphique (aussi rapide qu'une installation directe) |
| Serveur Linux avec carte AMD (ROCm) | `docker-amd` | Ollama dans Docker (image ROCm), sur la carte graphique |
| Mac (Apple Silicon) | `host` | Ollama installé directement sur le Mac : Docker ne peut pas utiliser la puce graphique du Mac, l'installation directe est 10 à 20 fois plus rapide |
| Serveur sans carte graphique, avec un poste équipé sur le réseau | `remote` | Les pages sont envoyées au poste équipé (adresse `http://<ip>:11434`) |
| Autre cas, tests | `docker` | Ollama dans Docker, sur le processeur : fonctionne partout, lentement |

Ordre de grandeur mesuré : une page scannée en 50 secondes environ sur un Mac en mode `host`, contre 15 à
30 minutes sur un processeur seul.

**Prérequis `docker-nvidia`** : pilote NVIDIA et *NVIDIA Container Toolkit* installés (`nvidia-smi`
fonctionne) ; le script prévient s'il manque. **`docker-amd`** : pilote AMD avec ROCm et une carte
reconnue par Ollama.

**Modes `host` et `remote`** (Ollama hors de Docker) :

1. installez Ollama sur la machine concernée (ollama.com ; sur Mac `brew install ollama`) et
   démarrez-le : `ollama serve` (pour `remote`, il doit écouter le réseau :
   `OLLAMA_HOST=0.0.0.0:11434 ollama serve`, ou le service installé avec les mêmes variables) ;
2. chargez le modèle : `ollama pull qwen2.5vl:7b` avec internet, ou depuis le paquet d'installation :
   `tar xzf models/ollama-models.tar.gz -C ~/.ollama` (Mac), ou sous Linux
   `sudo tar xzf models/ollama-models.tar.gz -C /usr/share/ollama/.ollama && sudo chown -R ollama: /usr/share/ollama/.ollama` ;
3. relancez `./scripts/first-run.sh` et choisissez `host` ou `remote` (donnez alors l'adresse du poste,
   avec une adresse IP fixe) : le script vérifie que le modèle répond.

La machine Ollama doit être allumée pendant les dépôts ; sinon les pages échouent et se relancent avec
**Réessayer**. En mode `remote`, Ollama n'a pas de mot de passe : ne l'ouvrez jamais sur internet
(pas de redirection du port 11434 sur la box) et, si possible, n'autorisez que le serveur SDAI.

Vérification : `ollama ps` (ou `docker compose exec ollama ollama ps`) affiche `100% GPU` pendant
le traitement d'une page.

---

## 3. Utilisateurs

Menu **Opérations → Utilisateurs**. Accessible aux administrateurs et aux personnes ayant la
permission **Peut gérer les utilisateurs**.

### 3.1 Rôles, habilitation, permissions

| Rôle | Peut |
|---|---|
| **lecteur** | consulter, rechercher, télécharger selon ses accès, lire les commentaires |
| **réviseur** | en plus : déposer, valider, corriger, classer, commenter, signer |
| **administrateur** | tout, y compris les réglages de l'organisation |

**Habilitation** : niveau de confidentialité maximal que la personne peut voir (Public, Interne,
Confidentiel, Secret).

**Permissions déléguées** (réviseurs) :

| Permission | Donne accès à |
|---|---|
| Peut gérer l'accès aux documents | restreindre un document à des personnes ou groupes |
| Peut modifier les données extraites | corriger les champs d'un document |
| Peut gérer la conservation et le cycle de vie | archiver, corbeille, gel juridique, proposer des éliminations (archiviste) |
| Peut créer des liens de partage | partager à l'extérieur |
| Peut gérer les utilisateurs | cette page |

### 3.2 Créer un compte

**Nouvel utilisateur** : e-mail, nom complet, rôle, habilitation, permissions, puis **Générer** un
mot de passe temporaire (ou en saisir un). **Créer le compte** : le mot de passe temporaire est
affiché **une seule fois** ; transmettez-le par un autre canal (téléphone, en main propre). La personne
devra choisir son propre mot de passe à sa première connexion.

### 3.3 Gérer un compte

**Gérer** sur la ligne d'un compte :

- **Enregistrer** les changements de rôle, habilitation, permissions ;
- **Réinitialiser le mot de passe** : nouveau mot de passe temporaire, les sessions ouvertes sont
  fermées ;
- **Désactiver** : la personne ne peut plus se connecter, ses sessions prennent fin, ses documents
  extraits sont libérés, ses liens de partage cessent de fonctionner. **Réactiver** annule ;
- **Supprimer** (administrateurs seulement) : le compte disparaît de toutes les listes et son adresse
  e-mail peut être réutilisée. Ce qu'il a fait reste dans l'historique, affiché « Utilisateur supprimé
  du système » ; les documents signés et les procès-verbaux d'élimination gardent son nom.

Règles de sécurité appliquées automatiquement :

- une personne qui n'est pas administratrice ne peut ni créer ni modifier un administrateur, ni
  donner une permission ou une habilitation qu'elle n'a pas ;
- personne ne peut modifier ses propres droits, se désactiver ou se supprimer ;
- le dernier administrateur actif ne peut pas être désactivé ni supprimé ;
- chaque action est enregistrée dans le **Journal d'audit**.

### 3.4 En ligne de commande (secours)

Si plus aucun administrateur ne peut se connecter :

```bash
docker compose exec api python create_admin.py --email admin@example.org --tenant default --role admin
```

Le mot de passe est demandé (il n'apparaît pas à l'écran). Pour bloquer un compte :
`python create_admin.py --email ... --deactivate`.

---

## 4. Droits d'accès

Menu **Opérations → Accès & Groupes**.

**Qui voit un document par défaut :**

- tant qu'il est dans la **File de révision**, toute l'organisation (pour que n'importe quel réviseur
  puisse le valider), sauf si un accès par catégorie ou par type le concerne déjà ;
- **dès qu'il est validé ou rejeté** (ou validé automatiquement), seulement **les administrateurs et
  la personne qui l'a déposé** (accès « Modification » automatique). Pour l'ouvrir à d'autres, donnez
  un accès par catégorie, par type ou sur le document ;
- les documents déposés avant cette règle (sans déposant connu) restent visibles par toute
  l'organisation tant qu'aucun accès n'est défini.

Dans tous les cas, l'**habilitation** de chacun s'applique en plus. On donne les accès de trois
façons, qui se combinent :

| Restriction | Où | Effet |
|---|---|---|
| Par document | fiche du document, **Restrictions d'accès → + Restreindre à…** | ce document seulement |
| Par catégorie | **Accès par catégorie** | tous les documents de la catégorie **et de ses sous-catégories** |
| Par type de document | **Accès par type de document** | tous les documents de ce type |

Chaque accès donne un niveau : **Lecture**, **Téléchargement** ou **Modification**. Dès qu'un accès
(quel qu'il soit) concerne un document, seules les personnes ou groupes ayant l'un de ces accès le
voient. Les administrateurs voient tout. Un lecteur ne dépasse jamais le niveau Téléchargement.

**Groupes** : créez des groupes (par exemple Direction, Service foncier), ajoutez-y des personnes,
puis donnez les accès aux groupes plutôt qu'aux personnes : un départ ou une arrivée se gère alors en
une opération.

**Conseil** : préparez les catégories et les accès par catégorie **avant** les premiers dépôts, et
définissez le classement automatique (section 5.2) : chaque nouveau document arrive alors dans sa
catégorie, visible des bonnes personnes.

Retirer le dernier accès d'une catégorie ou d'un type supprime cette restriction : ses documents ne
restent visibles que par leurs accès propres (déposant, accès par document).

**Confidentialité** : sur la fiche d'un document, choisissez son niveau. Une personne dont
l'habilitation est inférieure ne le voit nulle part.

---

## 5. Catégories

### 5.1 Arbre des catégories

Les catégories forment un arbre (catégories et sous-catégories), chacune avec un code (par
exemple `1.2`) et un nom. Gérez-le dans la page **Documents** (panneau **Catégories** : **+ Catégorie**, **Ajouter une sous-catégorie**,
**Renommer**, **Déplacer**, **Supprimer**) ou dans **Accès & Groupes → Catégories** - réservé aux
administrateurs. Seule une catégorie vide peut être supprimée.

Les accès donnés sur une catégorie s'appliquent à toutes ses sous-catégories ; il en va de même
pour les règles de conservation et les gels juridiques.

### 5.2 Classement automatique

**Accès & Groupes → Classement automatique** : pour chaque type de document, la catégorie où un nouveau
document est classé. Sans règle, il va dans **À classer** ; un réviseur le classe ensuite depuis sa
fiche.

### 5.3 Étiquettes

**Accès & Groupes → Étiquettes** : renommer, changer la couleur, **Fusionner avec…** (deux étiquettes
qui veulent dire la même chose), supprimer. Toute personne pouvant modifier un document peut lui
ajouter une étiquette.

---

## 6. Conservation

Menu **Cycle de vie**, réservé aux administrateurs et aux archivistes (permission **Peut gérer la
conservation et le cycle de vie**).

### 6.1 Règles de conservation

**Règles de conservation → Nouvelle règle** :

- **S'applique à** une catégorie (et tout ce qui est en dessous) ou un type de document ;
- **Durée (mois)** et **À compter de** : la date du document, de versement, de validation ou
  d'archivage ;
- **Sort final** : **Élimination**, **Réexamen** ou **Conservation définitive**.

La règle la plus précise l'emporte : celle de la catégorie la plus proche, sinon celle du type. La tâche
quotidienne recalcule les dates et marque **À éliminer** les documents archivés arrivés à échéance ;
**Exécuter maintenant** la lance tout de suite. Rien n'est jamais éliminé automatiquement à
l'échéance : il faut une proposition approuvée (section 7).

### 6.2 Archivage

Sur la fiche d'un document, **Cycle de vie → Archiver** : il passe en lecture seule (seuls les
archivistes peuvent encore le modifier) et sa durée de conservation commence si la règle part de la
date d'archivage. **Prolonger** fixe une nouvelle échéance à un document échu.

### 6.3 Gel juridique

**Geler (gel juridique)**, avec un motif (affaire, demande) : le document ne peut plus être mis à la
corbeille, purgé ni éliminé jusqu'à **Lever le gel**. On peut geler un document, une catégorie entière ou
le résultat d'une recherche (page **Documents**).

### 6.4 Corbeille

Un document supprimé va dans la **Corbeille** : il peut être **Restauré** pendant 30 jours
(`TRASH_RETENTION_DAYS`), puis il est éliminé automatiquement, avec un bordereau « Purge de la
corbeille ». Un document gelé n'est jamais purgé.

---

## 7. Élimination

Menu **Cycle de vie → Éliminations**. Une élimination demande **deux personnes** :

1. un archiviste sélectionne des documents **À éliminer**, ajoute un motif et **Propose** ;
2. **un autre administrateur** vérifie la liste et **Approuve et élimine** (ou **Refuse**).
   Celui qui a proposé ne peut pas approuver.

À l'approbation, les fichiers sont supprimés **sur les deux serveurs**. Il reste une trace (cote,
type, dates, empreintes SHA-256) et le **Procès-verbal (PDF)**, à imprimer et signer si votre
réglementation l'exige.

> Les sauvegardes faites **avant** l'élimination contiennent encore les fichiers, jusqu'à leur
> expiration (au plus environ un an : 30 sauvegardes quotidiennes et 12 mensuelles). Le procès-verbal
> le mentionne. Si une restauration remet en place une sauvegarde plus ancienne, la synchronisation
> supprime de nouveau les fichiers éliminés ; sur un serveur seul, refaites l'élimination
> (page **Éliminations** et journal d'audit, action `document_destroyed`).

---

## 8. Synchronisation local / cloud

Menu **Opérations → Synchronisation** (administrateurs). La page montre, pour **ce** serveur :

- **Ce serveur** : nœud, rôle, dernière synchronisation réussie, échecs consécutifs ;
- **Pairs** : dernier contact, **Modifications non envoyées** (doit revenir à 0) ;
- **Fichiers** : à transférer, volume, en attente de la source, **En échec** (**Relancer**) ;
- **Conflits de synchronisation** ;
- **Sauvegardes** et **E-mails envoyés par ce serveur**.

Une alerte s'affiche dans le menu de chaque page quand la synchronisation échoue depuis plus de
6 heures (`SYNC_ALERT_HOURS`), quand une sauvegarde a échoué ou date de plus de 36 heures, ou quand des
fichiers ou des e-mails sont en échec. Les administrateurs en sont aussi notifiés.

**Coupures** : c'est le serveur local qui contacte le cloud. Sans internet, il continue de fonctionner
et réessaie de plus en plus espacé (jusqu'à 15 minutes) ; tout est rattrapé au retour de la connexion.
`SYNC_MAX_KBPS` limite le débit utilisé (par exemple `256` sur une liaison partagée).

**Conflits** : le même champ modifié sur les deux serveurs pendant une coupure. La modification la plus
récente est déjà appliquée partout ; l'autre valeur est gardée ici. Choisissez **Garder la valeur de
ce serveur**, **Garder l'autre valeur** ou **Saisir la valeur…**. Le document est marqué dans la file
de révision tant que le conflit n'est pas tranché.

Tester la liaison depuis le serveur local : `docker compose exec sync python -m sync.check`.

| Message | Cause probable |
|---|---|
| `timestamp out of range` | horloges différentes : vérifier NTP (`timedatectl`) sur les deux serveurs |
| `Bad sync signature` | `SYNC_NODE_TOKEN` différent entre les deux `.env` |
| `404` sur `/api/sync/hello` | `SYNC_NODE_TOKEN` vide sur le cloud (relancer `docker compose up -d` après modification) |
| `cannot reach` | pas d'internet, DNS, pare-feu, ou cloud arrêté |
| déconnexion en passant d'un serveur à l'autre | `AUTH_SECRET_KEY` différent |

---

## 9. Sauvegardes

La synchronisation **n'est pas** une sauvegarde : une suppression ou une erreur est synchronisée
aussi. Chaque serveur se sauvegarde chaque nuit à `BACKUP_TIME` (02:30 par défaut) : la base, les
fichiers et `documents/`, **chiffrés** avec `RESTIC_PASSWORD`, sur un serveur SFTP séparé
(`BACKUP_SFTP_TARGET`), et en option sur un NAS (`BACKUP_NAS_PATH`). On garde 30 sauvegardes
quotidiennes et 12 mensuelles.

Mise en place :

1. un compte SFTP sur un serveur de sauvegarde, de préférence chez un autre hébergeur que le cloud ;
2. `BACKUP_SFTP_TARGET=sftp:backup@backup.example.net:/sdai-local` dans `.env` (ou la question de
   l'installation) ;
3. le script d'installation crée une clé SSH : ajoutez la clé publique affichée
   (`backup/ssh/id_ed25519.pub`) au fichier `~/.ssh/authorized_keys` du compte de sauvegarde ;
4. testez :

```bash
docker compose run --rm backup now            # une sauvegarde tout de suite
docker compose run --rm backup restore-test   # exercice de restauration
```

`restore-test` restaure la dernière sauvegarde dans un espace temporaire et vérifie la base, le journal
d'audit et un échantillon de fichiers ; il affiche `PASSED` ou `FAILED` et ne laisse rien derrière lui.
**Faites-le une fois par mois** et après tout changement de la configuration des sauvegardes.

Liste des sauvegardes d'un serveur :

```bash
docker compose run --rm --entrypoint sh backup -c \
  'restic -r "$BACKUP_SFTP_TARGET" snapshots --host "$NODE_ID"'
```

---

## 10. Restauration

La procédure complète, commande par commande, est dans [RESTORE.md](../../RESTORE.md). En résumé :

| Situation | Que faire |
|---|---|
| Un document supprimé par erreur | **Corbeille → Restaurer** (30 jours) |
| Une valeur modifiée par erreur | onglet **Historique** du document : l'ancienne valeur y est |
| Ancienne version d'un fichier | onglet **Versions → Restaurer** |
| **Serveur local perdu**, cloud à jour | réinstaller le serveur local avec le même `.env`, `python -m sync.join` avant le premier démarrage : il récupère tout depuis le cloud (RESTORE.md, « Rebuild the local server from the cloud ») |
| **Serveur cloud perdu** | nouvelle VM, même `.env` cloud, `sync.join`, DNS : le serveur local renvoie tout |
| Les deux perdus, ou serveur seul | réinstaller, puis restaurer la dernière sauvegarde restic (RESTORE.md, « Restore a node from restic on a clean VM ») |

Après une restauration, la synchronisation rattrape d'elle-même ce qui a changé depuis la sauvegarde
sur l'autre serveur. Suivez la page **Synchronisation** jusqu'à zéro modification en attente.

**Sans `.env` ni `RESTIC_PASSWORD`, aucune restauration n'est possible** : gardez-en une copie hors
des serveurs.

---

## 11. Certificats

### 11.1 Certificat du site (HTTPS)

- **Serveur local (`self_signed`)** : un certificat propre à cette installation est créé au premier
  démarrage, au nom donné pendant l'installation (`SELF_SIGNED_CN`). Les navigateurs affichent un
  avertissement la première fois : expliquez aux utilisateurs comment l'accepter, ou distribuez le
  certificat aux postes (`docker compose exec frontend cat /etc/nginx/ssl/cert.pem`). Valable 10 ans.
  Pour en créer un nouveau (changement de nom du serveur) :
  `docker compose exec frontend rm /etc/nginx/ssl/cert.pem /etc/nginx/ssl/key.pem`, modifiez
  `SELF_SIGNED_CN` dans `.env`, puis `docker compose up -d --force-recreate frontend`.
- **Serveur cloud (`letsencrypt`)** : certificat gratuit et reconnu, obtenu avec la commande `certbot`
  de la section 2.4 ; il se renouvelle automatiquement. Le port 80 doit rester ouvert.

### 11.2 Certificat de signature électronique

La signature des PDF utilise le certificat de l'organisation (fichier PKCS#12), placé dans le dossier
`secrets/` de l'installation, sur **chaque serveur** où l'on signe.

Certificat de test ou de démarrage (auto-signé, valable 3 ans) :

1. dans `.env`, choisissez le mot de passe du fichier (8 caractères au moins) :

   ```bash
   SIGNING_P12_PATH=/secrets/sdai-signing.p12
   SIGNING_P12_PASSWORD=<mot de passe du fichier>
   SIGNING_LOCATION=<ville de l'organisation>
   ```

2. créez le certificat dans `secrets/` (le service `api` voit ce dossier en lecture seule, d'où le
   montage `/out` pour l'écrire) :

   ```bash
   docker compose run --rm --no-deps -v "$PWD/secrets:/out" api \
     python scripts/generate_signing_cert.py --org "Mon organisation" --out /out/sdai-signing.p12
   ```

3. `docker compose up -d`. L'onglet **Signatures** d'un document propose alors **Demander une
   signature**.

Sur un déploiement avec cloud, copiez le même fichier dans `secrets/` du serveur cloud, avec les mêmes
lignes `SIGNING_*` dans son `.env`. SDAI vérifie ses propres signatures, mais un lecteur PDF hors de
l'organisation affiche une signature auto-signée comme « validité inconnue ». Pour les documents
envoyés à l'extérieur, achetez un certificat de signature de documents auprès d'un prestataire de
certification reconnu, copiez-le dans `secrets/` et ajoutez sa chaîne de certification à
`SIGNING_TRUST_ROOTS`.

**Expiration** : l'onglet **Signatures** avertit quand le certificat a expiré. Renouvelez-le avant :
les signatures déjà faites restent valides.

SDAI produit des signatures électroniques **simples ou avancées**, jamais **qualifiées**.

---

## 12. E-mails

Sans réglage, SDAI ne fait que des notifications dans l'application. Pour les e-mails, renseignez dans
`.env` : `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_SECURITY` (`starttls`, `ssl` ou `none`), `SMTP_USER`,
`SMTP_PASSWORD`, `SMTP_FROM`, et `PUBLIC_BASE_URL` (adresse utilisée dans les liens, celle du serveur
cloud s'il existe). Chaque serveur envoie les e-mails qu'il a créés ; s'il est hors ligne, ils partent
plus tard. Les e-mails en échec sont listés dans **Synchronisation → E-mails envoyés par ce serveur**
(**Renvoyer**).

---

## 13. Intégrité et journal d'audit

- **Journal d'audit** (menu **Journal d'audit**) : connexions, validations, modifications, accès,
  partages, signatures, éliminations, gestion des comptes. Filtres et export CSV.
- **Vérification d'intégrité** (**Accès & Groupes → Vérification de fixité / intégrité**) : vérifie que
  chaque fichier existe encore et correspond à son empreinte SHA-256. Elle tourne seule chaque semaine ;
  **Lancer la vérification d'intégrité** la lance tout de suite.
- **Vérifier la chaîne d'audit** : chaque entrée du journal est liée à la précédente par une empreinte.
  Un message « Le journal d'audit a été altéré » signale une modification directe de la base : prévenez
  le responsable de la sécurité et conservez les sauvegardes.

---

## 14. Mises à jour

1. Sur un poste avec internet, avec la nouvelle version : `./setup.sh` (nouveau paquet `models/`).
2. **Faites une sauvegarde** sur chaque serveur : `docker compose run --rm backup now`.
3. Copiez le nouveau dossier par-dessus l'installation **sans toucher** à `.env`, `documents/`,
   `secrets/` ni `backup/ssh/`, par exemple :
   `rsync -av --exclude .git --exclude node_modules --exclude .env --exclude documents --exclude secrets --exclude backup/ssh ./ admin@serveur:/opt/sdai/`
4. Lancez `./install.sh` : il charge les nouvelles images, garde `.env` et les données, et redémarre.
   La base est mise à niveau automatiquement au démarrage.
5. Avec un serveur cloud : **mettez à jour le cloud d'abord**, puis le serveur local. La
   synchronisation continue entre les deux.

Vérifiez ensuite la connexion, un dépôt de test et la page **Synchronisation**.

---

## 15. Réglages (.env)

Les principaux réglages ; tous sont décrits dans `.env.example`. Après modification :
`docker compose up -d`.

| Réglage | Défaut | Rôle |
|---|---|---|
| `DEFAULT_LANGUAGE` | `fr` (installation) | langue de l'interface tant que la personne n'en a pas choisi, des pages de partage et des e-mails |
| `TZ` | `UTC` | fuseau horaire (heure des sauvegardes) |
| `AUTH_TOKEN_EXPIRE_HOURS` | 8 | durée d'une session |
| `HTTP_PORT` / `HTTPS_PORT` | 8080 / 8443 (local), 80 / 443 (cloud) | ports du site |
| `PUBLIC_BASE_URL` | | adresse des liens de partage et des e-mails |
| `DOWNLOAD_WATERMARK_CONFIDENTIAL` | false | filigrane sur les PDF confidentiels et secrets téléchargés |
| `EXPORT_MAX_DOCUMENTS` / `EXPORT_MAX_MB` / `EXPORT_LINK_HOURS` | 500 / 2048 / 24 | limites des exports |
| `TRASH_RETENTION_DAYS` | 30 | durée dans la corbeille |
| `CHECKOUT_DEFAULT_HOURS` / `CHECKOUT_MAX_HOURS` | 8 / 72 | durée d'une extraction (verrou) |
| `SYNC_INTERVAL_SECONDS` | 30 | intervalle de synchronisation |
| `SYNC_MAX_KBPS` | 0 (illimité) | débit maximal de la synchronisation |
| `SYNC_ALERT_HOURS` | 6 | alerte après ce délai sans synchronisation |
| `BACKUP_SFTP_TARGET` / `BACKUP_NAS_PATH` / `BACKUP_TIME` | / / 02:30 | sauvegardes |
| `SMTP_*` | | e-mails (section 12) |
| `SIGNING_*` | | signature électronique (section 11.2) |
| `OCR_LANGS` | `fra+eng` | langues de lecture du texte des pages pour la recherche |

**Ne changez jamais** après l'installation : `NODE_ID`, `POSTGRES_PASSWORD`, `RESTIC_PASSWORD`, et sur
un déploiement avec cloud `AUTH_SECRET_KEY` et `SYNC_NODE_TOKEN` (sauf procédure de changement
coordonnée sur les deux serveurs).
