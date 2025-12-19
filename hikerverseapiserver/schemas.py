
from datetime import datetime
from pydantic import BaseModel, validator, EmailStr, field_validator


class SpacecraftBase(BaseModel):
    name: str

class SpacecraftCreate(SpacecraftBase):
    pass

class Spacecraft(SpacecraftBase):
    id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class SpacecraftConnectSchema(BaseModel):
    sc_ident: str

class CommanderBase(BaseModel):
    email: EmailStr
    full_name: str


class CommanderCreate(CommanderBase):
    password: str


class Commander(CommanderBase):
    id: str

    class Config:
        from_attributes = True

    @field_validator("id", mode="before")
    def convert_to_str(cls, v, values, **kwargs):
        return str(v) if v else v


class InstantCommandSchema(BaseModel):
    spacecraft_ident: str
    command: dict

class CommanderRegister(CommanderBase):
    password: str
    confirm_password: str
    spacecraft: list[Spacecraft] = []

    @field_validator("confirm_password", mode="before")
    def verify_password_match(cls, v, values, **kwargs):
        password = values.data.get("password")

        if v != password:
            raise ValueError("The two passwords did not match.")

        return v


class CommanderLogin(BaseModel):
    email: EmailStr
    password: str


class JwtTokenSchema(BaseModel):
    token: str
    payload: dict
    expire: datetime


class TokenPair(BaseModel):
    access: JwtTokenSchema
    refresh: JwtTokenSchema


class RefreshToken(BaseModel):
    refresh: str


class SuccessResponseScheme(BaseModel):
    status_code: int
    success: bool
    msg: str


class BlackListToken(BaseModel):
    id: str
    expire: datetime

    class Config:
        from_attributes = True


class MailBodySchema(BaseModel):
    token: str
    type: str


class EmailSchema(BaseModel):
    recipients: list[EmailStr]
    subject: str
    body: MailBodySchema


class MailTaskSchema(BaseModel):
    user: Commander
    body: MailBodySchema


class ForgotPasswordSchema(BaseModel):
    email: EmailStr


class PasswordResetSchema(BaseModel):
    password: str
    confirm_password: str

    @field_validator("confirm_password", mode="before")
    def verify_password_match(cls, v, values, **kwargs):
        password = values.get("password")

        if v != password:
            raise ValueError("The two passwords did not match.")

        return v


class PasswordUpdateSchema(PasswordResetSchema):
    old_password: str


class OldPasswordErrorSchema(BaseModel):
    old_password: bool

    @field_validator("old_password", mode="before")
    def check_old_password_status(cls, v, values, **kwargs):
        if not v:
            raise ValueError("Old password is not corret")


class CommandCreateSchema(BaseModel):
    tag: str
    content: str
    execute_at: datetime


class CommandListScheme(CommandCreateSchema):
    id: str
    commander_id: str

    class Config:
        from_attributes = True

class SpacecraftListScheme(CommandCreateSchema):
    id: str
    commander_id: str

    class Config:
        from_attributes = True
