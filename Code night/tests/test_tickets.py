import os
from typing import Dict

os.environ["DATABASE_URL"] = "sqlite:///./test_campusupport.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app, Base, engine, seed_departments  # noqa: E402
from app.database import SessionLocal  # noqa: E402


def setup_module():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    seed_departments()


def register_and_login(email: str, password: str) -> str:
    client = TestClient(app)
    client.post(
        "/auth/register",
        json={"email": email, "password": password, "role": "student"},
    )
    res = client.post("/auth/token", data={"username": email, "password": password})
    assert res.status_code == 200
    return res.json()["access_token"]


def auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_ticket_creation_and_fetch():
    token = register_and_login("student@test.com", "Pass123!")
    client = TestClient(app)
    # seed_departments runs on startup; department id 1 exists
    res = client.post(
        "/tickets",
        headers=auth_headers(token),
        json={
            "title": "WiFi issue",
            "description": "wifi kopuyor yurt",
            "department_id": 1,
            "priority": "high",
        },
    )
    assert res.status_code == 201, res.text
    ticket_id = res.json()["id"]

    res = client.get(f"/tickets/{ticket_id}", headers=auth_headers(token))
    assert res.status_code == 200
    data = res.json()
    assert data["title"] == "WiFi issue"
    assert data["priority"] == "high"


def test_ai_suggestion_stub():
    client = TestClient(app)
    res = client.post("/ai/suggest", json={"description": "wifi surekli kopuyor"})
    assert res.status_code == 200
    data = res.json()
    assert data["suggested_priority"] in {"medium", "high", "low"}
    assert "suggested_category" in data


def test_ai_insights_stub():
    token = register_and_login("student2@test.com", "Pass123!")
    client = TestClient(app)
    res = client.post(
        "/tickets",
        headers=auth_headers(token),
        json={
            "title": "Projeksiyon bozuk",
            "description": "projeksiyon calismiyor 302",
            "department_id": 1,
            "priority": "medium",
        },
    )
    assert res.status_code == 201
    ticket_id = res.json()["id"]

    res = client.get(f"/tickets/{ticket_id}/ai-insights", headers=auth_headers(token))
    assert res.status_code == 200
    data = res.json()
    assert "summary" in data and data["summary"]
    assert "draft_reply" in data and data["draft_reply"]
