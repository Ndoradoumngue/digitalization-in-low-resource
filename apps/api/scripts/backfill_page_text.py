#!/usr/bin/env python3
"""
Transcribe every page that has no text yet (GED full-text search).

New pages are read by the API's own page-text worker as they are ingested;
this fills in the rest - pages ingested before the feature existed, and
any page whose transcription was lost to a restart or failed (missing
image, Tesseract error). Run it on the LOCAL node only: the cloud node
receives page text through sync.

Usage (from apps/api, or /app in the api container):
  python scripts/backfill_page_text.py                 # every tenant
  python scripts/backfill_page_text.py --tenant land   # one tenant
  python scripts/backfill_page_text.py --dry-run       # count only
  python scripts/backfill_page_text.py --sleep 1 --limit 5000

  docker compose exec api python scripts/backfill_page_text.py

Idempotent and resumable: only pages with ocr_text IS NULL are read, and
each page is saved as soon as it is read - stop it any time (Ctrl+C) and
run it again to continue. Throttled: one page at a time, --sleep seconds
between pages, so the pipeline running next to it keeps its CPU.
Pages whose image can't be read stay NULL and are listed at the end.
"""

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import page_text  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://sdai:sdai@localhost:5432/sdai"
)
NODE_ID = os.getenv("NODE_ID", "local")
NODE_ROLE = os.getenv("NODE_ROLE", "local")

_PENDING_SQL = """
    SELECT p.id::text AS id, p.image_path
    FROM batch_document_pages p
    JOIN batch_documents bd ON bd.id = p.batch_document_id
    JOIN batches b ON b.id = bd.batch_id
    JOIN sdai_tenants tn ON tn.id = b.tenant_id
    WHERE p.ocr_text IS NULL
      AND p.id > CAST(:after AS uuid)
      AND (CAST(:tenant AS text) IS NULL OR tn.slug = :tenant)
    ORDER BY p.id
    LIMIT :n
"""

_COUNT_SQL = """
    SELECT count(*)
    FROM batch_document_pages p
    JOIN batch_documents bd ON bd.id = p.batch_document_id
    JOIN batches b ON b.id = bd.batch_id
    JOIN sdai_tenants tn ON tn.id = b.tenant_id
    WHERE p.ocr_text IS NULL
      AND (CAST(:tenant AS text) IS NULL OR tn.slug = :tenant)
"""


async def backfill(
    engine,
    *,
    tenant: str | None = None,
    batch_size: int = 50,
    sleep: float = 0.2,
    limit: int | None = None,
    dry_run: bool = False,
    transcribe=page_text.transcribe,
    out=sys.stdout,
) -> dict:
    """Read every untranscribed page (keyset order by id, so one run never
    loops on a page that keeps failing). Returns the counts."""
    stats = {"todo": 0, "transcribed": 0, "empty": 0, "failed": 0, "gone": 0}
    async with engine.connect() as conn:
        stats["todo"] = int(
            (await conn.execute(text(_COUNT_SQL), {"tenant": tenant})).scalar()
        )
    print(f"{stats['todo']} page(s) without text", file=out)
    if dry_run or not stats["todo"]:
        return stats

    after = "00000000-0000-0000-0000-000000000000"
    seen = 0
    started = time.monotonic()
    failures: list[str] = []
    while limit is None or seen < limit:
        n = batch_size if limit is None else min(batch_size, limit - seen)
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(_PENDING_SQL), {"after": after, "tenant": tenant, "n": n}
                )
            ).all()
        if not rows:
            break
        for page_id, image_path in rows:
            after = page_id
            seen += 1
            try:
                ocr_text = await asyncio.to_thread(transcribe, Path(image_path))
            except Exception as exc:  # noqa: BLE001 - reported, the run goes on
                stats["failed"] += 1
                failures.append(
                    f"{page_id} {image_path}: {str(exc)[:200] or type(exc).__name__}"
                )
                continue
            async with engine.begin() as conn:
                await conn.execute(text(f"SET LOCAL sdai.node_id = '{NODE_ID}'"))
                saved = await page_text.store(
                    conn, page_id, ocr_text, only_if_missing=True
                )
            if not saved:
                stats["gone"] += 1
            elif ocr_text:
                stats["transcribed"] += 1
            else:
                stats["empty"] += 1
            if sleep:
                await asyncio.sleep(sleep)
        elapsed = time.monotonic() - started
        print(
            f"  {seen}/{stats['todo']} read ({stats['transcribed']} with text, {stats['empty']} empty,"
            f" {stats['failed']} failed) - {elapsed:.0f} s",
            file=out,
        )

    for line in failures:
        print(f"  failed: {line}", file=out)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--tenant", help="only this tenant's pages (slug)")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument(
        "--sleep", type=float, default=0.2, help="seconds to pause between pages"
    )
    parser.add_argument("--limit", type=int, help="stop after this many pages")
    parser.add_argument(
        "--dry-run", action="store_true", help="only count the pages without text"
    )
    args = parser.parse_args(argv)

    if NODE_ROLE != "local":
        print(
            "Run this on the local node - the cloud node receives page text through sync.",
            file=sys.stderr,
        )
        return 2
    if not page_text.OCR_ENABLED and not args.dry_run:
        print("OCR_ENABLED is off on this node - nothing to do.", file=sys.stderr)
        return 2

    async def go() -> dict:
        engine = create_async_engine(DATABASE_URL, echo=False)
        try:
            return await backfill(
                engine,
                tenant=args.tenant,
                batch_size=max(1, args.batch_size),
                sleep=max(0.0, args.sleep),
                limit=args.limit,
                dry_run=args.dry_run,
            )
        finally:
            await engine.dispose()

    try:
        stats = asyncio.run(go())
    except KeyboardInterrupt:
        print("Interrupted - run again to continue where it stopped.", file=sys.stderr)
        return 130
    print(
        f"Done: {stats['transcribed']} transcribed, {stats['empty']} without text,"
        f" {stats['failed']} failed, {stats['gone']} deleted meanwhile."
    )
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
