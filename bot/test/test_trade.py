import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from routers.trading import router

app = FastAPI()
app.include_router(router)

client = TestClient(app)

@pytest.mark.asyncio
async def test_send_trade_command():
    response = client.post("/trade/command/", json={"asset": "BTC", "action": "buy"})
    assert response.status_code == 200
    assert response.json()["status"] == "success"
