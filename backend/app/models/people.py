from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import Role, StudentStatus


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False, length=20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Validation Operator is a permission on registrar staff, not a role (Section 4).
    can_validate: Mapped[bool] = mapped_column(Boolean, default=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    # Lockouts end on their own after lockout_minutes, so nobody can keep staff locked out.
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    student: Mapped["Student | None"] = relationship(back_populates="user")
    teacher: Mapped["Teacher | None"] = relationship(back_populates="user")


class UserSession(Base):
    """Server-side session; enables logout invalidation and idle timeout."""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(String(45))

    user: Mapped[User] = relationship()


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), unique=True)
    student_number: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(100))
    middle_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    suffix: Mapped[str | None] = mapped_column(String(20))
    birthdate: Mapped[date | None] = mapped_column(Date)
    sex: Mapped[str | None] = mapped_column(String(10))
    program_id: Mapped[int | None] = mapped_column(ForeignKey("programs.id"))
    year_level: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[StudentStatus] = mapped_column(
        Enum(StudentStatus, native_enum=False, length=20), default=StudentStatus.ACTIVE
    )
    admission_date: Mapped[date | None] = mapped_column(Date)
    contact_email: Mapped[str | None] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(30))
    # From enrollment documents (S-07): DepEd Learner Reference Number (12 digits, unique),
    # birth place and parents' names as on the PSA birth certificate.
    lrn: Mapped[str | None] = mapped_column(String(12), unique=True, index=True)
    birth_place: Mapped[str | None] = mapped_column(String(200))
    mother_name: Mapped[str | None] = mapped_column(String(200))
    father_name: Mapped[str | None] = mapped_column(String(200))

    user: Mapped[User | None] = relationship(back_populates="student")
    program: Mapped["Program | None"] = relationship()  # noqa: F821

    @property
    def login_active(self) -> bool | None:
        """None when the student has no login; False once it was deactivated."""
        return None if self.user is None else self.user.is_active


class Teacher(Base):
    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), unique=True)
    employee_number: Mapped[str] = mapped_column(String(30), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    department: Mapped[str | None] = mapped_column(String(100))

    user: Mapped[User | None] = relationship(back_populates="teacher")
