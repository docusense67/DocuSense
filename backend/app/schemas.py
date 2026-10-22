from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import Role


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    role: Role
    is_active: bool
    is_locked: bool
    can_validate: bool
    must_change_password: bool
    last_login_at: datetime | None


class UserCreateIn(BaseModel):
    email: EmailStr
    role: Role
    temporary_password: str
    can_validate: bool = False


class PasswordResetIn(BaseModel):
    temporary_password: str
    reason: str = Field(min_length=3)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3)
