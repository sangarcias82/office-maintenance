from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


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


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_number: Mapped[Optional[str]] = mapped_column(String(30), unique=True)
    first_name: Mapped[str] = mapped_column(String(120), nullable=False)
    last_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(40))
    role_id: Mapped[int] = mapped_column(ForeignKey("role.id"), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    role: Mapped[Role] = relationship(back_populates="users")
    credential: Mapped[Optional[UserCredential]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

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

    def access_summary(self) -> str:
        if self.is_employee():
            return "Create requests · Own tickets"
        if self.is_technician():
            return "Assigned tickets · Log work"
        if self.is_area_manager():
            return "All tickets · Assign · Reports"
        return self.role_code


class UserCredential(Base):
    """Password hashes stay off AppUser so templates never see secrets."""

    __tablename__ = "user_credential"

    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped[AppUser] = relationship(back_populates="credential")


class Location(Base):
    __tablename__ = "location"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    building: Mapped[Optional[str]] = mapped_column(String(80))
    floor: Mapped[Optional[str]] = mapped_column(String(40))
    area: Mapped[Optional[str]] = mapped_column(String(80))
    description: Mapped[Optional[str]] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ServiceRequest(Base):
    __tablename__ = "service_request"
    __table_args__ = (UniqueConstraint("ticket_number", name="uq_service_request_ticket"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
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
    path_or_url: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    record: Mapped[MaintenanceRecord] = relationship(back_populates="photos")
