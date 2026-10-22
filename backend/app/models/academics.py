from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import ChangeRequestStatus, EnrollmentStatus, GradeRemark, Semester


class Program(Base):
    __tablename__ = "programs"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    department: Mapped[str | None] = mapped_column(String(100))
    total_units: Mapped[int | None] = mapped_column(Integer)


class Subject(Base):
    __tablename__ = "subjects"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    units: Mapped[Decimal] = mapped_column(Numeric(4, 1))
    program_id: Mapped[int | None] = mapped_column(ForeignKey("programs.id"))  # null = general ed


class Term(Base):
    __tablename__ = "terms"
    __table_args__ = (UniqueConstraint("academic_year", "semester"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    academic_year: Mapped[str] = mapped_column(String(9))  # e.g. 2025-2026
    semester: Mapped[Semester] = mapped_column(Enum(Semester, native_enum=False, length=10))
    is_grades_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)


class ClassSection(Base):
    __tablename__ = "class_sections"
    __table_args__ = (UniqueConstraint("subject_id", "term_id", "section_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"))
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id"))
    section_code: Mapped[str] = mapped_column(String(20))
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"))
    # Teacher "submit for lock" (Section 4); the registrar then locks the whole term.
    grades_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    subject: Mapped[Subject] = relationship()
    term: Mapped[Term] = relationship()


class SectionAdviser(Base):
    """Class adviser of a block section (change S-08): every class section of one term that
    shares a section code (e.g. all BSIT-1A subjects in 2nd Sem 2025-2026) is one advisory
    class. The adviser may view its rosters and grades; encoding stays with each subject's
    teacher."""

    __tablename__ = "section_advisers"
    __table_args__ = (UniqueConstraint("term_id", "section_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id"))
    section_code: Mapped[str] = mapped_column(String(20))
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"), index=True)
    assigned_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  server_default=func.now())

    term: Mapped[Term] = relationship()


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("student_id", "class_section_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    class_section_id: Mapped[int] = mapped_column(ForeignKey("class_sections.id"), index=True)
    status: Mapped[EnrollmentStatus] = mapped_column(
        Enum(EnrollmentStatus, native_enum=False, length=20), default=EnrollmentStatus.ENROLLED
    )

    class_section: Mapped[ClassSection] = relationship()
    grade: Mapped["Grade | None"] = relationship(back_populates="enrollment")


class Grade(Base):
    __tablename__ = "grades"

    id: Mapped[int] = mapped_column(primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(ForeignKey("enrollments.id"), unique=True)
    # 1.00-5.00; null for INC/DRP
    grade_value: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    remarks: Mapped[GradeRemark] = mapped_column(Enum(GradeRemark, native_enum=False, length=10))
    encoded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    enrollment: Mapped[Enrollment] = relationship(back_populates="grade")


class GradeChangeRequest(Base):
    __tablename__ = "grade_change_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    grade_id: Mapped[int] = mapped_column(ForeignKey("grades.id"), index=True)
    old_value: Mapped[str | None] = mapped_column(String(10))
    new_value: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str] = mapped_column(Text)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[ChangeRequestStatus] = mapped_column(
        Enum(ChangeRequestStatus, native_enum=False, length=10),
        default=ChangeRequestStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
