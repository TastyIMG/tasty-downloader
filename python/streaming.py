import json

from aiohttp import web


NDJSON_HEADERS = {
    "Content-Type": "application/x-ndjson",
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


async def prepare_ndjson(request):
    response = web.StreamResponse(status=200, headers=NDJSON_HEADERS)
    await response.prepare(request)

    async def send_event(payload):
        await response.write((json.dumps(payload) + "\n").encode("utf-8"))
        await response.drain()

    return response, send_event
