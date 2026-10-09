"""Sync worker pieces that need no database (GED Phase 8.3)."""

import asyncio

import pytest
from sync import worker


@pytest.mark.parametrize("failures", [0, 1, 3, 20])
def test_backoff_grows_and_is_capped(failures):
    delay = worker.backoff_seconds(failures, 30)
    assert delay <= worker.MAX_BACKOFF_SECONDS
    assert delay >= 0.8 * min(worker.MAX_BACKOFF_SECONDS, 30 * 2 ** min(failures, 10))


def test_http_peer_throttles_to_max_kbps(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    async def go():
        monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)
        peer = worker.HttpPeer("http://peer", "local", "t", max_kbps=100)
        await peer._throttle(102_400)
        await peer._throttle(0)
        await peer.close()

    asyncio.run(go())
    assert slept == [pytest.approx(1.0)]


def test_http_peer_raises_on_error_status(monkeypatch):
    class Resp:
        status_code = 401
        text = "Bad sync signature"
        content = b""

    async def fake_request(*a, **kw):
        return Resp()

    async def go():
        peer = worker.HttpPeer("http://peer", "local", "t")
        monkeypatch.setattr(peer._http, "request", fake_request)
        try:
            await peer.hello()
        finally:
            await peer.close()

    with pytest.raises(worker.PeerError) as err:
        asyncio.run(go())
    assert err.value.status == 401
