"""Test the link to the peer node - one signed hello, no data exchanged:

    docker compose run --rm sync python -m sync.check

Exit code 0 when the peer answered (or when this node has no peer to
call: the cloud node waits for the local one), 1 otherwise, with the
likely cause - used by scripts/first-run.sh after configuring a node.
"""

from __future__ import annotations

import asyncio
import sys

from sync import config


def explain(exc: Exception) -> str:
    """What to check, for a failed hello."""
    from sync.worker import PeerError

    if isinstance(exc, PeerError):
        if exc.status == 401:
            return (
                "the peer refused the signature: SYNC_NODE_TOKEN differs between the two"
                " nodes, or their clocks are more than 5 minutes apart (enable NTP)"
            )
        if exc.status == 404:
            return "sync is disabled on the peer (SYNC_NODE_TOKEN is empty there) or the URL is wrong"
        return f"the peer answered HTTP {exc.status}"
    return (
        f"cannot reach {config.SYNC_PEER_URL} ({type(exc).__name__}: {exc}) - check the"
        " address, DNS, firewall and that the peer is running"
    )


async def check() -> int:
    import ingest_router
    from sync.worker import HttpPeer

    if not config.enabled():
        print("Sync is disabled on this node (SYNC_NODE_TOKEN is empty).")
        return 0
    if not config.SYNC_PEER_URL:
        print(
            f"Node {ingest_router.NODE_ID} has no SYNC_PEER_URL: it waits for its peer to"
            " connect (normal for the cloud node)."
        )
        return 0
    client = HttpPeer(config.SYNC_PEER_URL, ingest_router.NODE_ID, config.SYNC_NODE_TOKEN)
    try:
        hello = await client.hello()
    except Exception as exc:
        print(f"FAILED: {explain(exc)}", file=sys.stderr)
        return 1
    finally:
        await client.close()
    print(
        f"OK: {ingest_router.NODE_ID} reached {hello.get('node_id')}"
        f" ({hello.get('node_role')}) at {config.SYNC_PEER_URL}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(check()))
