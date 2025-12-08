from datetime import datetime, date
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field

from .models import RoleEnum, TicketPriority, TicketStatus


class Token(BaseModel):
    access_token: str
    token_type: str


class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None
    role: RoleEnum = RoleEnum.student
    department_id: Optional[int] = Field(default=None, description="Required for support/department roles")


class UserCreate(UserBase):
    password: str = Field(min_length=6)


class UserPublic(BaseModel):
    id: int
    email: EmailStr
    full_name: Optional[str]
    role: RoleEnum
    department_id: Optional[int]

    model_config = {"from_attributes": True}


class DepartmentPublic(BaseModel):
    id: int
    name: str
    description: Optional[str]

    model_config = {"from_attributes": True}


class CommentCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class CommentPublic(BaseModel):
    id: int
    content: str
    author_id: int
    author_email: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TicketBase(BaseModel):
    title: str
    description: str
    department_id: int
    priority: TicketPriority = TicketPriority.medium
    category: Optional[str] = None
    assigned_unit: Optional[str] = None


class TicketCreate(TicketBase):
    pass


class TicketUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[TicketPriority] = None
    category: Optional[str] = None
    assigned_unit: Optional[str] = None


class TicketStatusUpdate(BaseModel):
    status: TicketStatus


class TicketAssign(BaseModel):
    support_user_id: int


class TicketPublic(BaseModel):
    id: int
    title: str
    description: str
    priority: TicketPriority
    status: TicketStatus
    category: Optional[str]
    assigned_unit: Optional[str]
    department_id: int
    assigned_to_id: Optional[int]
    created_by_id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TicketDetailed(TicketPublic):
    department: Optional[DepartmentPublic] = None
    assignee_email: Optional[str] = None
    creator_email: Optional[str] = None
    comments: List[CommentPublic] = Field(default_factory=list)
    first_response_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None


class AISuggestRequest(BaseModel):
    description: str = Field(min_length=4)


class AISuggestResponse(BaseModel):
    suggested_category: str
    suggested_priority: TicketPriority


class AIInsightResponse(BaseModel):
    summary: str
    draft_reply: str


class AgentUpdate(BaseModel):
    priority: Optional[TicketPriority] = None
    category: Optional[str] = None
    assigned_unit: Optional[str] = None
    message: Optional[str] = None


class ReportRequest(BaseModel):
    week_start: Optional[date] = None


class SupportReport(BaseModel):
    support_user_id: int
    support_email: str
    closed_this_week: int
    open_assigned: int
    average_response_minutes: Optional[float]
    fastest_resolution_minutes: Optional[float]
    slowest_resolution_minutes: Optional[float]


class DepartmentReport(BaseModel):
    department_id: int
    week_start: date
    week_end: date
    supports: List[SupportReport]
