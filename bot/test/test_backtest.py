import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from routers.backtest import router

app = FastAPI()
app.include_router(router)

client = TestClient(app)

@pytest.mark.asyncio
async def test_start_backtest():
    response = client.post("/backtest/", json={
        "asset": "BTC", 
        "strategy_name": "SMA", 
        "start_date": "2021-01-01", 
        "end_date": "2021-12-31"
    })
    assert response.status_code == 200
    assert response.json()["status"] == "success"
