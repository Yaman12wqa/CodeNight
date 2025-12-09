import asyncio
import logging
import os
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, status

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agent-service")

AGENT_SHARED_SECRET = os.getenv("AGENT_SHARED_SECRET", "agent-secret")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "dev-internal-secret")
TICKET_SERVICE_URL = os.getenv("TICKET_SERVICE_URL", "http://ticket-service:8000")
AI_API_KEY = os.getenv("AI_API_KEY")
AI_API_BASE = os.getenv("AI_API_BASE")
CALENDAR_API_BASE = os.getenv("CALENDAR_API_BASE", "")

app = FastAPI(title="CampuSupport Agent", version="0.1.0")


def require_agent_secret(x_agent_key: str = Header(None)):
    if not x_agent_key or x_agent_key != AGENT_SHARED_SECRET:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent key")
    return True


@app.get("/health")
def health():
    return {"status": "ok", "service": "agent-service"}


async def call_ticket_service(path: str, method: str = "GET", json: Optional[dict] = None):
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.request(method, f"{TICKET_SERVICE_URL}{path}", headers=headers, json=json)
        resp.raise_for_status()
        return resp.json()


async def call_ai(prompt: str, purpose: str) -> Optional[str]:
    if not AI_API_KEY or not AI_API_BASE:
        logger.info("agent.ai.skip", extra={"purpose": purpose, "reason": "no_api_key"})
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                AI_API_BASE,
                headers={"Authorization": f"Bearer {AI_API_KEY}"},
                json={"input": prompt, "purpose": purpose},
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info("agent.ai.ok", extra={"purpose": purpose})
            return data.get("result") or data.get("text")
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent.ai.fail", extra={"purpose": purpose, "error": str(exc)})
        return None


def heuristic_priority(text: str) -> str:
    lowered = text.lower()
    if any(k in lowered for k in ["acil", "urgent", "kopuyor", "kilit", "down", "calismiyor"]):
        return "high"
    if any(k in lowered for k in ["yavas", "gecik", "slow"]):
        return "medium"
    return "low"


def heuristic_category(text: str) -> str:
    lowered = text.lower()
    if any(k in lowered for k in ["wifi", "internet", "lms", "vpn", "modem"]):
        return "Internet"
    if any(k in lowered for k in ["projeksiyon", "monitor", "ekran", "donanim", "bilgisayar", "lab"]):
        return "Donanim"
    if any(k in lowered for k in ["randevu", "danisman", "kayit", "transkript", "ogrenci"]):
        return "Ogrenci Islemleri"
    return "Genel"


def pick_unit(category: str) -> str:
    mapping = {
        "Internet": "Network",
        "Donanim": "Donanim",
        "Ogrenci Islemleri": "OgrenciIsleri",
    }
    return mapping.get(category, "Genel")


async def mock_calendar_flow(description: str) -> Optional[str]:
    if not any(k in description.lower() for k in ["randevu", "danisman"]):
        return None
    logger.info("agent.calendar.check", extra={"desc": description})
    # If external calendar API configured, call it; otherwise return stub.
    if CALENDAR_API_BASE:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{CALENDAR_API_BASE}/slots?service=advisor")
                resp.raise_for_status()
                data = resp.json()
                slot = data.get("slots", ["2025-01-10 10:00"])[0]
                logger.info("agent.calendar.ok", extra={"slot": slot})
                return slot
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent.calendar.fail", extra={"error": str(exc)})
    slot = "2025-01-10 10:00"
    logger.info("agent.calendar.stub", extra={"slot": slot})
    return slot


@app.post("/process/{ticket_id}")
async def process_ticket(
    ticket_id: int,
    _: bool = Depends(require_agent_secret),
):
    logger.info("agent.process.start", extra={"ticket_id": ticket_id})
    try:
        ticket = await call_ticket_service(f"/internal/tickets/{ticket_id}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent.ticket.fetch_failed", extra={"ticket_id": ticket_id, "error": str(exc)})
        raise HTTPException(status_code=502, detail="Ticket service unreachable")

    user_id = ticket.get("created_by_id")
    try:
        summary = await call_ticket_service(f"/internal/users/{user_id}/tickets/summary")
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent.summary.fail", extra={"user_id": user_id, "error": str(exc)})
        summary = {"total": 0, "recent_ids": [], "recent_titles": []}

    description = ticket.get("description", "")
    ai_text = await call_ai(
        f"Ticket aciklamasina gore kategori ve oncelik belirle: {description}. Sonuc: kategori, oncelik.",
        purpose="agent-classify",
    )
    if ai_text and "," in ai_text:
        parts = [p.strip() for p in ai_text.split(",")]
        category = parts[0] or heuristic_category(description)
        priority = (parts[1] if len(parts) > 1 else heuristic_priority(description)).lower()
    else:
        category = heuristic_category(description)
        priority = heuristic_priority(description)

    assigned_unit = pick_unit(category)

    slot = await mock_calendar_flow(description)
    sla_hint = "SLA: 24 saat"
    msg = f"Talebiniz {assigned_unit} birimine yonlendirildi. {sla_hint}. Oncelik: {priority}."
    if slot:
        msg += f" Onerilen randevu: {slot}."
    if summary.get("total", 0) > 0:
        msg += f" Daha once {summary['total']} talebiniz var."

    try:
        update_payload = {
            "priority": priority,
            "category": category,
            "assigned_unit": assigned_unit,
            "message": msg,
        }
        await call_ticket_service(f"/internal/tickets/{ticket_id}/agent-update", method="POST", json=update_payload)
        logger.info("agent.ticket.updated", extra={"ticket_id": ticket_id, "category": category, "priority": priority})
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent.ticket.update_failed", extra={"ticket_id": ticket_id, "error": str(exc)})
        raise HTTPException(status_code=502, detail="Ticket update failed")

    return {
        "ticket_id": ticket_id,
        "category": category,
        "priority": priority,
        "assigned_unit": assigned_unit,
        "message": msg,
        "user_summary": summary,
    }
