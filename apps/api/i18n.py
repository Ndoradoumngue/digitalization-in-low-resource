"""
Backend response localization — English/French.

The frontend sends the user's chosen UI language on every request as the
standard `Accept-Language` header (see packages/api-client's fetch
wrapper, which reads it from the same localStorage key the language
switcher writes to). `resolve_locale` turns that into "en" or "fr",
defaulting to "en" for anything else (missing header, browser default,
an unsupported language). `t()` looks up a message by a stable key and
formats it for that locale — this is the single source of truth for
every user-facing string an API error/response returns; call sites
should never inline an English literal in `detail=`.
"""

from typing import Optional

from fastapi import Header

SUPPORTED_LOCALES = ("en", "fr")
DEFAULT_LOCALE = "en"


def resolve_locale(accept_language: Optional[str] = Header(default=None)) -> str:
    """FastAPI dependency — reads the Accept-Language header and returns
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
    "auth.incorrect_credentials": {
        "en": "Incorrect email or password",
        "fr": "E-mail ou mot de passe incorrect",
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
        "en": "No extracted document found for this upload — it may not have finished processing yet, or was already deleted.",
        "fr": "Aucun document extrait trouvé pour ce téléversement — il n'a peut-être pas encore fini d'être traité, ou a déjà été supprimé.",
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
