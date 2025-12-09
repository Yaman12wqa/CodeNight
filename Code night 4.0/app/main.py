import logging
from datetime import date, datetime, timedelta
from typing import List, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from . import auth, models, schemas
from .config import settings
from .database import Base, SessionLocal, engine
from .dependencies import get_current_active_user, get_db, verify_internal_secret

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("campusupport")

app = FastAPI(title=settings.app_name, version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event():
    Base.metadata.create_all(bind=engine)
    run_sqlite_migrations()
    seed_departments()
    seed_agent_bot()


def seed_departments():
    """Create default departments if database is empty."""
def run_sqlite_migrations():
    """Lightweight migration to add new columns on SQLite without Alembic."""
    if "sqlite" not in settings.database_url:
        return
    with engine.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(tickets)").fetchall()}
        if "category" not in cols:
            conn.exec_driver_sql("ALTER TABLE tickets ADD COLUMN category VARCHAR")
        if "assigned_unit" not in cols:
            conn.exec_driver_sql("ALTER TABLE tickets ADD COLUMN assigned_unit VARCHAR")


    defaults = [
        {"name": "Bilgi Islem", "description": "Teknik destek ve altyapi"},
        {"name": "Yapi Isleri", "description": "Kampus bakim ve fiziksel sorunlar"},
        {"name": "Ogrenci Isleri", "description": "Akademik ve idari islemler"},
    ]
    db = SessionLocal()
    try:
        existing = {d.name for d in db.query(models.Department).all()}
        for dep in defaults:
            if dep["name"] not in existing:
                db.add(models.Department(**dep))
        db.commit()
    finally:
        db.close()


def seed_agent_bot():
    """Create a system bot user for agent comments."""
    db = SessionLocal()
    try:
        bot = db.query(models.User).filter(models.User.email == "agent@system.local").first()
        if not bot:
            bot_user = models.User(
                email="agent@system.local",
                full_name="Agent Bot",
                hashed_password=auth.get_password_hash("agent-system"),
                role=models.RoleEnum.admin,
                department_id=None,
            )
            db.add(bot_user)
            db.commit()
    finally:
        db.close()


def build_comment_payload(comment: models.Comment) -> schemas.CommentPublic:
    return schemas.CommentPublic(
        id=comment.id,
        content=comment.content,
        author_id=comment.author_id,
        author_email=comment.author.email if comment.author else "",
        created_at=comment.created_at,
    )


def build_ticket_payload(ticket: models.Ticket) -> schemas.TicketDetailed:
    return schemas.TicketDetailed(
        id=ticket.id,
        title=ticket.title,
        description=ticket.description,
        category=ticket.category,
        assigned_unit=ticket.assigned_unit,
        priority=ticket.priority,
        status=ticket.status,
        department_id=ticket.department_id,
        department=schemas.DepartmentPublic.model_validate(ticket.department, from_attributes=True)
        if ticket.department
        else None,
        assigned_to_id=ticket.assigned_to_id,
        assignee_email=ticket.assignee.email if ticket.assignee else None,
        created_by_id=ticket.created_by_id,
        creator_email=ticket.creator.email if ticket.creator else None,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        first_response_at=ticket.first_response_at,
        resolved_at=ticket.resolved_at,
        closed_at=ticket.closed_at,
        comments=[build_comment_payload(c) for c in sorted(ticket.comments, key=lambda x: x.created_at)],
    )


def ensure_ticket_visibility(ticket: models.Ticket, user: models.User):
    if user.role == models.RoleEnum.admin:
        return
    if ticket.created_by_id == user.id:
        return
    if user.role in {models.RoleEnum.department, models.RoleEnum.support} and user.department_id == ticket.department_id:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You cannot view this ticket")


@app.get("/")
def root():
    return {"message": f"{settings.app_name} API is running", "docs": "/docs", "frontend": "/frontend"}


@app.get("/health")
def health():
    return {"status": "ok", "service": "ticket-service"}


@app.get("/internal/health")
def internal_health(_: bool = Depends(verify_internal_secret)):
    return {"status": "ok", "service": "ticket-service"}


@app.post("/auth/register", response_model=schemas.UserPublic, status_code=status.HTTP_201_CREATED)
def register_user(user_in: schemas.UserCreate, db: Session = Depends(get_db)):
    existing = db.query(models.User).filter(models.User.email == user_in.email).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    if user_in.role in {models.RoleEnum.support, models.RoleEnum.department} and not user_in.department_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Department is required for this role")

    hashed_password = auth.get_password_hash(user_in.password)
    user = models.User(
        email=user_in.email,
        full_name=user_in.full_name,
        hashed_password=hashed_password,
        role=user_in.role,
        department_id=user_in.department_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.post("/auth/token", response_model=schemas.Token)
def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)
):
    user = db.query(models.User).filter(models.User.email == form_data.username).first()
    if not user or not auth.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    access_token = auth.create_access_token({"sub": str(user.id), "role": user.role})
    return {"access_token": access_token, "token_type": "bearer"}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Fail closed with a generic message while still returning a 500
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": "Internal server error"})


def simple_priority_guess(text: str) -> models.TicketPriority:
    lowered = text.lower()
    if any(k in lowered for k in ["acil", "urgent", "kopuyor", "kilit", "down", "calismiyor"]):
        return models.TicketPriority.high
    if any(k in lowered for k in ["yavas", "gecik", "slow"]):
        return models.TicketPriority.medium
    return models.TicketPriority.low


def simple_category_guess(text: str) -> str:
    lowered = text.lower()
    if any(k in lowered for k in ["wifi", "internet", "lms", "vpn", "modem"]):
        return "Internet"
    if any(k in lowered for k in ["projeksiyon", "monitor", "ekran", "donanim", "bilgisayar", "lab"]):
        return "Donanim"
    if any(k in lowered for k in ["randevu", "danisman", "kayit", "transkript", "ogrenci"]):
        return "Ogrenci Islemleri"
    return "Genel"


async def call_ai_service(prompt: str, purpose: str) -> Optional[str]:
    if not settings.ai_api_key or not settings.ai_api_base:
        logger.info("ai.call.skipped", extra={"purpose": purpose, "reason": "no_api_key"})
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                settings.ai_api_base,
                headers={"Authorization": f"Bearer {settings.ai_api_key}"},
                json={"input": prompt, "purpose": purpose},
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info("ai.call.success", extra={"purpose": purpose})
            return data.get("result") or data.get("text")
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai.call.failed", extra={"purpose": purpose, "error": str(exc)})
        return None


def build_summary_stub(text: str) -> str:
    words = text.split()
    if len(words) <= 30:
        return text
    return " ".join(words[:30]) + " ..."


def build_reply_stub(text: str) -> str:
    return (
        "Merhaba, bildiriminiz icin tesekkurler. Problemi incelemeye basladik. "
        "Gerekli kontrolleri yapip size kisa surede donus yapacagiz."
    )


def send_resolution_notification(ticket: models.Ticket) -> None:
    if not settings.notify_webhook_url:
        logger.info("notify.skipped", extra={"ticket_id": ticket.id, "reason": "no_webhook"})
        return
    payload = {
        "ticket_id": ticket.id,
        "title": ticket.title,
        "status": ticket.status,
        "description": ticket.description,
    }
    try:
        resp = httpx.post(settings.notify_webhook_url, json=payload, timeout=8)
        resp.raise_for_status()
        logger.info("notify.sent", extra={"ticket_id": ticket.id})
    except Exception as exc:  # noqa: BLE001
        logger.warning("notify.failed", extra={"ticket_id": ticket.id, "error": str(exc)})


@app.get("/users/me", response_model=schemas.UserPublic)
def read_users_me(current_user: models.User = Depends(get_current_active_user)):
    return current_user


@app.get("/departments", response_model=List[schemas.DepartmentPublic])
def list_departments(db: Session = Depends(get_db)):
    return db.query(models.Department).order_by(models.Department.name).all()


@app.get("/departments/{department_id}/supports", response_model=List[schemas.UserPublic])
def list_support_users(
    department_id: int,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in {models.RoleEnum.department, models.RoleEnum.admin}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only department/admin can view support users")
    if current_user.role == models.RoleEnum.department and current_user.department_id != department_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your department")
    return (
        db.query(models.User)
        .filter(models.User.role == models.RoleEnum.support, models.User.department_id == department_id)
        .all()
    )


@app.post("/tickets", response_model=schemas.TicketDetailed, status_code=status.HTTP_201_CREATED)
def create_ticket(
    ticket_in: schemas.TicketCreate,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    if current_user.role == models.RoleEnum.support:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Support users cannot create tickets")
    department = db.get(models.Department, ticket_in.department_id)
    if not department:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")

    ticket = models.Ticket(
        title=ticket_in.title,
        description=ticket_in.description,
        category=ticket_in.category,
        assigned_unit=ticket_in.assigned_unit,
        priority=ticket_in.priority,
        status=models.TicketStatus.open,
        department_id=ticket_in.department_id,
        created_by_id=current_user.id,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    logger.info("ticket.created", extra={"ticket_id": ticket.id, "created_by": current_user.id})
    return build_ticket_payload(ticket)


@app.post("/ai/suggest", response_model=schemas.AISuggestResponse)
async def ai_suggest(payload: schemas.AISuggestRequest):
    description = payload.description
    prompt = (
        "Ticket aciklamasina gore kategori ve oncelik oner: "
        f"{description}\nYanit sadece kategori ve oncelik olsun."
    )
    ai_result = await call_ai_service(prompt, purpose="suggest")
    suggested_category = ai_result or simple_category_guess(description)
    suggested_priority = simple_priority_guess(description)
    logger.info("ai.suggest", extra={"used_ai": bool(ai_result)})
    return schemas.AISuggestResponse(suggested_category=suggested_category, suggested_priority=suggested_priority)


@app.get("/tickets/me", response_model=List[schemas.TicketDetailed])
def list_my_tickets(current_user: models.User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    tickets = (
        db.query(models.Ticket)
        .filter(models.Ticket.created_by_id == current_user.id)
        .order_by(models.Ticket.created_at.desc())
        .all()
    )
    return [build_ticket_payload(t) for t in tickets]


@app.get("/tickets/{ticket_id}", response_model=schemas.TicketDetailed)
def get_ticket(ticket_id: int, current_user: models.User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    ticket = db.get(models.Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    ensure_ticket_visibility(ticket, current_user)
    return build_ticket_payload(ticket)


@app.get("/tickets/{ticket_id}/ai-insights", response_model=schemas.AIInsightResponse)
async def ticket_ai_insights(
    ticket_id: int,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    ticket = db.get(models.Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    ensure_ticket_visibility(ticket, current_user)

    description = ticket.description
    prompt = f"Bu ticket metnini ozetle ve destek personeli icin cevap taslagi oner: {description}"
    ai_text = await call_ai_service(prompt, purpose="summary")
    if ai_text:
        parts = ai_text.split("\n", 1)
        summary = parts[0].strip()
        draft = parts[1].strip() if len(parts) > 1 else build_reply_stub(description)
        logger.info("ai.summary", extra={"ticket_id": ticket.id, "used_ai": True})
        return schemas.AIInsightResponse(summary=summary, draft_reply=draft)

    logger.info("ai.summary.stub", extra={"ticket_id": ticket.id, "used_ai": False})
    return schemas.AIInsightResponse(summary=build_summary_stub(description), draft_reply=build_reply_stub(description))


@app.patch("/tickets/{ticket_id}", response_model=schemas.TicketDetailed)
def update_ticket(
    ticket_id: int,
    payload: schemas.TicketUpdate,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    ticket = db.get(models.Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    if current_user.role == models.RoleEnum.support:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Support users cannot edit tickets")
    if current_user.role == models.RoleEnum.student and ticket.created_by_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only edit your tickets")
    if current_user.role == models.RoleEnum.department and ticket.department_id != current_user.department_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your department")
    if ticket.status == models.TicketStatus.closed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Closed tickets cannot be edited")

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return build_ticket_payload(ticket)
    for field, value in updates.items():
        setattr(ticket, field, value)
    ticket.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(ticket)
    return build_ticket_payload(ticket)


@app.get("/tickets", response_model=List[schemas.TicketDetailed])
def list_tickets(
    department_id: Optional[int] = None,
    status_filter: Optional[models.TicketStatus] = Query(default=None, alias="status"),
    priority: Optional[models.TicketPriority] = None,
    order_by_priority: bool = False,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    query = db.query(models.Ticket)

    if current_user.role == models.RoleEnum.student:
        query = query.filter(models.Ticket.created_by_id == current_user.id)
    elif current_user.role in {models.RoleEnum.department, models.RoleEnum.support}:
        query = query.filter(models.Ticket.department_id == current_user.department_id)
    elif department_id:
        query = query.filter(models.Ticket.department_id == department_id)

    if status_filter:
        query = query.filter(models.Ticket.status == status_filter)
    if priority:
        query = query.filter(models.Ticket.priority == priority)
    if order_by_priority:
        priority_order = {
            models.TicketPriority.high: 3,
            models.TicketPriority.medium: 2,
            models.TicketPriority.low: 1,
        }
        query = sorted(query.all(), key=lambda t: priority_order[t.priority], reverse=True)
        return [build_ticket_payload(t) for t in query]

    tickets = query.order_by(models.Ticket.created_at.desc()).all()
    return [build_ticket_payload(t) for t in tickets]


@app.patch("/tickets/{ticket_id}/assign", response_model=schemas.TicketDetailed)
def assign_ticket(
    ticket_id: int,
    payload: schemas.TicketAssign,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in {models.RoleEnum.department, models.RoleEnum.admin}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only department/admin can assign tickets")
    ticket = db.get(models.Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    if current_user.role == models.RoleEnum.department and ticket.department_id != current_user.department_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ticket is not in your department")

    support_user = db.get(models.User, payload.support_user_id)
    if not support_user or support_user.role != models.RoleEnum.support:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Support user not found")
    if support_user.department_id != ticket.department_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Support user is in a different department")

    ticket.assigned_to_id = support_user.id
    ticket.assigned_at = datetime.utcnow()
    db.commit()
    db.refresh(ticket)
    return build_ticket_payload(ticket)


@app.patch("/tickets/{ticket_id}/status", response_model=schemas.TicketDetailed)
def update_ticket_status(
    ticket_id: int,
    payload: schemas.TicketStatusUpdate,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    ticket = db.get(models.Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    if current_user.role == models.RoleEnum.support:
        if ticket.assigned_to_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only assigned support can update status")
    elif current_user.role == models.RoleEnum.department:
        if ticket.department_id != current_user.department_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your department")
    elif current_user.role == models.RoleEnum.student:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Students cannot change status")

    now = datetime.utcnow()
    ticket.status = payload.status
    ticket.updated_at = now
    if payload.status == models.TicketStatus.in_progress and ticket.first_response_at is None:
        ticket.first_response_at = now
    if payload.status in {models.TicketStatus.resolved, models.TicketStatus.closed} and ticket.resolved_at is None:
        ticket.resolved_at = now
    if payload.status == models.TicketStatus.closed:
        ticket.closed_at = now
    db.commit()
    db.refresh(ticket)
    if payload.status == models.TicketStatus.resolved:
        send_resolution_notification(ticket)
    return build_ticket_payload(ticket)


@app.delete("/tickets/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ticket(
    ticket_id: int,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    ticket = db.get(models.Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    if current_user.role == models.RoleEnum.admin:
        pass
    elif current_user.role == models.RoleEnum.department and ticket.department_id == current_user.department_id:
        pass
    elif (
        current_user.role == models.RoleEnum.student
        and ticket.created_by_id == current_user.id
        and ticket.status == models.TicketStatus.open
    ):
        pass
    else:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You cannot delete this ticket")

    db.delete(ticket)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/tickets/{ticket_id}/comments", response_model=schemas.CommentPublic, status_code=status.HTTP_201_CREATED)
def add_comment(
    ticket_id: int,
    payload: schemas.CommentCreate,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    ticket = db.get(models.Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    try:
        ensure_ticket_visibility(ticket, current_user)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            raise
        raise

    comment = models.Comment(ticket_id=ticket_id, author_id=current_user.id, content=payload.content)
    ticket.updated_at = datetime.utcnow()
    if current_user.role == models.RoleEnum.support and ticket.first_response_at is None:
        ticket.first_response_at = datetime.utcnow()
    db.add(comment)
    db.commit()
    db.refresh(comment)
    db.refresh(ticket)
    return build_comment_payload(comment)


@app.get("/tickets/{ticket_id}/comments", response_model=List[schemas.CommentPublic])
def list_comments(
    ticket_id: int,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    ticket = db.get(models.Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    ensure_ticket_visibility(ticket, current_user)
    comments = db.query(models.Comment).filter(models.Comment.ticket_id == ticket_id).order_by(models.Comment.created_at).all()
    return [build_comment_payload(c) for c in comments]


@app.get("/internal/tickets/{ticket_id}", response_model=schemas.TicketDetailed)
def internal_get_ticket(
    ticket_id: int,
    _: bool = Depends(verify_internal_secret),
    db: Session = Depends(get_db),
):
    ticket = db.get(models.Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    return build_ticket_payload(ticket)


@app.get("/internal/users/{user_id}/tickets/summary")
def internal_user_summary(
    user_id: int,
    _: bool = Depends(verify_internal_secret),
    db: Session = Depends(get_db),
):
    total = db.query(models.Ticket).filter(models.Ticket.created_by_id == user_id).count()
    recent = (
        db.query(models.Ticket)
        .filter(models.Ticket.created_by_id == user_id)
        .order_by(models.Ticket.created_at.desc())
        .limit(2)
        .all()
    )
    return {
        "total": total,
        "recent_ids": [t.id for t in recent],
        "recent_titles": [t.title for t in recent],
    }


@app.post("/internal/tickets/{ticket_id}/agent-update", response_model=schemas.TicketDetailed)
def internal_agent_update(
    ticket_id: int,
    payload: schemas.AgentUpdate,
    _: bool = Depends(verify_internal_secret),
    db: Session = Depends(get_db),
):
    ticket = db.get(models.Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    updates = payload.model_dump(exclude_unset=True)
    if "priority" in updates and updates["priority"] is not None:
        ticket.priority = updates["priority"]
    if "category" in updates:
        ticket.category = updates["category"]
    if "assigned_unit" in updates:
        ticket.assigned_unit = updates["assigned_unit"]
    ticket.updated_at = datetime.utcnow()

    bot_user = db.query(models.User).filter(models.User.email == "agent@system.local").first()
    if payload.message and bot_user:
        comment = models.Comment(ticket_id=ticket_id, author_id=bot_user.id, content=payload.message)
        db.add(comment)
    db.commit()
    db.refresh(ticket)
    return build_ticket_payload(ticket)


@app.get("/departments/{department_id}/report", response_model=schemas.DepartmentReport)
def department_report(
    department_id: int,
    week_start: Optional[date] = Query(default=None, description="Week start date (YYYY-MM-DD)"),
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in {models.RoleEnum.department, models.RoleEnum.admin}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only department/admin can view reports")
    if current_user.role == models.RoleEnum.department and current_user.department_id != department_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your department")

    department = db.get(models.Department, department_id)
    if not department:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")

    start_date = week_start or (datetime.utcnow().date() - timedelta(days=7))
    week_end = start_date + timedelta(days=7)

    supports = (
        db.query(models.User)
        .filter(models.User.role == models.RoleEnum.support, models.User.department_id == department_id)
        .all()
    )

    report_items: List[schemas.SupportReport] = []
    for support in supports:
        tickets = (
            db.query(models.Ticket)
            .filter(models.Ticket.assigned_to_id == support.id)
            .all()
        )
        closed = [
            t
            for t in tickets
            if t.resolved_at
            and start_date <= t.resolved_at.date() < week_end
            and t.status in {models.TicketStatus.resolved, models.TicketStatus.closed}
        ]
        open_assigned = [
            t for t in tickets if t.status in {models.TicketStatus.open, models.TicketStatus.in_progress}
        ]

        response_times = []
        resolution_times = []
        for t in tickets:
            start_point = t.assigned_at or t.created_at
            if t.first_response_at and start_point:
                response_times.append((t.first_response_at - start_point).total_seconds() / 60)
            if t.resolved_at and start_point:
                resolution_times.append((t.resolved_at - start_point).total_seconds() / 60)

        report_items.append(
            schemas.SupportReport(
                support_user_id=support.id,
                support_email=support.email,
                closed_this_week=len(closed),
                open_assigned=len(open_assigned),
                average_response_minutes=sum(response_times) / len(response_times) if response_times else None,
                fastest_resolution_minutes=min(resolution_times) if resolution_times else None,
                slowest_resolution_minutes=max(resolution_times) if resolution_times else None,
            )
        )

    return schemas.DepartmentReport(department_id=department_id, week_start=start_date, week_end=week_end, supports=report_items)


app.mount("/frontend", StaticFiles(directory="frontend", html=True), name="frontend")


@app.get("/frontend")
def serve_frontend():
    return FileResponse("frontend/index.html")
