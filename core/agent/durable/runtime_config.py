"""Transient worker-only data. These types must never be workflow/activity arguments or results."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from agent.durable.control import AttemptLease, ControlPlaneClient
from agent.platform_runtime.artifacts import ReleaseRef


class WorkerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    endpoint: Annotated[str, Field(min_length=1, max_length=2048)]
    modelName: Annotated[str, Field(min_length=1, max_length=255)]
    credential: SecretStr = Field(repr=False)
    version: Annotated[int, Field(ge=1)]
    supportsTools: bool
    supportsUsage: bool
    connectTimeoutSeconds: Annotated[int, Field(ge=1, le=60)]
    readTimeoutSeconds: Annotated[int, Field(ge=1, le=1800)]
    totalTimeoutSeconds: Annotated[int, Field(ge=1, le=3600)]
    caRef: Annotated[str, Field(max_length=128)] | None


class SkillBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    releaseId: UUID
    manifestSha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class RuntimeBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input: dict[str, Any] = Field(repr=False)
    snapshot: dict[str, Any] = Field(repr=False)
    model: WorkerModel = Field(repr=False)
    deadline: datetime
    skills: Annotated[list[SkillBinding], Field(max_length=20)]

    @field_validator("deadline")
    @classmethod
    def aware_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Deadline must have a timezone")
        return value


class RuntimeReleaseSource:
    def __init__(self, control: ControlPlaneClient, lease: AttemptLease):
        self.control, self.lease = control, lease

    async def manifest(self, ref: ReleaseRef) -> bytes:
        return await self.control.artifact(
            self.lease, str(ref.release_id), -1, 512 * 1024
        )

    async def file(self, ref: ReleaseRef, index: int, maximum: int) -> bytes:
        return await self.control.artifact(
            self.lease, str(ref.release_id), index, maximum
        )
