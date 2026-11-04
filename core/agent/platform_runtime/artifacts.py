"""Verify the exact manifest bytes issued by the tenant-scoped Java control plane."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class ArtifactError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("SKILL_ARTIFACT_INVALID")


def safe_path(value: str) -> str:
    if (
        not value
        or len(value) > 1024
        or value.startswith("/")
        or "\\" in value
        or ":" in value
        or unicodedata.normalize("NFC", value) != value
        or any(unicodedata.category(char) == "Cc" for char in value)
        or any(
            not part
            or part in {".", ".."}
            or part != part.strip()
            or part.endswith(".")
            for part in value.split("/")
        )
    ):
        raise ArtifactError()
    return value


class ManifestFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    path: str
    size: Annotated[StrictInt, Field(ge=0, le=MAX_FILE_BYTES)]
    sha256: Digest
    dependencyLock: bool

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return safe_path(value)


class ReleaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schemaVersion: Literal[1]
    name: Annotated[str, Field(min_length=1, max_length=64)]
    version: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]
    runtimeImage: Annotated[
        str,
        Field(max_length=512, pattern=r"^[a-z0-9][a-z0-9._:/-]*@sha256:[a-f0-9]{64}$"),
    ]
    dependencyPolicy: Literal["preinstalled-image-only"]
    files: Annotated[list[ManifestFile], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def validate_package(self) -> "ReleaseManifest":
        name = self.name
        if (
            name != name.lower()
            or name.startswith("-")
            or name.endswith("-")
            or "--" in name
            or unicodedata.normalize("NFC", name) != name
            or any(
                not (char.isalpha() or char.isdigit() or char == "-") for char in name
            )
        ):
            raise ArtifactError()
        paths = [file.path.lower() for file in self.files]
        if len(set(paths)) != len(paths) or not any(
            file.path == "SKILL.md" for file in self.files
        ):
            raise ArtifactError()
        if any(other.startswith(path + "/") for path in paths for other in paths):
            raise ArtifactError()
        if sum(file.size for file in self.files) > MAX_TOTAL_BYTES:
            raise ArtifactError()
        return self


def _unique_object(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ArtifactError()
        result[key] = value
    return result


def verify_manifest(raw: bytes, expected_digest: str) -> ReleaseManifest:
    try:
        if (
            len(raw) > 512 * 1024
            or not re.fullmatch(r"[a-f0-9]{64}", expected_digest)
            or hashlib.sha256(raw).hexdigest() != expected_digest
        ):
            raise ArtifactError()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        return ReleaseManifest.model_validate(value)
    except Exception:
        raise ArtifactError() from None


@dataclass(frozen=True)
class ReleaseRef:
    release_id: UUID
    manifest_sha256: str


@dataclass(frozen=True)
class VerifiedRelease:
    reference: ReleaseRef
    manifest: ReleaseManifest
    contents: tuple[bytes, ...]


class ReleaseSource(Protocol):
    """A trusted client must scope both methods to the authenticated run snapshot."""

    async def manifest(self, reference: ReleaseRef) -> bytes: ...
    async def file(
        self, reference: ReleaseRef, index: int, max_bytes: int
    ) -> bytes: ...


async def load_release(source: ReleaseSource, reference: ReleaseRef) -> VerifiedRelease:
    manifest = verify_manifest(
        await source.manifest(reference), reference.manifest_sha256
    )
    contents: list[bytes] = []
    for index, file in enumerate(manifest.files):
        # The source must also bound the network read before allocating the returned bytes.
        content = await source.file(reference, index, file.size)
        if (
            len(content) != file.size
            or hashlib.sha256(content).hexdigest() != file.sha256
        ):
            raise ArtifactError()
        contents.append(content)
    return VerifiedRelease(reference, manifest, tuple(contents))
