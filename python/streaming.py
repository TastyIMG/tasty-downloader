import asyncio
import json

from aiohttp import web


NDJSON_HEADERS = {
    "Content-Type": "application/x-ndjson",
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


async def client_disconnected(request):
    """Compatible disconnect check across aiohttp versions."""
    check = getattr(request, "is_disconnected", None)
    if callable(check):
        result = check()
        if asyncio.iscoroutine(result):
            return await result
        return bool(result)

    transport = getattr(request, "transport", None)
    if transport is None:
        return False
    is_closing = getattr(transport, "is_closing", None)
    if callable(is_closing):
        return bool(is_closing())
    return False


async def prepare_ndjson(request):
    response = web.StreamResponse(status=200, headers=NDJSON_HEADERS)
    await response.prepare(request)

    async def send_event(payload):
        await response.write((json.dumps(payload) + "\n").encode("utf-8"))
        await response.drain()

    return response, send_event
