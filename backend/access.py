"""Who may see and do what (design document, Section 17.6).

Accounts, passwords, sessions and the one place where access is decided.

    UNIVERSITY_ADMIN   everything, in every school, and the accounts themselves
    HR_ADMIN           everything, in every school
    SCHOOL_HR          the schools (or single departments) it is granted
    DEPARTMENT_HR      the departments it is granted
    INTERVIEWER        its own view only (built separately); none of the HR pages

A grant (`user_school_access`) names a school, optionally one department of
it, and a level: VIEW (read), VIEW_EDIT (also correct the record, add resumes,
run the reading and the assessment) or APPROVE (also decide at Gate 2 and
approve emails at Gate 3). A grant on a school with no department named
covers the whole school.

Every request for an HR page passes through `authorise`, which works out from
the address what is being asked for and refuses what the account's grants do
not cover. What lies outside an account's schools is reported as not found,
so its existence is not given away.

Passwords are stored only as scrypt hashes. A session is a random token in an
HttpOnly cookie; the database holds only the token's hash.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, false, or_, select, true
from sqlalchemy.orm import Session

from backend.models import Application, Department, JobOpening, PasswordReset, School, User, UserSchoolAccess, UserSession

USER_TYPES: dict[str, str] = {
    "UNIVERSITY_ADMIN": "University administrator", "HR_ADMIN": "HR administrator", "SCHOOL_HR": "School HR",
    "DEPARTMENT_HR": "Department HR", "INTERVIEWER": "Interviewer",
}
ADMIN_TYPES = ("UNIVERSITY_ADMIN", "HR_ADMIN")
VIEW, VIEW_EDIT, APPROVE = "VIEW", "VIEW_EDIT", "APPROVE"
LEVELS: dict[str, str] = {VIEW: "View only", VIEW_EDIT: "View and edit", APPROVE: "View, edit and approve"}
_RANK = {VIEW: 1, VIEW_EDIT: 2, APPROVE: 3}

COOKIE = "recruitai_session"
SESSION_IDLE = timedelta(hours=8)
MIN_PASSWORD = 10
MAX_FAILED_LOGINS = 5
LOCKED_FOR = timedelta(minutes=15)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccessError(Exception):
    """An account cannot be created or changed as asked. The message is for the person."""


class NotSignedIn(Exception):
    """No valid session: the person is sent to the sign-in page."""


class NotSetUp(Exception):
    """No account exists yet: the person is sent to create the first administrator."""


class GoTo(Exception):
    """This account's own pages are elsewhere: the person is sent there."""

    def __init__(self, location: str) -> None:
        super().__init__(location)
        self.location = location


class MustChangePassword(Exception):
    """Signed in with a password someone else set: it has to be changed before anything else."""


class Forbidden(Exception):
    """Signed in, in scope, but the account's level does not allow this."""


class OutOfScope(Exception):
    """Outside the account's schools. Answered as not found."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)  # SQLite returns naive datetimes


# --- passwords ---------------------------------------------------------------

_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        got = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


# Checked against when the address is unknown, so a wrong address takes as long as a wrong password.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def check_new_password(password: str, email: str = "") -> None:
    if len(password) < MIN_PASSWORD:
        raise AccessError(f"Choose a password of at least {MIN_PASSWORD} characters.")
    if len(password) > 200:
        raise AccessError("Choose a password of at most 200 characters.")
    if email and password.lower() == email.lower():
        raise AccessError("The password must not be the email address.")


# --- who is asking -----------------------------------------------------------


@dataclass(frozen=True)
class Principal:
    """The signed-in person, as the pages need them: who they are and what they are granted."""

    user_id: int | None
    name: str
    user_type: str
    grants: tuple[tuple[str, int | None, str], ...] = ()  # (school_id, department_id or None, level)
    # How this person is written into the audit trail. An id, not a name: the trail is read by many.
    actor: str = ""

    @property
    def is_admin(self) -> bool:
        return self.user_type in ADMIN_TYPES

    @property
    def manages_accounts(self) -> bool:
        return self.user_type == "UNIVERSITY_ADMIN"

    def level(self, school_id: str | None, department_id: int | None) -> int:
        """The highest level this person holds over a school's (or one department's) applications; 0 for none."""
        if self.user_type == "INTERVIEWER":
            return 0
        if self.is_admin:
            return _RANK[APPROVE]
        return max((_RANK[lv] for s, d, lv in self.grants if s == school_id and (d is None or d == department_id)), default=0)

    def may(self, needed: str, school_id: str | None, department_id: int | None) -> bool:
        return self.level(school_id, department_id) >= _RANK[needed]

    def sees_interview(self, school_id: str | None, department_id: int | None) -> bool:
        """Whether this person may see the interview panel's view of a school's (or department's) short-listed candidates."""
        if self.user_type == "INTERVIEWER":
            return any(s == school_id and (d is None or d == department_id) for s, d, _ in self.grants)
        return self.level(school_id, department_id) > 0

    def may_anywhere(self, needed: str) -> bool:
        return self.is_admin or (self.user_type != "INTERVIEWER" and any(_RANK[lv] >= _RANK[needed] for _, _, lv in self.grants))

    def schools_for_openings(self) -> set[str] | None:
        """Schools this person may create an opening in (a grant on the whole school, to edit); None means all."""
        if self.is_admin:
            return None
        return {s for s, d, lv in self.grants if d is None and _RANK[lv] >= _RANK[VIEW_EDIT]} if self.user_type != "INTERVIEWER" else set()


def principal_of(user: User) -> Principal:
    return Principal(user_id=user.user_id, name=user.name, user_type=user.user_type, actor=f"user:{user.user_id}",
                     grants=tuple((g.school_id, g.department_id, g.access_level) for g in user.grants))


def opening_filter(principal: Principal, interview: bool = False):
    """The condition that keeps a query on `job_openings` to what this person is granted.

    `interview` is for the panel's view, the only one an interviewer account has.
    """
    if principal.is_admin:
        return true()
    if (principal.user_type == "INTERVIEWER" and not interview) or not principal.grants:
        return false()
    return or_(*[
        JobOpening.school_id == s if d is None else and_(JobOpening.school_id == s, JobOpening.department_id == d)
        for s, d, _ in principal.grants
    ])


def scope_of(target: JobOpening | Application) -> tuple[str | None, int | None]:
    """(school, department) an opening or an application belongs to. An application follows its opening."""
    if isinstance(target, Application) and target.opening is not None:
        target = target.opening
    return target.school_id, target.department_id


def names(session: Session) -> dict[str, str]:
    """Audit-trail actors as people's names, for a page. Actions from before there were accounts read "HR"."""
    found = {f"user:{uid}": name for uid, name in session.execute(select(User.user_id, User.name))}
    return {"user:hr": "HR", "system:acknowledgement": "The system", **found}


# --- the one place access is decided -------------------------------------------

# What each address needs, where it is not the default (reading needs VIEW, changing needs VIEW_EDIT).
_APPROVE_ROUTES = frozenset({
    "/hr/applications/{application_id}/approve", "/hr/applications/{application_id}/override",
    "/hr/applications/{application_id}/return", "/hr/applications/{application_id}/email",
    "/hr/applications/{application_id}/reopen-decision", "/hr/openings/{opening_id}/emails",
})
_ACCOUNT_ROUTES_PREFIX = "/admin/"
_ADMIN_PREFIXES = ("/hr/inbox", "/hr/jobs/", "/hr/policy", "/hr/dashboard")
_INTERVIEW_PREFIX = "/interview"
# The JSON API that creates and reads applications directly: administrators only.
_ADMIN_ROUTES = frozenset({"/applications", "/applications/{application_id}/read"})
_OWN_ACCOUNT_PREFIX = "/account/"


def authorise(session: Session, principal: Principal, method: str, route: str, params: dict) -> None:
    """Refuse a request this person's account does not cover. Raises Forbidden or OutOfScope; returns on success."""
    if route.startswith(_OWN_ACCOUNT_PREFIX):
        return
    if route.startswith(_ACCOUNT_ROUTES_PREFIX):
        if not principal.manages_accounts:
            raise Forbidden("Only a university administrator manages accounts.")
        return
    if route.startswith(_INTERVIEW_PREFIX):
        # Read-only pages. Which candidates appear, and which parts of their record, is settled by the page itself.
        if "application_id" in params:
            target = session.get(Application, _as_int(params["application_id"]))
            if target is not None and not principal.sees_interview(*scope_of(target)):
                raise OutOfScope()
        return
    if principal.user_type == "INTERVIEWER":
        if route == "/" and method == "GET":
            raise GoTo("/interview")
        raise Forbidden("This is an interviewer account. It sees the short-listed candidates of its school, and none of the HR pages.")
    if route.startswith(_ADMIN_PREFIXES) or route in _ADMIN_ROUTES:
        if not principal.is_admin:
            raise Forbidden("This page is for HR administrators: it is not tied to one school.")
        return

    changing = method not in ("GET", "HEAD")
    needed = APPROVE if changing and route in _APPROVE_ROUTES else (VIEW_EDIT if changing else VIEW)
    target = None
    if "application_id" in params:
        target = session.get(Application, _as_int(params["application_id"]))
    elif "opening_id" in params:
        target = session.get(JobOpening, _as_int(params["opening_id"]))
    if target is None:
        # A page about no one opening (the home page, a new opening, the queue), or an id that does not exist:
        # the page itself answers the second. The first needs the level somewhere.
        if changing and not principal.may_anywhere(needed):
            raise Forbidden("Your account can view but not change anything.")
        return
    school_id, department_id = scope_of(target)
    if principal.level(school_id, department_id) == 0:
        raise OutOfScope()
    if not principal.may(needed, school_id, department_id):
        raise Forbidden("Your account may " + ("view and edit, but not approve, here." if needed == APPROVE else "view, but not change, this."))


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


# --- sessions ----------------------------------------------------------------


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def sign_in(session: Session, email: str, password: str, now: datetime | None = None) -> str:
    """Check an address and password; returns the session token for the cookie. Raises AccessError in words.

    The same words whichever of the two was wrong, and after five wrong
    passwords the account is closed to sign-in for a quarter of an hour.
    """
    now = now or _now()
    wrong = AccessError("The email address or the password is not right.")
    user = session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        verify_password(password, _DUMMY_HASH)
        raise wrong
    if user.locked_until is not None and _aware(user.locked_until) > now:
        raise AccessError("Too many wrong passwords. Try again in fifteen minutes.")
    if not verify_password(password, user.password_hash) or not user.is_active:
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.failed_logins, user.locked_until = 0, now + LOCKED_FOR
        session.flush()
        raise wrong
    user.failed_logins, user.locked_until, user.last_login_at = 0, None, now
    token = secrets.token_urlsafe(32)
    session.add(UserSession(token_hash=_token_hash(token), user_id=user.user_id, created_at=now, last_seen_at=now,
                            expires_at=now + SESSION_IDLE))
    session.flush()
    return token


def user_for_token(session: Session, token: str | None, now: datetime | None = None) -> User | None:
    """The account a cookie's token belongs to, if the session is still good. Using it keeps it alive."""
    if not token:
        return None
    now = now or _now()
    row = session.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token)))
    if row is None:
        return None
    user = session.get(User, row.user_id)
    if _aware(row.expires_at) <= now or user is None or not user.is_active:
        session.delete(row)
        session.flush()
        return None
    if now - _aware(row.last_seen_at) > timedelta(minutes=5):  # not a write on every page
        row.last_seen_at, row.expires_at = now, now + SESSION_IDLE
    return user


def sign_out(session: Session, token: str | None) -> None:
    if token:
        row = session.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token)))
        if row is not None:
            session.delete(row)
            session.flush()


def _end_sessions(session: Session, user: User) -> None:
    for row in session.scalars(select(UserSession).where(UserSession.user_id == user.user_id)):
        session.delete(row)


# --- accounts ----------------------------------------------------------------


def any_account(session: Session) -> bool:
    return session.scalar(select(User.user_id).limit(1)) is not None


def create_user(session: Session, *, name: str, email: str, password: str, user_type: str,
                must_change_password: bool = True) -> User:
    name, email = " ".join(name.split()), email.strip().lower()
    if len(name) < 2 or len(name) > 200:
        raise AccessError("Give the person's name.")
    if not _EMAIL_RE.match(email) or len(email) > 254:
        raise AccessError("Enter a valid email address.")
    if user_type not in USER_TYPES:
        raise AccessError("Choose the kind of account.")
    check_new_password(password, email)
    if session.scalar(select(User.user_id).where(User.email == email)) is not None:
        raise AccessError("There is already an account with this email address.")
    user = User(name=name, email=email, password_hash=hash_password(password), user_type=user_type,
                must_change_password=must_change_password)
    session.add(user)
    session.flush()
    return user


def create_first_administrator(session: Session, *, name: str, email: str, password: str) -> User:
    """The very first account, made by whoever sets the system up. Refused once any account exists."""
    if any_account(session):
        raise AccessError("The system is already set up. Sign in instead.")
    return create_user(session, name=name, email=email, password=password, user_type="UNIVERSITY_ADMIN", must_change_password=False)


def change_own_password(session: Session, user: User, current: str, new: str, keep_token: str | None = None) -> None:
    if not verify_password(current, user.password_hash):
        raise AccessError("The current password is not right.")
    if new == current:
        raise AccessError("Choose a password different from the current one.")
    check_new_password(new, user.email)
    user.password_hash, user.must_change_password = hash_password(new), False
    # Every other place this account is signed in is signed out.
    keep = _token_hash(keep_token) if keep_token else None
    for row in session.scalars(select(UserSession).where(UserSession.user_id == user.user_id)):
        if row.token_hash != keep:
            session.delete(row)
    session.flush()


def reset_password(session: Session, user: User, new: str) -> None:
    """An administrator sets a temporary password; the person must choose their own at the next sign-in."""
    check_new_password(new, user.email)
    user.password_hash, user.must_change_password = hash_password(new), True
    user.failed_logins, user.locked_until = 0, None
    _end_sessions(session, user)
    session.flush()


def _other_active_account_managers(session: Session, user: User) -> int:
    return len(session.scalars(select(User.user_id).where(
        User.user_type == "UNIVERSITY_ADMIN", User.is_active, User.user_id != user.user_id)).all())


def set_active(session: Session, user: User, active: bool) -> None:
    if not active and user.user_type == "UNIVERSITY_ADMIN" and _other_active_account_managers(session, user) == 0:
        raise AccessError("This is the only university administrator. Make another before closing this account.")
    user.is_active = active
    if not active:
        _end_sessions(session, user)
    session.flush()


def set_user_type(session: Session, user: User, user_type: str) -> None:
    if user_type not in USER_TYPES:
        raise AccessError("Choose the kind of account.")
    if user.user_type == "UNIVERSITY_ADMIN" and user_type != "UNIVERSITY_ADMIN" and _other_active_account_managers(session, user) == 0:
        raise AccessError("This is the only university administrator. Make another before changing this account.")
    user.user_type = user_type
    session.flush()


def grant(session: Session, user: User, school_id: str, department_id: int | None, level: str) -> UserSchoolAccess:
    """Give an account a level over a school, or over one department of it. A second grant on the same scope replaces the first."""
    if level not in LEVELS:
        raise AccessError("Choose a level.")
    if session.get(School, school_id) is None:
        raise AccessError("Choose a school.")
    if department_id is not None:
        department = session.get(Department, department_id)
        if department is None or department.school_id != school_id:
            raise AccessError("That department is not in that school.")
    if user.user_type == "DEPARTMENT_HR" and department_id is None:
        raise AccessError("A department HR account is granted a department, not a whole school.")
    row = next((g for g in user.grants if g.school_id == school_id and g.department_id == department_id), None)
    if row is None:
        row = UserSchoolAccess(user_id=user.user_id, school_id=school_id, department_id=department_id, access_level=level)
        user.grants.append(row)
    row.access_level = level
    session.flush()
    return row


def revoke(session: Session, user: User, grant_id: int) -> None:
    row = next((g for g in user.grants if g.id == grant_id), None)
    if row is not None:
        user.grants.remove(row)
        session.flush()


# --- a forgotten password ------------------------------------------------------

RESET_VALID_FOR = timedelta(minutes=30)
RESET_NOT_AGAIN_FOR = timedelta(minutes=5)


def start_password_reset(session: Session, email: str, now: datetime | None = None) -> tuple[User, str] | None:
    """Make a one-time reset link for the account with this address. Returns (account, token), or None when none is made.

    None for an unknown or closed account, and when a link was made in the
    last five minutes. The caller answers the same words either way, so the
    page cannot be used to find out which addresses have accounts.
    """
    now = now or _now()
    user = session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not user.is_active:
        return None
    latest = session.scalars(select(PasswordReset).where(PasswordReset.user_id == user.user_id)
                             .order_by(PasswordReset.reset_id.desc()).limit(1)).first()
    if latest is not None and now - _aware(latest.created_at) < RESET_NOT_AGAIN_FOR:
        return None
    token = secrets.token_urlsafe(32)
    session.add(PasswordReset(token_hash=_token_hash(token), user_id=user.user_id, created_at=now, expires_at=now + RESET_VALID_FOR))
    session.flush()
    return user, token


def _live_reset(session: Session, token: str, now: datetime) -> PasswordReset | None:
    row = session.scalar(select(PasswordReset).where(PasswordReset.token_hash == _token_hash(token or "")))
    if row is None or row.used_at is not None or _aware(row.expires_at) <= now:
        return None
    user = session.get(User, row.user_id)
    return row if user is not None and user.is_active else None


def reset_is_live(session: Session, token: str, now: datetime | None = None) -> bool:
    return _live_reset(session, token, now or _now()) is not None


def finish_password_reset(session: Session, token: str, new: str, now: datetime | None = None) -> User:
    """Set the new password for the account a reset link belongs to. The link works once."""
    now = now or _now()
    row = _live_reset(session, token, now)
    if row is None:
        raise AccessError("This link is no longer valid. Ask for a new one.")
    user = session.get(User, row.user_id)
    check_new_password(new, user.email)
    user.password_hash, user.must_change_password = hash_password(new), False
    user.failed_logins, user.locked_until = 0, None
    for other in session.scalars(select(PasswordReset).where(PasswordReset.user_id == user.user_id, PasswordReset.used_at.is_(None))):
        other.used_at = now  # this one, and any older link still unused
    _end_sessions(session, user)
    session.flush()
    return user
