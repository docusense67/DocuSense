from sqlalchemy import func, select

from app.models import Grade, Student, Subject, User
from app.models.enums import GradeRemark
from app.seed import seed


def test_seed_creates_demo_dataset_once(db):
    assert seed(db) is True
    assert db.scalar(select(func.count()).select_from(Subject)) == 8
    assert db.scalar(select(func.count()).select_from(Student)) == 3
    assert db.scalar(select(func.count()).select_from(Grade)) == 24
    assert db.scalar(select(func.count()).select_from(Grade).where(
        Grade.remarks == GradeRemark.INC)) == 1
    assert all(u.must_change_password for u in db.scalars(select(User)))

    assert seed(db) is False  # idempotent
