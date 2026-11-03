"""Small, fail-closed helpers for deployment-managed credential files."""

import hashlib
import os
import stat
from typing import Collection

MAX_CREDENTIAL_FILE_BYTES = 4096
TENANT_INTERNAL_API_KEY_HEADER = "X-Tenant-Internal-Key"

# Cache keys must be deterministic so that all Workflow replicas can reuse a
# verification result, but they must not be a plain fast hash of an API secret.
# A domain-separated PBKDF2 digest keeps the cache namespace opaque and makes
# offline guessing materially more expensive than a single fast hash.  The
# context is intentionally versioned: the cache prefix is bumped alongside it
# so old SHA-256 entries cannot be reused.
_CREDENTIAL_CACHE_KEY_CONTEXT = b"astron-agent:workflow:credential-cache:v3"
_CREDENTIAL_CACHE_KDF_ITERATIONS = 100_000


def credential_cache_key(credential: str) -> str:
    """Return the deterministic, versioned digest used for credential caches."""
    return hashlib.pbkdf2_hmac(
        "sha256",
        credential.encode("utf-8"),
        _CREDENTIAL_CACHE_KEY_CONTEXT,
        _CREDENTIAL_CACHE_KDF_ITERATIONS,
        dklen=32,
    ).hex()


def read_credential_file(file_name: str) -> str:
    """Read a bounded regular file through one non-following file descriptor."""
    if not file_name:
        return ""
    descriptor = -1
    try:
        before_open = os.lstat(file_name)
        if stat.S_ISLNK(before_open.st_mode) or not stat.S_ISREG(before_open.st_mode):
            return ""
        if before_open.st_size > MAX_CREDENTIAL_FILE_BYTES:
            return ""
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(file_name, flags)
        after_open = os.fstat(descriptor)
        if (
            not stat.S_ISREG(after_open.st_mode)
            or not os.path.samestat(before_open, after_open)
            or after_open.st_size > MAX_CREDENTIAL_FILE_BYTES
        ):
            return ""
        data = bytearray()
        while len(data) <= MAX_CREDENTIAL_FILE_BYTES:
            chunk = os.read(
                descriptor,
                min(1024, MAX_CREDENTIAL_FILE_BYTES + 1 - len(data)),
            )
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > MAX_CREDENTIAL_FILE_BYTES:
            return ""
        return bytes(data).decode("utf-8").strip()
    except (OSError, UnicodeError):
        return ""
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def credential_from_env_or_file(
    value_env: str,
    file_env: str,
    *,
    min_length: int = 1,
    max_length: int = MAX_CREDENTIAL_FILE_BYTES,
    placeholders: Collection[str] = (),
) -> str:
    """Prefer a valid environment value, then a deployment-managed file."""

    def valid(value: str) -> bool:
        return (
            min_length <= len(value) <= max_length
            and "\r" not in value
            and "\n" not in value
            and value not in placeholders
        )

    value = os.getenv(value_env, "").strip()
    if valid(value):
        return value
    value = read_credential_file(os.getenv(file_env, "").strip())
    return value if valid(value) else ""
