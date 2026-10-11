"""Local users with roles, Argon2 password hashes, cookie sessions and CSRF protection.

- Session: random token in an HttpOnly, SameSite=Strict cookie; only its SHA-256 is stored.
- CSRF: double submit. The server issues a per-session token in a readable cookie; every
  non-GET request must echo it in the ``X-CSRF-Token`` header.
- Roles: viewer < engineer < admin.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from typing import Any, Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import AuditLog, User, UserSession, now
from .settings import get_settings

ROLES = {"viewer": 1, "engineer": 2, "admin": 3}
SESSION_COOKIE, CSRF_COOKIE, CSRF_HEADER = "ft_session", "ft_csrf", "x-csrf-token"
_ph = PasswordHasher()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, h: str) -> bool:
    try:
        return _ph.verify(h, pw)
    except (VerificationError, InvalidHashError):
        return False


def check_password_policy(pw: str) -> None:
    if len(pw) < 10:
        raise HTTPException(400, "Password must be at least 10 characters")


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def create_session(db: Session, user: User, response: Response) -> UserSession:
    s = get_settings()
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    sess = UserSession(token_hash=_sha(token), csrf=csrf, user_id=user.id,
                       expires_at=now() + dt.timedelta(hours=s.session_hours))
    db.add(sess)
    db.commit()
    max_age = s.session_hours * 3600
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict", secure=s.secure_cookies,
                        max_age=max_age, path="/")
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, samesite="strict", secure=s.secure_cookies,
                        max_age=max_age, path="/")
    return sess


def end_session(db: Session, request: Request, response: Response) -> None:
    tok = request.cookies.get(SESSION_COOKIE)
    if tok:
        sess = db.scalar(select(UserSession).where(UserSession.token_hash == _sha(tok)))
        if sess:
            db.delete(sess)
            db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    tok = request.cookies.get(SESSION_COOKIE)
    if not tok:
        raise HTTPException(401, "Not signed in")
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == _sha(tok)))
    if sess is None or sess.expires_at < now():
        raise HTTPException(401, "Session expired")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or not secrets.compare_digest(sent, sess.csrf):
            raise HTTPException(403, "CSRF token missing or invalid")
    user = db.get(User, sess.user_id)
    if user is None or not user.active:
        raise HTTPException(401, "User disabled")
    return user


def require(role: str):
    """Dependency factory: the signed-in user must have at least ``role``."""
    def dep(user: User = Depends(current_user)) -> User:
        if ROLES.get(user.role, 0) < ROLES[role]:
            raise HTTPException(403, f"Requires the {role} role")
        return user
    return dep


viewer = require("viewer")
engineer = require("engineer")
admin = require("admin")


def audit(db: Session, user: str, action: str, obj: str = "", before: Optional[Any] = None,
          after: Optional[Any] = None) -> None:
    """Append to the audit log (caller commits)."""
    db.add(AuditLog(user=user, action=action, object=obj, before=before, after=after))


def ensure_initial_admin(db: Session) -> Optional[str]:
    """First start: create ``admin`` with a random password written to the data folder."""
    if db.scalar(select(User).limit(1)) is not None:
        return None
    pw = secrets.token_urlsafe(12)
    db.add(User(username="admin", full_name="Administrator", role="admin", password_hash=hash_password(pw),
                must_change_password=True))
    audit(db, "system", "user.create", "user:admin", after={"role": "admin"})
    db.commit()
    s = get_settings()
    f = s.data_dir / "initial_admin_password.txt"
    f.write_text(f"FlowTwin first sign-in\nuser: admin\npassword: {pw}\n"
                 "You will be asked to change it. Delete this file afterwards.\n", encoding="utf-8")
    return pw
