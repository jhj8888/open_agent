"""Read deployment-managed credentials from environment variables or files."""

import os
import stat
from typing import Collection

MAX_CREDENTIAL_FILE_BYTES = 4096


def read_credential_file(file_name: str) -> str:
    """Read a bounded regular file through one non-following descriptor."""
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
    def valid(candidate: str) -> bool:
        return (
            min_length <= len(candidate) <= max_length
            and "\r" not in candidate
            and "\n" not in candidate
            and candidate not in placeholders
        )

    value = (os.getenv(value_env, "") or "").strip()
    if valid(value):
        return value
    file_name = (os.getenv(file_env, "") or "").strip()
    value = read_credential_file(file_name)
    return value if valid(value) else ""
