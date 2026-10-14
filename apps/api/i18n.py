"""
Backend response localization - English/French.

The frontend sends the user's chosen UI language on every request as the
standard `Accept-Language` header (see packages/api-client's fetch
wrapper, which reads it from the same localStorage key the language
switcher writes to). `resolve_locale` turns that into "en" or "fr",
defaulting to "en" for anything else (missing header, browser default,
an unsupported language). `t()` looks up a message by a stable key and
formats it for that locale - this is the single source of truth for
every user-facing string an API error/response returns; call sites
should never inline an English literal in `detail=`.
"""

from fastapi import Header

SUPPORTED_LOCALES = ("en", "fr")
DEFAULT_LOCALE = "en"


def resolve_locale(accept_language: str | None = Header(default=None)) -> str:
    """FastAPI dependency - reads the Accept-Language header and returns
    a supported locale code, defaulting to English."""
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
}


def t(key: str, locale: str = DEFAULT_LOCALE, **kwargs) -> str:
    """Look up message `key` for `locale` (falling back to English, then
    to the bare key if even that's missing) and format it with kwargs."""
    table = MESSAGES.get(key)
    if table is None:
        return key
    template = table.get(locale) or table.get(DEFAULT_LOCALE) or key
    return template.format(**kwargs) if kwargs else template
