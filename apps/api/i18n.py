"""
Backend response localization - English/French.

The frontend sends the user's chosen UI language on every request as the
standard `Accept-Language` header (see packages/api-client's fetch
wrapper, which reads it from the same localStorage key the language
switcher writes to). `resolve_locale` turns that into "en" or "fr",
defaulting to DEFAULT_LOCALE (env DEFAULT_LOCALE, "en" unless set) for
anything else (missing header, an unsupported language). `t()` looks up a message by a stable key and
formats it for that locale - this is the single source of truth for
every user-facing string an API error/response returns; call sites
should never inline an English literal in `detail=`.
"""

import os

from fastapi import Header

SUPPORTED_LOCALES = ("en", "fr")
# Language of the deployment (DEFAULT_LANGUAGE in .env): answers without a
# usable Accept-Language (public share pages, API keys, e-mails to people
# who never signed in). English unless set.
DEFAULT_LOCALE = (os.getenv("DEFAULT_LOCALE") or "en").strip().lower()
if DEFAULT_LOCALE not in SUPPORTED_LOCALES:
    DEFAULT_LOCALE = "en"


def resolve_locale(accept_language: str | None = Header(default=None)) -> str:
    """FastAPI dependency - reads the Accept-Language header and returns
    a supported locale code, defaulting to DEFAULT_LOCALE."""
    if accept_language:
        lang = accept_language.split(",")[0].split("-")[0].strip().lower()
        if lang in SUPPORTED_LOCALES:
            return lang
    return DEFAULT_LOCALE


MESSAGES: dict[str, dict[str, str]] = {
    # ── auth.py ──────────────────────────────────────────────────────────
    "auth.not_authenticated": {
        "en": "Not authenticated",
        "fr": "Non authentifié",
    },
    "auth.invalid_token": {
        "en": "Invalid token",
        "fr": "Jeton invalide",
    },
    "auth.invalid_or_expired_token": {
        "en": "Invalid or expired token",
        "fr": "Jeton invalide ou expiré",
    },
    "auth.token_revoked": {
        "en": "Token revoked",
        "fr": "Jeton révoqué",
    },
    "auth.user_not_found_or_deactivated": {
        "en": "User not found or deactivated",
        "fr": "Utilisateur introuvable ou désactivé",
    },
    "auth.admin_required": {
        "en": "Admin access required",
        "fr": "Accès administrateur requis",
    },
    "auth.access_manager_required": {
        "en": "Access-management permission required",
        "fr": "Permission de gestion des accès requise",
    },
    "auth.extraction_editor_required": {
        "en": "Extraction-editing permission required",
        "fr": "Permission de modification des extractions requise",
    },
    "auth.user_required": {
        "en": "This action requires a user account, not an API key",
        "fr": "Cette action nécessite un compte utilisateur, pas une clé API",
    },
    "auth.contributor_required": {
        "en": "Read-only accounts cannot upload or follow ingestion",
        "fr": "Les comptes en lecture seule ne peuvent pas téléverser ni suivre l'ingestion",
    },
    "auth.reviewer_required": {
        "en": "Read-only accounts cannot perform this action",
        "fr": "Les comptes en lecture seule ne peuvent pas effectuer cette action",
    },
    "auth.incorrect_credentials": {
        "en": "Incorrect email or password",
        "fr": "E-mail ou mot de passe incorrect",
    },
    "auth.invalid_api_key": {
        "en": "Invalid, revoked, or expired API key",
        "fr": "Clé API invalide, révoquée ou expirée",
    },
    # ── shared across routers ───────────────────────────────────────────
    "common.table_not_found": {
        "en": "Table '{table}' not found",
        "fr": "Table « {table} » introuvable",
    },
    "auth.password_change_required": {
        "en": 'You must choose a new password before continuing',
        "fr": 'Vous devez choisir un nouveau mot de passe avant de continuer',
    },
    "auth.user_manager_required": {
        "en": 'Managing user accounts requires the user management permission',
        "fr": 'La gestion des comptes nécessite la permission de gestion des utilisateurs',
    },
    "users.wrong_current_password": {
        "en": 'Your current password is incorrect',
        "fr": 'Votre mot de passe actuel est incorrect',
    },
    "users.password_unchanged": {
        "en": 'The new password must be different from the current one',
        "fr": "Le nouveau mot de passe doit être différent de l'actuel",
    },
    "users.password_too_short": {
        "en": 'The password must have at least 8 characters',
        "fr": 'Le mot de passe doit comporter au moins 8 caractères',
    },
    "users.password_like_email": {
        "en": 'The password must not be the e-mail address',
        "fr": "Le mot de passe ne doit pas être l'adresse e-mail",
    },
    "users.invalid_email": {
        "en": 'Invalid e-mail address',
        "fr": 'Adresse e-mail invalide',
    },
    "users.email_taken": {
        "en": 'An account already uses this e-mail address',
        "fr": 'Un compte utilise déjà cette adresse e-mail',
    },
    "users.email_taken_inactive": {
        "en": 'A deactivated account uses this e-mail address - reactivate it instead',
        "fr": 'Un compte désactivé utilise cette adresse e-mail - réactivez-le plutôt',
    },
    "users.cannot_grant_admin": {
        "en": 'Only an administrator can give the administrator role',
        "fr": "Seul un administrateur peut attribuer le rôle d'administrateur",
    },
    "users.cannot_grant_permission": {
        "en": 'You can only give permissions you hold yourself',
        "fr": 'Vous ne pouvez attribuer que les permissions que vous détenez',
    },
    "users.clearance_above_yours": {
        "en": 'This is above your own clearance',
        "fr": 'Cela dépasse votre propre habilitation',
    },
    "users.cannot_manage_admin": {
        "en": 'Only an administrator can manage an administrator account',
        "fr": 'Seul un administrateur peut gérer un compte administrateur',
    },
    "users.last_admin": {
        "en": 'This is the last active administrator - give the role to someone else first',
        "fr": "C'est le dernier administrateur actif - attribuez d'abord ce rôle à quelqu'un d'autre",
    },
    "users.not_your_own_rights": {
        "en": 'You cannot change your own role, clearance or permissions',
        "fr": 'Vous ne pouvez pas modifier votre propre rôle, habilitation ou permissions',
    },
    "users.not_yourself": {
        "en": 'You cannot do this to your own account',
        "fr": 'Vous ne pouvez pas faire cela sur votre propre compte',
    },
    "users.use_change_password": {
        "en": 'Change your own password from your account page',
        "fr": 'Changez votre propre mot de passe depuis votre page de compte',
    },
    "common.not_found": {
        "en": "Not found",
        "fr": "Introuvable",
    },
    "common.document_not_found": {
        "en": "Document not found",
        "fr": "Document introuvable",
    },
    "documents.export_field_required": {
        "en": "CSV export needs a list field: add ?field=<name>",
        "fr": "L'export CSV nécessite un champ de type liste : ajoutez ?field=<nom>",
    },
    "documents.export_field_not_list": {
        "en": "Field '{field}' is not a list and cannot be exported as CSV",
        "fr": "Le champ « {field} » n'est pas une liste et ne peut pas être exporté en CSV",
    },
    "common.group_not_found": {
        "en": "Group not found",
        "fr": "Groupe introuvable",
    },
    "common.group_already_exists": {
        "en": "Group '{name}' already exists",
        "fr": "Le groupe « {name} » existe déjà",
    },
    "common.user_not_found": {
        "en": "User not found",
        "fr": "Utilisateur introuvable",
    },
    "common.series_not_found": {
        "en": "Series not found",
        "fr": "Série introuvable",
    },
    "common.series_already_exists": {
        "en": "Series '{name}' already exists",
        "fr": "La série « {name} » existe déjà",
    },
    "common.page_not_found": {
        "en": "Page not found",
        "fr": "Page introuvable",
    },
    "common.batch_not_found": {
        "en": "Batch not found",
        "fr": "Lot introuvable",
    },
    "common.link_not_found": {
        "en": "Link not found",
        "fr": "Lien introuvable",
    },
    "common.grant_not_found": {
        "en": "Grant not found",
        "fr": "Autorisation introuvable",
    },
    "common.file_not_found": {
        "en": "File not found",
        "fr": "Fichier introuvable",
    },
    "common.access_denied": {
        "en": "Access denied",
        "fr": "Accès refusé",
    },
    # ── admin_router.py ──────────────────────────────────────────────────
    "admin.no_fields_to_update": {
        "en": "No fields to update",
        "fr": "Aucun champ à mettre à jour",
    },
    # ── documents_router.py ─────────────────────────────────────────────
    "documents.permission_download_required": {
        "en": "You can view this document but not download or export it",
        "fr": "Vous pouvez consulter ce document mais pas le télécharger ni l'exporter",
    },
    "documents.permission_edit_required": {
        "en": "You can view this document but not modify it",
        "fr": "Vous pouvez consulter ce document mais pas le modifier",
    },
    "documents.grant_target_required": {
        "en": "A series grant needs series_id and a document-type grant needs table_name",
        "fr": "Une autorisation sur une série exige series_id, une autorisation sur un type exige table_name",
    },
    "documents.confidentiality_above_clearance": {
        "en": "You cannot classify a document above your own clearance",
        "fr": "Vous ne pouvez pas classer un document au-dessus de votre propre habilitation",
    },
    "documents.no_editable_fields": {
        "en": "No editable fields in request",
        "fr": "Aucun champ modifiable dans la requête",
    },
    "documents.cannot_link_self": {
        "en": "A document cannot be linked to itself.",
        "fr": "Un document ne peut pas être lié à lui-même.",
    },
    "documents.target_document_not_found": {
        "en": "Target document not found",
        "fr": "Document cible introuvable",
    },
    # ── review_router.py ─────────────────────────────────────────────────
    "review.invalid_action": {
        "en": "action must be 'approve' or 'reject'",
        "fr": "l'action doit être 'approve' ou 'reject'",
    },
    # ── ingest_router.py ─────────────────────────────────────────────────
    "ingest.only_crashed_can_retry": {
        "en": "Only crashed documents (manual_entry status) can be retried.",
        "fr": "Seuls les documents échoués (statut manual_entry) peuvent être relancés.",
    },
    "ingest.no_stored_images": {
        "en": "No stored images to retry from.",
        "fr": "Aucune image stockée à partir de laquelle relancer.",
    },
    "ingest.stored_images_missing": {
        "en": "Stored image(s) no longer on disk: {missing}. Re-upload the original file instead.",
        "fr": "Image(s) stockée(s) introuvable(s) sur le disque : {missing}. Retéléversez plutôt le fichier original.",
    },
    "ingest.source_not_recorded": {
        "en": "The original source file wasn't recorded for this document (it was ingested before this feature existed). Use Retry instead, or re-upload the file.",
        "fr": "Le fichier source original n'a pas été enregistré pour ce document (il a été ingéré avant l'existence de cette fonctionnalité). Utilisez Réessayer, ou retéléversez le fichier.",
    },
    "ingest.source_file_missing": {
        "en": "Original source file no longer on disk: {path}",
        "fr": "Fichier source original introuvable sur le disque : {path}",
    },
    "ingest.stored_page_image_missing": {
        "en": "Stored page image no longer on disk: {path}",
        "fr": "Image de page stockée introuvable sur le disque : {path}",
    },
    "ingest.drive_not_configured": {
        "en": "Google Drive not configured. Set GOOGLE_SERVICE_ACCOUNT_FILE env var.",
        "fr": "Google Drive non configuré. Définissez la variable d'environnement GOOGLE_SERVICE_ACCOUNT_FILE.",
    },
    "ingest.drive_lib_not_installed": {
        "en": "google-api-python-client not installed.",
        "fr": "google-api-python-client n'est pas installé.",
    },
    "ingest.unsupported_file_type": {
        "en": "Unsupported file type '{ext}' for {filename}. Allowed: {allowed}",
        "fr": "Type de fichier non pris en charge « {ext} » pour {filename}. Autorisés : {allowed}",
    },
    "ingest.file_too_large": {
        "en": "{filename} exceeds the maximum upload size ({max_mb} MB).",
        "fr": "{filename} dépasse la taille maximale de téléversement ({max_mb} Mo).",
    },
    "ingest.invalid_file_content": {
        "en": "{filename} does not look like a valid {ext} file (content doesn't match its extension).",
        "fr": "{filename} ne semble pas être un fichier {ext} valide (le contenu ne correspond pas à son extension).",
    },
    "ingest.unknown_preset": {
        "en": "Unknown prompt preset '{preset}'. Call GET /api/ingest/presets for the list of valid presets.",
        "fr": "Préréglage de prompt inconnu « {preset} ». Appelez GET /api/ingest/presets pour la liste des préréglages valides.",
    },
    "ingest.drive_not_configured": {
        "en": "Google Drive import is not configured on this server (GOOGLE_SERVICE_ACCOUNT_FILE)",
        "fr": "L'import Google Drive n'est pas configuré sur ce serveur (GOOGLE_SERVICE_ACCOUNT_FILE)",
    },
    "ingest.provide_path_or_drive": {
        "en": "Provide either 'path' or 'google_drive_folder_id'.",
        "fr": "Fournissez soit 'path', soit 'google_drive_folder_id'.",
    },
    "ingest.path_outside_root": {
        "en": "Path is outside the allowed ingest directory ({root}).",
        "fr": "Le chemin est en dehors du répertoire d'ingestion autorisé ({root}).",
    },
    "ingest.path_not_found": {
        "en": "Path not found: {path}",
        "fr": "Chemin introuvable : {path}",
    },
    "ingest.no_supported_files": {
        "en": "No supported files found.",
        "fr": "Aucun fichier pris en charge trouvé.",
    },
    "ingest.no_pages_found": {
        "en": "No pages found for this document",
        "fr": "Aucune page trouvée pour ce document",
    },
    "ingest.no_pending_or_failed_pages": {
        "en": "No pending or failed pages to resume",
        "fr": "Aucune page en attente ou échouée à reprendre",
    },
    "ingest.no_extracted_document": {
        "en": "No extracted document found for this upload - it may not have finished processing yet, or was already deleted.",
        "fr": "Aucun document extrait trouvé pour ce téléversement - il n'a peut-être pas encore fini d'être traité, ou a déjà été supprimé.",
    },
    "ingest.document_not_found": {
        "en": "Document not found",
        "fr": "Document introuvable",
    },
    # ── versions_router.py ───────────────────────────────────────────────
    "versions.not_found": {
        "en": "Version not found",
        "fr": "Version introuvable",
    },
    "versions.already_current": {
        "en": "This version is already the current one",
        "fr": "Cette version est déjà la version courante",
    },
    "versions.document_locked": {
        "en": "The document is checked out by another user",
        "fr": "Le document est extrait par un autre utilisateur",
    },
    "versions.document_archived": {
        "en": "The document is archived - only a lifecycle manager can change it",
        "fr": "Le document est archivé - seul un gestionnaire du cycle de vie peut le modifier",
    },
    "versions.conflict": {
        "en": "Another version was added at the same time - please retry",
        "fr": "Une autre version a été ajoutée au même moment - veuillez réessayer",
    },
    "versions.file_missing": {
        "en": "The file of this version is no longer on disk",
        "fr": "Le fichier de cette version n'est plus sur le disque",
    },
    # ── document_files.py (original download, GED Phase 5) ───────────────
    "download.watermark_level_confidential": {"en": "CONFIDENTIAL", "fr": "CONFIDENTIEL"},
    "download.watermark_level_secret": {"en": "SECRET", "fr": "SECRET"},
    "download.watermark_footer": {
        "en": "Copy downloaded by {who} on {when} UTC - not the archived original",
        "fr": "Copie téléchargée par {who} le {when} UTC - ce n'est pas l'original archivé",
    },
    "download.watermark_failed": {
        "en": "This PDF cannot be watermarked, so it cannot be downloaded - ask an administrator",
        "fr": "Ce PDF ne peut pas être filigrané, il ne peut donc pas être téléchargé - contactez un administrateur",
    },
    # ── share_router.py (external share links, GED Phase 5) ──────────────
    "shares.permission_required": {
        "en": "You are not allowed to create share links",
        "fr": "Vous n'êtes pas autorisé à créer des liens de partage",
    },
    "shares.forbidden_secret": {
        "en": "Secret documents can never be shared outside",
        "fr": "Un document secret ne peut jamais être partagé à l'extérieur",
    },
    "shares.forbidden_confidential": {
        "en": "Only an administrator can share a confidential document",
        "fr": "Seul un administrateur peut partager un document confidentiel",
    },
    "shares.not_found": {"en": "Share link not found", "fr": "Lien de partage introuvable"},
    "shares.page_title": {"en": "Shared document", "fr": "Document partagé"},
    "shares.page_intro": {
        "en": "A document has been shared with you:",
        "fr": "Un document a été partagé avec vous :",
    },
    "shares.page_expires": {"en": "Available until", "fr": "Disponible jusqu'au"},
    "shares.page_size": {"en": "Size", "fr": "Taille"},
    "shares.page_downloads_left": {"en": "Downloads left", "fr": "Téléchargements restants"},
    "shares.page_password": {"en": "Password", "fr": "Mot de passe"},
    "shares.page_download": {"en": "Download", "fr": "Télécharger"},
    "shares.page_wrong_password": {"en": "Wrong password", "fr": "Mot de passe incorrect"},
    "shares.page_footer": {
        "en": "Every access to this link is recorded.",
        "fr": "Chaque accès à ce lien est enregistré.",
    },
    "shares.invalid_title": {
        "en": "This link is not available",
        "fr": "Ce lien n'est pas disponible",
    },
    "shares.invalid_body": {
        "en": "It may have expired, been revoked or used up, or be mistyped. Ask the person who sent it for a new one.",
        "fr": "Il a peut-être expiré, été révoqué ou épuisé, ou il est mal recopié. Demandez-en un nouveau à la personne qui vous l'a envoyé.",
    },
    "shares.watermark_who": {"en": "share link {id}", "fr": "lien de partage {id}"},
    # ── export_router.py (selective export, GED Phase 5) ─────────────────
    "exports.empty": {
        "en": "Nothing to export - none of the selected documents can be downloaded by you",
        "fr": "Rien à exporter - aucun des documents choisis ne peut être téléchargé par vous",
    },
    "exports.too_many": {
        "en": "Too many documents for one export (maximum {max}) - narrow the selection",
        "fr": "Trop de documents pour un seul export (maximum {max}) - réduisez la sélection",
    },
    "exports.too_large": {
        "en": "This export would be {size} MB - the limit is {max} MB. Narrow the selection or export the current versions only",
        "fr": "Cet export ferait {size} Mo - la limite est de {max} Mo. Réduisez la sélection ou n'exportez que les versions courantes",
    },
    "exports.not_found": {"en": "Export not found", "fr": "Export introuvable"},
    "exports.not_available": {
        "en": "This export is not ready, has failed or has expired",
        "fr": "Cet export n'est pas prêt, a échoué ou a expiré",
    },
    "exports.still_running": {
        "en": "This export is still being built",
        "fr": "Cet export est encore en cours de préparation",
    },
    "common.invalid_id": {
        "en": "Invalid identifier",
        "fr": "Identifiant invalide",
    },
    # ── classification_router.py ─────────────────────────────────────────
    "classification.tag_not_found": {
        "en": "Tag not found",
        "fr": "Étiquette introuvable",
    },
    "classification.tag_exists": {
        "en": "A tag named '{name}' already exists",
        "fr": "Une étiquette « {name} » existe déjà",
    },
    "classification.tag_name_required": {
        "en": "Give either an existing tag or a tag name",
        "fr": "Indiquez soit une étiquette existante, soit un nom d'étiquette",
    },
    "classification.tag_merge_self": {
        "en": "A tag cannot be merged into itself",
        "fr": "Une étiquette ne peut pas être fusionnée avec elle-même",
    },
    "classification.series_cycle": {
        "en": "A series cannot be moved under itself or one of its sub-series",
        "fr": "Une série ne peut pas être déplacée sous elle-même ou sous une de ses sous-séries",
    },
    "classification.series_not_empty": {
        "en": "This series is not empty ({children} sub-series, {documents} documents, {rules} filing rules)",
        "fr": "Cette série n'est pas vide ({children} sous-séries, {documents} documents, {rules} règles de classement)",
    },
    # ── sync/admin.py ────────────────────────────────────────────────────
    "sync.conflict_not_found": {
        "en": "Sync conflict not found",
        "fr": "Conflit de synchronisation introuvable",
    },
    "sync.conflict_not_open": {
        "en": "This sync conflict is already resolved",
        "fr": "Ce conflit de synchronisation est déjà résolu",
    },
    "sync.bad_resolution": {
        "en": "This resolution does not apply to this conflict",
        "fr": "Cette résolution ne s'applique pas à ce conflit",
    },
    "sync.value_required": {
        "en": "A value is required for a manual resolution",
        "fr": "Une valeur est requise pour une résolution manuelle",
    },
    "sync.conflict_row_gone": {
        "en": "The record of this conflict no longer exists - dismiss it instead",
        "fr": "L'enregistrement de ce conflit n'existe plus - ignorez-le plutôt",
    },
    "ingest.local_node_only": {
        "en": "This is done on the local server (it runs the extraction) - open the document there",
        "fr": "Cette action se fait sur le serveur local (qui exécute l'extraction) - ouvrez le document là-bas",
    },
    "sync.file_pending": {
        "en": "This file is still being synchronised from the other server - try again shortly",
        "fr": "Fichier en cours de synchronisation depuis l'autre serveur - réessayez dans un instant",
    },
    "sync.retry_failed": {
        "en": "The change still cannot be applied - fix the cause first",
        "fr": "La modification ne peut toujours pas être appliquée - corrigez d'abord la cause",
    },
    # ── api_server.py ────────────────────────────────────────────────────
    "benchmarks.vlm_results_not_found": {
        "en": "VLM results file not found. Run vlm_ollama_test.py first.",
        "fr": "Fichier de résultats VLM introuvable. Exécutez d'abord vlm_ollama_test.py.",
    },
    "benchmarks.no_ocr_result": {
        "en": "No OCR result for this document",
        "fr": "Aucun résultat OCR pour ce document",
    },
    "benchmarks.all_results_not_found": {
        "en": "_all_results.json not found",
        "fr": "_all_results.json introuvable",
    },
    "benchmarks.no_vlm_result": {
        "en": "No VLM result for this document",
        "fr": "Aucun résultat VLM pour ce document",
    },
    "images.raw_not_found": {
        "en": "Raw image not found",
        "fr": "Image brute introuvable",
    },
    "images.preprocessed_not_found": {
        "en": "Preprocessed image not found",
        "fr": "Image prétraitée introuvable",
    },
    # ── lifecycle.py / lifecycle_router.py (GED Phase 6) ─────────────────
    "lifecycle.illegal_transition": {
        "en": "A document cannot go from '{frm}' to '{to}' this way",
        "fr": "Un document ne peut pas passer de « {frm} » à « {to} » de cette façon",
    },
    "lifecycle.destroyed": {
        "en": "The document has been destroyed",
        "fr": "Le document a été éliminé",
    },
    "lifecycle.in_trash": {
        "en": "The document is in the trash - restore it first",
        "fr": "Le document est dans la corbeille - restaurez-le d'abord",
    },
    "lifecycle.already_in_trash": {
        "en": "The document is already in the trash",
        "fr": "Le document est déjà dans la corbeille",
    },
    "lifecycle.not_in_trash": {
        "en": "The document is not in the trash",
        "fr": "Le document n'est pas dans la corbeille",
    },
    "lifecycle.trash_expired": {
        "en": "The document has been in the trash too long to be restored",
        "fr": "Le document est dans la corbeille depuis trop longtemps pour être restauré",
    },
    "lifecycle.restore_duplicate": {
        "en": "The same file was uploaded again since ({record}) - it cannot be restored",
        "fr": "Le même fichier a été déposé à nouveau depuis ({record}) - impossible de le restaurer",
    },
    "lifecycle.legal_hold": {
        "en": "The document is under legal hold",
        "fr": "Le document est sous gel juridique",
    },
    "lifecycle.extend_past": {
        "en": "The new retention date must be in the future",
        "fr": "La nouvelle date de conservation doit être dans le futur",
    },
    "lifecycle.read_only": {
        "en": "The document is archived - only a lifecycle manager can change it",
        "fr": "Le document est archivé - seul un gestionnaire du cycle de vie peut le modifier",
    },
    "lifecycle.manager_required": {
        "en": "Only a lifecycle manager (archivist) can do this",
        "fr": "Seul un gestionnaire du cycle de vie (archiviste) peut faire cela",
    },
    "retention.date_field_required": {
        "en": "A rule running from the document date needs the name of the date field",
        "fr": "Une règle qui court à partir de la date du document doit nommer le champ de date",
    },
    "retention.rule_exists": {
        "en": "This series or document type already has a retention rule",
        "fr": "Cette série ou ce type de document a déjà une règle de conservation",
    },
    "retention.rule_not_found": {
        "en": "Retention rule not found",
        "fr": "Règle de conservation introuvable",
    },
    "legal_hold.nothing_found": {
        "en": "The search finds no document",
        "fr": "La recherche ne trouve aucun document",
    },
    "legal_hold.too_many": {
        "en": "The search finds more than {max} documents - narrow it down",
        "fr": "La recherche trouve plus de {max} documents - affinez-la",
    },
    "legal_hold.not_found": {
        "en": "Legal hold not found or already released",
        "fr": "Gel juridique introuvable ou déjà levé",
    },
    "destruction.empty": {
        "en": "Select at least one document",
        "fr": "Sélectionnez au moins un document",
    },
    "destruction.not_eligible": {
        "en": "{record} is not eligible for destruction",
        "fr": "{record} n'est pas éliminable",
    },
    "destruction.held": {
        "en": "{record} is under legal hold",
        "fr": "{record} est sous gel juridique",
    },
    "destruction.already_proposed": {
        "en": "{record} is already in a pending destruction proposal",
        "fr": "{record} figure déjà dans une proposition d'élimination en attente",
    },
    "destruction.not_found": {
        "en": "Destruction record not found",
        "fr": "Bordereau d'élimination introuvable",
    },
    "destruction.not_pending": {
        "en": "This proposal has already been decided",
        "fr": "Cette proposition a déjà été traitée",
    },
    "destruction.same_person": {
        "en": "The person who proposed a destruction cannot approve it",
        "fr": "La personne qui a proposé une élimination ne peut pas l'approuver",
    },
    "destruction.not_proposer": {
        "en": "Only the proposer (or an admin) can withdraw this proposal",
        "fr": "Seul l'auteur de la proposition (ou un administrateur) peut la retirer",
    },
    "destruction.no_pv": {
        "en": "This destruction has not been executed - there is no report",
        "fr": "Cette élimination n'a pas été exécutée - il n'y a pas de procès-verbal",
    },
    # ── locks.py / checkout_router.py ────────────────────────────────────
    "locks.locked_by": {
        "en": 'Checked out by {name} - read-only until it is checked back in',
        "fr": "Verrouillé par {name} - en lecture seule jusqu'à sa réintégration",
    },
    "locks.not_holder": {
        "en": 'You have not checked this document out',
        "fr": "Vous n'avez pas extrait ce document",
    },
    "locks.not_locked": {
        "en": 'This document is not checked out',
        "fr": "Ce document n'est pas extrait",
    },
    # ── comments_router.py ───────────────────────────────────────────────
    "comments.not_found": {
        "en": 'Comment not found',
        "fr": 'Commentaire introuvable',
    },
    "comments.empty": {
        "en": 'A comment cannot be empty',
        "fr": 'Un commentaire ne peut pas être vide',
    },
    "comments.bad_parent": {
        "en": 'The comment you reply to does not belong to this document',
        "fr": "Le commentaire auquel vous répondez n'appartient pas à ce document",
    },
    "comments.not_author": {
        "en": 'Only the author can change this comment',
        "fr": "Seul l'auteur peut modifier ce commentaire",
    },
    # ── signature_router.py ──────────────────────────────────────────────
    "signatures.request_not_found": {
        "en": 'Signature request not found',
        "fr": 'Demande de signature introuvable',
    },
    "signatures.bad_signers": {
        "en": 'Every signer must be a different person who can read this document',
        "fr": 'Chaque signataire doit être une personne différente ayant accès à ce document',
    },
    "signatures.already_pending": {
        "en": 'A signature request is already in progress on this document',
        "fr": 'Une demande de signature est déjà en cours sur ce document',
    },
    "signatures.request_closed": {
        "en": 'This signature request is closed',
        "fr": 'Cette demande de signature est close',
    },
    "signatures.not_your_turn": {
        "en": 'It is not your turn to sign this document',
        "fr": "Ce n'est pas à vous de signer ce document maintenant",
    },
    "signatures.wrong_password": {
        "en": 'Incorrect password - the document was not signed',
        "fr": "Mot de passe incorrect - le document n'a pas été signé",
    },
    "signatures.not_configured": {
        "en": 'Electronic signature is not set up on this server (organisation certificate missing)',
        "fr": "La signature électronique n'est pas configurée sur ce serveur (certificat de l'organisation absent)",
    },
    "signatures.unsupported_file": {
        "en": 'Only PDF and image documents can be signed',
        "fr": 'Seuls les documents PDF et images peuvent être signés',
    },
    "signatures.cannot_sign": {
        "en": 'This file cannot be signed (protected or damaged PDF)',
        "fr": 'Ce fichier ne peut pas être signé (PDF protégé ou endommagé)',
    },
    "signatures.converted_pdfa": {
        "en": 'Version {n} converted to PDF/A for signature',
        "fr": 'Version {n} convertie en PDF/A pour signature',
    },
    "signatures.converted_pdf": {
        "en": 'Version {n} converted to PDF for signature',
        "fr": 'Version {n} convertie en PDF pour signature',
    },
    "signatures.signed_version": {
        "en": 'Electronically signed by {name}',
        "fr": 'Signé électroniquement par {name}',
    },
    "signatures.not_requester": {
        "en": 'Only the person who asked for the signatures (or an admin) can cancel',
        "fr": "Seul l'auteur de la demande (ou un administrateur) peut l'annuler",
    },
    # ── notifications.py (e-mails - never any document content) ─────────
    "email.greeting": {
        "en": 'Hello,',
        "fr": 'Bonjour,',
    },
    "email.open": {
        "en": 'Open: {url}',
        "fr": 'Ouvrir : {url}',
    },
    "email.footer": {
        "en": 'You receive this e-mail because of your notification preferences in SDAI. Change them here: {url}',
        "fr": 'Vous recevez cet e-mail en raison de vos préférences de notification dans SDAI. Pour les modifier : {url}',
    },
    "email.digest_intro": {
        "en": 'Here is what happened in SDAI during the last 24 hours (unread notifications):',
        "fr": "Voici ce qui s'est passé dans SDAI au cours des dernières 24 heures (notifications non lues) :",
    },
    "email.subject.digest": {
        "en": 'Your daily summary',
        "fr": 'Votre résumé quotidien',
    },
    "email.subject.review_pending": {
        "en": 'Documents to review',
        "fr": 'Documents à valider',
    },
    "email.body.review_pending": {
        "en": 'New documents are waiting for your review.',
        "fr": 'De nouveaux documents attendent votre validation.',
    },
    "email.subject.document_shared": {
        "en": 'A document was shared with you',
        "fr": 'Un document a été partagé avec vous',
    },
    "email.body.document_shared": {
        "en": 'A document was shared with you.',
        "fr": 'Un document a été partagé avec vous.',
    },
    "email.subject.mention": {
        "en": 'You were mentioned in a comment',
        "fr": 'Vous avez été mentionné dans un commentaire',
    },
    "email.body.mention": {
        "en": 'Someone mentioned you in a comment on a document.',
        "fr": "Quelqu'un vous a mentionné dans un commentaire sur un document.",
    },
    "email.subject.comment_reply": {
        "en": 'New reply to your comment',
        "fr": 'Nouvelle réponse à votre commentaire',
    },
    "email.body.comment_reply": {
        "en": 'Someone replied to your comment on a document.',
        "fr": "Quelqu'un a répondu à votre commentaire sur un document.",
    },
    "email.subject.retention_eligible": {
        "en": 'Retention periods have ended',
        "fr": 'Des durées de conservation sont échues',
    },
    "email.body.retention_eligible": {
        "en": 'Documents reached the end of their retention period and wait for a decision.',
        "fr": 'Des documents ont atteint la fin de leur durée de conservation et attendent une décision.',
    },
    "email.subject.destruction_proposed": {
        "en": 'A destruction awaits your approval',
        "fr": 'Une élimination attend votre approbation',
    },
    "email.body.destruction_proposed": {
        "en": 'A destruction proposal awaits your approval.',
        "fr": "Une proposition d'élimination attend votre approbation.",
    },
    "email.subject.sync_down": {
        "en": 'Synchronisation is down',
        "fr": 'La synchronisation est interrompue',
    },
    "email.body.sync_down": {
        "en": 'The synchronisation between the local and cloud servers has not succeeded for several hours.',
        "fr": "La synchronisation entre les serveurs local et cloud n'a pas abouti depuis plusieurs heures.",
    },
    "email.subject.backup_failed": {
        "en": 'A backup failed',
        "fr": 'Une sauvegarde a échoué',
    },
    "email.body.backup_failed": {
        "en": 'The last backup of a server failed.',
        "fr": "La dernière sauvegarde d'un serveur a échoué.",
    },
    "email.subject.signature_requested": {
        "en": 'Your signature is requested',
        "fr": 'Votre signature est demandée',
    },
    "email.body.signature_requested": {
        "en": 'You are asked to sign a document.',
        "fr": 'Il vous est demandé de signer un document.',
    },
    "email.subject.signature_completed": {
        "en": 'A document has been signed',
        "fr": 'Un document a été signé',
    },
    "email.body.signature_completed": {
        "en": 'Every requested signature of a document has been given.',
        "fr": 'Toutes les signatures demandées sur un document ont été apposées.',
    },
    "email.subject.signature_declined": {
        "en": 'A signature was declined',
        "fr": 'Une signature a été refusée',
    },
    "email.body.signature_declined": {
        "en": 'A signer declined to sign a document.',
        "fr": 'Un signataire a refusé de signer un document.',
    },
    "email.subject.lock_released": {
        "en": 'Your check-out was released',
        "fr": 'Votre extraction a été libérée',
    },
    "email.body.lock_released": {
        "en": 'An administrator released your check-out of a document.',
        "fr": "Un administrateur a libéré votre extraction d'un document.",
    },
}


def t(key: str, locale: str = DEFAULT_LOCALE, **kwargs) -> str:
    """Look up message `key` for `locale` (falling back to English, then
    to the bare key if even that's missing) and format it with kwargs."""
    table = MESSAGES.get(key)
    if table is None:
        return key
    template = table.get(locale) or table.get(DEFAULT_LOCALE) or key
    return template.format(**kwargs) if kwargs else template


def format_date(value, locale: str = DEFAULT_LOCALE) -> str:
    """A date for people: 14/10/2026 in French, 2026-10-14 otherwise.
    Takes a date, a datetime (its UTC day) or an ISO string; "-" for none."""
    from datetime import date, datetime, timezone

    if not value:
        return "-"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value[:10]
    if isinstance(value, datetime):
        value = value.astimezone(timezone.utc).date() if value.tzinfo else value.date()
    if not isinstance(value, date):
        return str(value)
    return value.strftime("%d/%m/%Y" if locale == "fr" else "%Y-%m-%d")


def format_datetime(value, locale: str = DEFAULT_LOCALE) -> str:
    """A UTC timestamp for people: 14/10/2026 13:05 UTC in French,
    2026-10-14 13:05 UTC otherwise; "-" for none."""
    from datetime import datetime, timezone

    if not value:
        return "-"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value[:16]
    if value.tzinfo:
        value = value.astimezone(timezone.utc)
    return value.strftime("%d/%m/%Y %H:%M UTC" if locale == "fr" else "%Y-%m-%d %H:%M UTC")
