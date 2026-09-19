"""Standalone demo receiver: fails twice then succeeds, for manual/demo runs."""
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

app = FastAPI()
state = {"count": 0}

@app.post("/receive")
async def receive(request: Request):
    body = await request.json()
    state["count"] += 1
    print(f"[receiver] call #{state['count']} eventId={body.get('eventId')}")
    if state["count"] <= 2:
        return PlainTextResponse("simulated outage", status_code=503)
    return PlainTextResponse("ok", status_code=200)
