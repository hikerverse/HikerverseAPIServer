import json
import os
import uuid
from typing import Annotated
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Response, Cookie
from fastapi.exceptions import RequestValidationError
from fastapi.security import OAuth2PasswordBearer

from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.requests import Request
from starlette.responses import FileResponse

from hikerservespacecraft.spacecraft import Spacecraft
from hikerservespacecraft.spacecraft_constructor import get_initial_spacecraft
from hikerservespacecraft.utils.ser import serialize, deserialize
from hikerverseapiserver import schemas
from hikerverseapiserver.dependencies import get_db

from hikerverseapiserver.core.config import REFRESH_TOKEN_ROTATION
from hikerverseapiserver.core.hash import get_password_hash, verify_password
from hikerverseapiserver.core.jwt import (
    create_token_pair,
    refresh_token_state_with_rotation,
    refresh_token_state_without_rotation,
    decode_token_with_blacklisted,
    mail_token,
    add_refresh_token_cookie,
    SUB,
    JTI,
    # EXP,
)
from hikerverseapiserver.exceptions import BadRequestException, NotFoundException
from hikerverseapiserver.schemas import Spacecraft as SpacecraftSchema
from hikerverseapiserver.tasks import (
    user_mail_event,
)
from hikerversedb import models
from app import app

router = APIRouter()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


@router.post("/register", response_model=schemas.Commander)
def register(
        data: schemas.CommanderRegister,
        bg_task: BackgroundTasks,
        db: Session = Depends(get_db),
):
    user = models.Commander.find_by_email(db=db, email=data.email)
    if user:
        raise HTTPException(status_code=400, detail="Email has already registered")

    # hashing password
    user_data = data.dict(exclude={"confirm_password"})
    user_data["password"] = get_password_hash(user_data["password"])

    user_data["id"] = str(uuid.uuid4())

    # save user to db
    user = models.Commander(**user_data)
    user.is_active = False

    default_initial_spacecraft_dict = serialize(get_initial_spacecraft())
    default_initial_spacecraft_json = json.dumps(default_initial_spacecraft_dict)

    default_initial_spacecraft = models.Spacecraft(commander_id=user.id,
                                                   spacecraft_metadata=default_initial_spacecraft_json)
    default_initial_spacecraft.id = str(uuid.uuid4())
    user.spacecraft.append(default_initial_spacecraft)

    user.save(db=db)

    # send verify email
    user_schema = schemas.Commander.from_orm(user)
    verify_token = mail_token(user_schema)

    mail_task_data = schemas.MailTaskSchema(
        user=user_schema, body=schemas.MailBodySchema(type="verify", token=verify_token)
    )
    bg_task.add_task(user_mail_event, mail_task_data)

    return user_schema


@router.post("/login")
def login(
        data: schemas.CommanderLogin,
        response: Response,
        db: Session = Depends(get_db),
        request: Request = None,
):
    commander = models.Commander.authenticate(
        db=db, email=data.email, password=data.password
    )

    if not commander:
        return {"status_code": -1, "success": False, "msg": "Incorrect user and/or password", "token": None}

    if not commander.is_active:
        return {"status_code": -2, "success": False,
                "msg": "Commander not authorized, verify via email link", "token": None}

    commander.is_logged_in = True
    commander.save(db=db)

    commander = schemas.Commander.from_orm(commander)

    token_pair = create_token_pair(user=commander)
    add_refresh_token_cookie(response=response, token=token_pair.refresh.token)

    return {"status_code": 0, "success": True, "msg": "Login successful",
            "token": token_pair.access.token, "commander_ident": commander.id}


@router.post("/refresh")
def refresh(
        response: Response,
        refresh: Annotated[str | None, Cookie()] = None,
        db: Session = Depends(get_db),
):
    if not refresh:
        raise BadRequestException(detail="refresh token required")

    # Without rotation refresh token does not renew
    if not REFRESH_TOKEN_ROTATION:
        return refresh_token_state_without_rotation(token=refresh)

    payload = decode_token_with_blacklisted(token=refresh, db=db)
    user = models.Commander.find_by_id(db=db, id=payload[SUB])

    return refresh_token_state_with_rotation(
        response=response,
        payload=payload,
        user=user,
        db=db,
    )


@router.get("/verify", response_model=schemas.SuccessResponseScheme)
def verify(token: str, db: Session = Depends(get_db)):
    payload = decode_token_with_blacklisted(token=token, db=db)
    user = models.Commander.find_by_id(db=db, id=payload[SUB])
    if not user:
        raise NotFoundException(detail="User not found")

    user.is_active = True
    user.save(db=db)
    return {"msg": "Successfully activated"}


@router.post("/logout", response_model=schemas.SuccessResponseScheme)
def logout(
        token: Annotated[str, Depends(oauth2_scheme)],
        db: Session = Depends(get_db),
):
    payload = decode_token_with_blacklisted(token=token, db=db)

    black_listed = models.BlackListToken(
        id=payload[JTI], expire=datetime.fromtimestamp(payload['exp'], tz=timezone.utc)
    )
    black_listed.save(db=db)

    commander = models.Commander.find_by_id(db=db, id=payload[SUB])
    commander.is_logged_in = False
    commander.save(db=db)

    for sc in commander.spacecraft:
        sc.is_active = False
        sc.save(db=db)
        print(f"Removing active spacecraft {sc.id}")

        app.state.global_data["universe"]["spacecraft"].pop(sc.id, None)

    return {"status_code": 0, "success": True, "msg": "Logout successful"}


@router.post("/forgot-password", response_model=schemas.SuccessResponseScheme)
def forgot_password(
        data: schemas.ForgotPasswordSchema,
        bg_task: BackgroundTasks,
        db: Session = Depends(get_db),
):
    user = models.Commander.find_by_email(db=db, email=data.email)
    if user:
        user_schema = schemas.Commander.from_orm(user)
        reset_token = mail_token(user_schema)

        mail_task_data = schemas.MailTaskSchema(
            user=user_schema,
            body=schemas.MailBodySchema(type="password-reset", token=reset_token),
        )
        bg_task.add_task(user_mail_event, mail_task_data)

    return {"msg": "Reset token sended successfully your email check your email"}


@router.post("/password-reset", response_model=schemas.SuccessResponseScheme)
def password_reset_token(
        token: str,
        data: schemas.PasswordResetSchema,
        db: Session = Depends(get_db),
):
    payload = decode_token_with_blacklisted(token=token, db=db)
    user = models.Commander.find_by_id(db=db, id=payload[SUB])
    if not user:
        raise NotFoundException(detail="User not found")

    user.password = get_password_hash(data.password)
    user.save(db=db)

    return {"msg": "Password succesfully updated"}


@router.post("/password-update", response_model=schemas.SuccessResponseScheme)
def password_update(
        token: Annotated[str, Depends(oauth2_scheme)],
        data: schemas.PasswordUpdateSchema,
        db: Session = Depends(get_db),
):
    payload = decode_token_with_blacklisted(token=token, db=db)
    user = models.Commander.find_by_id(db=db, id=payload[SUB])
    if not user:
        raise NotFoundException(detail="User not found")

    # raise Validation error
    if not verify_password(data.old_password, user.password):
        try:
            schemas.OldPasswordErrorSchema(old_password=False)
        except ValidationError as e:
            raise RequestValidationError(e.raw_errors)
    user.password = get_password_hash(data.password)
    user.save(db=db)

    return {"msg": "Successfully updated"}


@router.post("/instant_command")
def instant_command(
        token: Annotated[str, Depends(oauth2_scheme)],
        data: schemas.InstantCommandSchema,
        db: Session = Depends(get_db),
):
    payload = decode_token_with_blacklisted(token=token, db=db)
    commander = models.Commander.find_by_id(db=db, id=payload[SUB])
    if not commander:
        return {"status_code": -1, "success": False, "msg": "Incorrect user and/or password", "token": None}

    spacecraft_: Spacecraft = app.state.global_data["universe"]["spacecraft"].get(data.spacecraft_ident, None)
    if not spacecraft_:
        return {"status_code": -2, "success": False, "msg": "Spacecraft not connected", "token": None}
    else:
        cmd_result_ = spacecraft_.spacecraft_computer.route_command(cmd=data.command)

        spacecraft__dict = serialize(spacecraft_)
        spacecraft_json = json.dumps(spacecraft__dict)
        sc_model = models.Spacecraft.find_by_id(db=db, id=data.spacecraft_ident)
        if sc_model:
            sc_model.spacecraft_metadata = spacecraft_json
            sc_model.save(db=db)

        return cmd_result_


@router.get("/commands")
def commands(
        token: Annotated[str, Depends(oauth2_scheme)],
        db: Session = Depends(get_db),
):
    payload = decode_token_with_blacklisted(token=token, db=db)
    commander = models.Commander.find_by_id(db=db, id=payload[SUB])
    if not commander:
        return {"status_code": -1, "success": False, "msg": "Incorrect user and/or password", "token": None}

    commands_ = models.Command.find_by_commander(db=db, commander=commander)

    return [schemas.CommandListScheme.from_orm(command) for command in commands_]


@router.get("/spacecraft")
def spacecraft(
        token: Annotated[str, Depends(oauth2_scheme)],
        db: Session = Depends(get_db),
):
    payload = decode_token_with_blacklisted(token=token, db=db)
    commander = models.Commander.find_by_id(db=db, id=payload[SUB])
    if not commander:
        return {"status_code": -1, "success": False, "msg": "Incorrect user and/or password", "token": None}

    spacecraft_ = models.Spacecraft.find_by_commander(db=db, commander_id=commander.id)

    return [{"name": spacecraft.spacecraft_name, "id": spacecraft.id} for spacecraft in spacecraft_]



@router.post("/commands", response_model=schemas.SuccessResponseScheme, status_code=201)
def command_create(
        token: Annotated[str, Depends(oauth2_scheme)],
        data: schemas.CommandCreateSchema,
        db: Session = Depends(get_db),
):
    payload = decode_token_with_blacklisted(token=token, db=db)
    commander = models.Commander.find_by_id(db=db, id=payload[SUB])
    if not commander:
        raise NotFoundException(detail="Commander not found")

    command = models.Command(**data.dict())
    command.id = str(uuid.uuid4())
    command.commander = commander

    command.save(db=db)

    return {"msg": "Command succesfully created"}



@router.post("/spacecraft_connect", response_model=schemas.SuccessResponseScheme, status_code=201)
def spacecraft_connect(
        token: Annotated[str, Depends(oauth2_scheme)],
        data: schemas.SpacecraftConnectSchema,
        db: Session = Depends(get_db),
):
    payload = decode_token_with_blacklisted(token=token, db=db)
    commander = models.Commander.find_by_id(db=db, id=payload[SUB])
    if not commander:
        return {"status_code": -100, "success": False, "msg": "Incorrect/unknown commander ident"}

    # can we cache some data to prevent having to do a DB lookup here?
    sc = models.Spacecraft.find_by_ident(db=db, sc_ident=data.sc_ident, commander_id=commander.id)
    if not sc:
        return {"status_code": -101, "success": False, "msg": "Incorrect/unknown spacecraft ident"}


    # see if we have it in global data already
    spacecraft_: Spacecraft = app.state.global_data["universe"]["spacecraft"].get(sc.id, None)
    if spacecraft_:
        print(f"Found spacecraft in global data: {sc.id} -> {spacecraft_ is not None}")
    else:
        sc.is_active = True
        sc.save(db=db)
        print(f"Adding active spacecraft {sc.id}")
        spacecraft_: Spacecraft = deserialize(sc.spacecraft_metadata) if sc else None
        app.state.global_data["universe"]["spacecraft"][sc.id] = spacecraft_

    return {"status_code": 100, "success": True, "msg": "Spacecraft succesfully connected"}


@router.post("/spacecraft_disconnect", response_model=schemas.SuccessResponseScheme, status_code=201)
def spacecraft_disconnect(
        token: Annotated[str, Depends(oauth2_scheme)],
        data: schemas.SpacecraftDisconnectSchema,
        db: Session = Depends(get_db),
):
    payload = decode_token_with_blacklisted(token=token, db=db)
    commander = models.Commander.find_by_id(db=db, id=payload[SUB])
    if not commander:
        return {"status_code": -100, "success": False, "msg": "Incorrect/unknown commander ident"}

    sc = models.Spacecraft.find_by_ident(db=db, sc_ident=data.sc_ident, commander_id=commander.id)
    sc.is_active = False
    sc.save(db=db)
    print(f"Removing active spacecraft {sc.id}")
    app.state.global_data["universe"]["spacecraft"].pop(sc.id, None)

    if not sc:
        return {"status_code": -101, "success": False, "msg": "Incorrect/unknown spacecraft ident"}

    return {"status_code": 100, "success": True, "msg": "Spacecraft succesfully disconnected"}


@app.post("/console-execute")
async def console_page(request: Request):
    data = await request.json()
    code = data.get("code", "")
    session_id = data.get("session_id", None)

    print(f"Console execute request: session_id={session_id}, code={code!r}")
    # do nothgin
    return {"output": code, "error": "", "session_id": session_id}
    #exec_request = ExecRequest(code=code, session_id=session_id)
    #esult = await execute(exec_request)
    #return result

@app.get("/console")
async def console_page():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "console.html"))

