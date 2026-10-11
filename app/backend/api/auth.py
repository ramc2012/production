"""Sign-in, sign-out, current user, password change."""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..security import (audit, check_password_policy, create_session, current_user, end_session, hash_password,
                        verify_password)

router = APIRouter(prefix="/auth", tags=["auth"])
_failures: "defaultdict[str, deque]" = defaultdict(deque)
MAX_FAILURES, WINDOW_S = 5, 300


class Login(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    old_password: str
    new_password: str


def user_out(u: User) -> dict:
    return dict(id=u.id, username=u.username, full_name=u.full_name, designation=u.designation, role=u.role,
                must_change_password=u.must_change_password, active=u.active)


@router.post("/login")
def login(body: Login, response: Response, db: Session = Depends(get_db)):
    key = body.username.strip().lower()
    q = _failures[key]
    while q and q[0] < time.time() - WINDOW_S:
        q.popleft()
    if len(q) >= MAX_FAILURES:
        raise HTTPException(429, "Too many failed sign-ins. Wait 5 minutes.")
    u = db.scalar(select(User).where(User.username == body.username.strip()))
    if u is None or not u.active or not verify_password(body.password, u.password_hash):
        q.append(time.time())
        audit(db, body.username.strip()[:80] or "?", "auth.login_failed")
        db.commit()
        raise HTTPException(401, "Wrong user name or password")
    q.clear()
    create_session(db, u, response)
    audit(db, u.username, "auth.login")
    db.commit()
    return user_out(u)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    end_session(db, request, response)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_out(user)


@router.post("/password")
def change_password(body: PasswordChange, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "Current password is wrong")
    check_password_policy(body.new_password)
    if body.new_password == body.old_password:
        raise HTTPException(400, "Choose a new password")
    u = db.get(User, user.id)
    u.password_hash = hash_password(body.new_password)
    u.must_change_password = False
    audit(db, u.username, "auth.password_change", f"user:{u.username}")
    db.commit()
    return user_out(u)
