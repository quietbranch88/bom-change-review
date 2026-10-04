"""Local synthetic-account authentication, not HTTP/OIDC or an OS security boundary."""

from contextlib import contextmanager
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import quote

from auth_contract import AuthError, Principal

APP_ID = 1112493377
SCHEMA_VERSION = 1
SCRYPT_N = 2 ** 17
LOGIN_LIMIT = 10
LOGIN_WINDOW = 60
SCHEMA = """
CREATE TABLE accounts (
 user_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('reader','reviewer')),
 salt BLOB NOT NULL CHECK(length(salt)=16), digest BLOB NOT NULL CHECK(length(digest)=32),
 enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)));
CREATE TABLE cases (snapshot_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL);
CREATE TABLE grants (
 user_id TEXT NOT NULL REFERENCES accounts(user_id),
 snapshot_id TEXT NOT NULL REFERENCES cases(snapshot_id),
 PRIMARY KEY(user_id,snapshot_id));
CREATE TABLE sessions (
 verifier TEXT PRIMARY KEY, user_id TEXT NOT NULL UNIQUE REFERENCES accounts(user_id),
 created REAL NOT NULL, last_seen REAL NOT NULL, expires REAL NOT NULL,
 idle_seconds INTEGER NOT NULL CHECK(idle_seconds>0));
CREATE TABLE login_bucket (
 id INTEGER PRIMARY KEY CHECK(id=1), started REAL NOT NULL, attempts INTEGER NOT NULL,
 dummy_salt BLOB NOT NULL, dummy_digest BLOB NOT NULL);
"""


def password_digest(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=8, p=1,
                          maxmem=256 * 1024 * 1024, dklen=32)


def valid_password(value):
    return isinstance(value, str) and 15 <= len(value) <= 128


def verifier(token):
    if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
        raise AuthError("authentication_required")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class SQLiteAuth:
    def __init__(self, path, *, absolute_seconds=900, idle_seconds=300, clock=time.time):
        if (type(absolute_seconds) is not int or not 1 <= absolute_seconds <= 86400
                or type(idle_seconds) is not int or not 1 <= idle_seconds <= absolute_seconds):
            raise ValueError("invalid_session_lifetime")
        self.path = Path(path).resolve()
        self.absolute_seconds, self.idle_seconds, self.clock = absolute_seconds, idle_seconds, clock
        # Open existing stores only. A missing database must not recreate an empty auth policy.
        with self.transaction() as connection:
            self.check_schema(connection)

    @classmethod
    def initialize(cls, path, accounts, cases, grants, **kwargs):
        """Trusted synthetic composition only: (user, tenant, role, password) tuples."""
        if not accounts or any(not valid_password(row[3]) for row in accounts):
            raise ValueError("invalid_synthetic_accounts")
        path = Path(path).resolve()
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.executescript(SCHEMA)
                with connection:
                    connection.execute(f"PRAGMA application_id={APP_ID}")
                    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                    for user, tenant, role, password in accounts:
                        salt = secrets.token_bytes(16)
                        connection.execute("INSERT INTO accounts(user_id,tenant_id,role,salt,digest) VALUES(?,?,?,?,?)",
                                           (user, tenant, role, salt, password_digest(password, salt)))
                    connection.executemany("INSERT INTO cases VALUES(?,?)", cases)
                    connection.executemany("INSERT INTO grants VALUES(?,?)", grants)
                    salt = secrets.token_bytes(16)
                    connection.execute("INSERT INTO login_bucket VALUES(1,0,0,?,?)",
                                       (salt, password_digest(secrets.token_urlsafe(32), salt)))
            finally:
                connection.close()
        except (OSError, sqlite3.Error, ValueError):
            # Do not remove an existing or partially initialized file; diagnose privately.
            raise AuthError("auth_initialization_failed") from None
        return cls(path, **kwargs)

    @contextmanager
    def transaction(self):
        connection = None
        try:
            connection = sqlite3.connect("file:" + quote(self.path.as_posix(), safe="/:") + "?mode=rw",
                                         uri=True, timeout=.1)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            self.check_schema(connection)
            yield connection
            connection.commit()
        except AuthError:
            raise
        except (sqlite3.Error, OSError):
            raise AuthError("auth_unavailable") from None
        finally:
            if connection is not None:
                connection.close()  # Uncommitted failures roll back; no leaked connection.

    @staticmethod
    def check_schema(connection):
        if (connection.execute("PRAGMA application_id").fetchone()[0] != APP_ID
                or connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION):
            raise AuthError("auth_store_mismatch")

    def login(self, username, password):
        if (not isinstance(username, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", username) is None
                or not valid_password(password)):
            raise AuthError("invalid_credentials")
        error, token = None, None
        with self.transaction() as connection:
            now = self.clock()
            started, attempts, dummy_salt, dummy_digest = connection.execute(
                "SELECT started,attempts,dummy_salt,dummy_digest FROM login_bucket WHERE id=1").fetchone()
            if now >= started + LOGIN_WINDOW:
                started, attempts = now, 0
            if attempts >= LOGIN_LIMIT:
                error = "login_rate_limited"
            else:
                connection.execute("UPDATE login_bucket SET started=?,attempts=? WHERE id=1", (started, attempts + 1))
                row = connection.execute("SELECT salt,digest,enabled FROM accounts WHERE user_id=?", (username,)).fetchone()
                salt, expected = (row[0], row[1]) if row else (dummy_salt, dummy_digest)
                try:
                    matched = hmac.compare_digest(password_digest(password, salt), expected)
                except (ValueError, MemoryError):
                    raise AuthError("auth_unavailable") from None
                if row is None or not matched or row[2] != 1:
                    error = "invalid_credentials"
                else:
                    # Measure TTL from issuance, not from the beginning of the expensive hash.
                    now = self.clock()
                    connection.execute("DELETE FROM sessions WHERE expires<=? OR last_seen+idle_seconds<=? OR user_id=?",
                                       (now, now, username))
                    token = secrets.token_urlsafe(32)
                    connection.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?)",
                                       (verifier(token), username, now, now, now + self.absolute_seconds, self.idle_seconds))
        if error:
            raise AuthError(error)  # Invalid credentials still commit the attempt counter.
        return token

    def authorize(self, token, snapshot_id, action="read_evidence"):
        key = verifier(token)
        error, principal = None, None
        with self.transaction() as connection:
            now = self.clock()
            row = connection.execute(
                "SELECT a.user_id,a.tenant_id,a.role,s.expires,s.last_seen,a.enabled,s.idle_seconds "
                "FROM sessions s JOIN accounts a ON a.user_id=s.user_id WHERE s.verifier=?", (key,)).fetchone()
            if row is None or row[5] != 1 or now >= row[3] or now >= row[4] + row[6]:
                connection.execute("DELETE FROM sessions WHERE verifier=?", (key,))
                error = "authentication_required"
            else:
                grant = connection.execute(
                    "SELECT 1 FROM grants g JOIN cases c ON c.snapshot_id=g.snapshot_id "
                    "WHERE g.user_id=? AND g.snapshot_id=? AND c.tenant_id=?", (row[0], snapshot_id, row[1])).fetchone()
                if action != "read_evidence" or grant is None:
                    error = "access_denied"
                else:
                    connection.execute("UPDATE sessions SET last_seen=? WHERE verifier=?", (now, key))
                    principal = Principal(row[0], row[1], row[2])
        if error:
            raise AuthError(error)  # Commit expiry/disable cleanup before returning the refusal.
        return principal

    def logout(self, token):
        key = verifier(token)
        with self.transaction() as connection:
            connection.execute("DELETE FROM sessions WHERE verifier=?", (key,))
