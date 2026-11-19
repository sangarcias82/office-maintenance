from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db import Base
from app.theme_tokens import css_variables, validate_theme_tokens


COMPANY_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
COMPANY_SLUG_MAX = 60


class Company(Base):
    """Tenant. The slug is the stable key (planned: session + fronts/<slug> lookup).

    Only generic tenant data lives here. Look and feel comes from front files
    (app/fronts/<slug>/templates) plus ``theme_tokens``: a generic map of design
    token -> hex colour (see app/theme_tokens.py). No brand-specific columns.
    """

    __tablename__ = "company"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(COMPANY_SLUG_MAX), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    theme_tokens: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    users: Mapped[list[AppUser]] = relationship(back_populates="company")

    @validates("theme_tokens")
    def _validate_theme_tokens(self, _key: str, value) -> Optional[dict]:
        clean = validate_theme_tokens(value)
        return clean or None

    def css_variables(self) -> list[tuple[str, str]]:
        """(token, hex) pairs to emit as CSS custom properties; re-validated on read."""
        return css_variables(self.theme_tokens)

    @validates("slug")
    def _validate_slug(self, _key: str, value: str) -> str:
        if self.slug is not None and value != self.slug:
            raise ValueError("Company slug is stable and cannot be changed.")
        if not isinstance(value, str) or len(value) > COMPANY_SLUG_MAX or not COMPANY_SLUG_RE.match(value):
            raise ValueError(
                "Company slug must be lowercase letters/digits separated by single hyphens."
            )
        return value

    @validates("display_name")
    def _validate_display_name(self, _key: str, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("Company display name is required.")
        return text


class Role(Base):
    __tablename__ = "role"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255))

    users: Mapped[list[AppUser]] = relationship(back_populates="role")


class Priority(Base):
    __tablename__ = "priority"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255))


class RequestStatus(Base):
    __tablename__ = "request_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255))


class MaintenanceType(Base):
    __tablename__ = "maintenance_type"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255))


class Account(Base):
    """A person: one personal email and one password, across companies.

    The same employee can work for several companies: one Account, one AppUser
    (membership) per company. Password hashes live in AccountCredential.
    """

    __tablename__ = "user_account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    memberships: Mapped[list[AppUser]] = relationship(back_populates="account")
    credential: Mapped[Optional[AccountCredential]] = relationship(
        back_populates="account", uselist=False, cascade="all, delete-orphan"
    )

    @validates("email")
    def _normalize_email(self, _key: str, value: str) -> str:
        return (value or "").strip().lower()


class AccountCredential(Base):
    """Password hashes stay off AppUser/Account so templates never see secrets."""

    __tablename__ = "account_credential"

    account_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    account: Mapped[Account] = relationship(back_populates="credential")


class AppUser(Base):
    """Membership of a person (Account) in exactly one company.

    Role, employee number, login email and active flag are per company. Login
    identifiers are unique per company, and a person has at most one membership
    per company. Requests and records reference AppUser, so data stays scoped.
    """

    __tablename__ = "app_user"
    __table_args__ = (
        UniqueConstraint("company_id", "email", name="uq_app_user_company_email"),
        UniqueConstraint("company_id", "employee_number", name="uq_app_user_company_employee_number"),
        UniqueConstraint("company_id", "account_id", name="uq_app_user_company_account"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id"), nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"), nullable=False, index=True)
    employee_number: Mapped[Optional[str]] = mapped_column(String(30))
    first_name: Mapped[str] = mapped_column(String(120), nullable=False)
    last_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(180), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(40))
    role_id: Mapped[int] = mapped_column(ForeignKey("role.id"), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    company: Mapped[Company] = relationship(back_populates="users")
    account: Mapped[Account] = relationship(back_populates="memberships")
    role: Mapped[Role] = relationship(back_populates="users")

    @validates("email")
    def _normalize_email(self, _key: str, value: str) -> str:
        return (value or "").strip().lower()

    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def role_code(self) -> str:
        return self.role.code if self.role else ""

    def is_employee(self) -> bool:
        return self.role_code == "EMPLOYEE"

    def is_technician(self) -> bool:
        return self.role_code == "TECHNICIAN"

    def is_area_manager(self) -> bool:
        return self.role_code == "AREA_MANAGER"

    def can_create_request(self) -> bool:
        return self.is_employee() or self.is_area_manager()

    def can_view_request_list(self) -> bool:
        return True

    def can_log_work(self) -> bool:
        return self.is_technician()

    def can_view_reports(self) -> bool:
        return self.is_area_manager()


class Location(Base):
    __tablename__ = "location"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_location_company_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    building: Mapped[Optional[str]] = mapped_column(String(80))
    floor: Mapped[Optional[str]] = mapped_column(String(40))
    area: Mapped[Optional[str]] = mapped_column(String(80))
    description: Mapped[Optional[str]] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ServiceRequest(Base):
    """A ticket. Owned by one company; its location, requester and technician
    must belong to the same company (enforced in services/requests.py).
    Maintenance records, materials and photos inherit the company via the request.
    """

    __tablename__ = "service_request"
    __table_args__ = (
        UniqueConstraint("company_id", "ticket_number", name="uq_service_request_company_ticket"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id"), nullable=False, index=True)
    ticket_number: Mapped[str] = mapped_column(String(30), nullable=False)
    reported_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id"), nullable=False)
    problem_description: Mapped[str] = mapped_column(Text, nullable=False)
    priority_id: Mapped[int] = mapped_column(ForeignKey("priority.id"), nullable=False)
    maintenance_type_id: Mapped[int] = mapped_column(ForeignKey("maintenance_type.id"), nullable=False)
    status_id: Mapped[int] = mapped_column(ForeignKey("request_status.id"), nullable=False)
    requester_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), nullable=False)
    assigned_technician_id: Mapped[Optional[int]] = mapped_column(ForeignKey("app_user.id"))
    assigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    company: Mapped[Company] = relationship()
    location: Mapped[Location] = relationship()
    priority: Mapped[Priority] = relationship()
    maintenance_type: Mapped[MaintenanceType] = relationship()
    status: Mapped[RequestStatus] = relationship()
    requester: Mapped[AppUser] = relationship(foreign_keys=[requester_id])
    assigned_technician: Mapped[Optional[AppUser]] = relationship(foreign_keys=[assigned_technician_id])
    records: Mapped[list[MaintenanceRecord]] = relationship(back_populates="request")


class MaintenanceRecord(Base):
    __tablename__ = "maintenance_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("service_request.id"), nullable=False)
    technician_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), nullable=False)
    attended_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    work_performed: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    service_status_id: Mapped[int] = mapped_column(ForeignKey("request_status.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    request: Mapped[ServiceRequest] = relationship(back_populates="records")
    technician: Mapped[AppUser] = relationship()
    service_status: Mapped[RequestStatus] = relationship()
    materials: Mapped[list[MaterialUsed]] = relationship(
        back_populates="record", cascade="all, delete-orphan"
    )
    photos: Mapped[list[PhotoEvidence]] = relationship(
        back_populates="record", cascade="all, delete-orphan"
    )


class MaterialUsed(Base):
    __tablename__ = "material_used"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("maintenance_record.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    unit: Mapped[str] = mapped_column(String(40), default="ea", nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String(255))

    record: Mapped[MaintenanceRecord] = relationship(back_populates="materials")


class PhotoEvidence(Base):
    __tablename__ = "photo_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("maintenance_record.id", ondelete="CASCADE"))
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Path relative to settings.upload_dir (e.g. "evidence/1/2/<uuid>-evidence.png").
    # Never a public URL: files are served by GET /evidence/{id} after an access check.
    relative_path: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    record: Mapped[MaintenanceRecord] = relationship(back_populates="photos")

    @validates("relative_path")
    def _validate_relative_path(self, _key: str, value: str) -> str:
        path = PurePosixPath(value or "")
        if not value or path.is_absolute() or "\\" in value or ".." in path.parts:
            raise ValueError("Evidence path must be relative to the upload folder.")
        return str(path)


class TicketCounter(Base):
    """Last ticket sequence handed out per company and year.

    Allocation increments this row with one UPDATE inside the request's
    transaction, so concurrent creates serialize on the row instead of both
    computing max+1 from the tickets table.
    """

    __tablename__ = "ticket_counter"

    company_id: Mapped[int] = mapped_column(ForeignKey("company.id"), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
