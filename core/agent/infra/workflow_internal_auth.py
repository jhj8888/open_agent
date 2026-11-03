"""Authenticate deployment-internal callers of the Agent workflow endpoint."""

import secrets
from typing import Annotated

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from agent.infra.credentials import credential_from_env_or_file

WORKFLOW_INTERNAL_API_KEY_ENV = "WORKFLOW_INTERNAL_API_KEY"
WORKFLOW_INTERNAL_API_KEY_FILE_ENV = "WORKFLOW_INTERNAL_API_KEY_FILE"
WORKFLOW_INTERNAL_API_KEY_HEADER = "X-Workflow-Internal-Key"
WORKFLOW_INTERNAL_API_KEY_PLACEHOLDER = "CHANGE_ME_WORKFLOW_INTERNAL_API_KEY"
WORKFLOW_INTERNAL_API_KEY_MIN_LENGTH = 32

_workflow_internal_api_key_header = APIKeyHeader(
    name=WORKFLOW_INTERNAL_API_KEY_HEADER,
    auto_error=False,
)


def optional_workflow_internal_api_key() -> str:
    """Return a valid deployment-internal key without accepting published defaults."""
    return credential_from_env_or_file(
        WORKFLOW_INTERNAL_API_KEY_ENV,
        WORKFLOW_INTERNAL_API_KEY_FILE_ENV,
        min_length=WORKFLOW_INTERNAL_API_KEY_MIN_LENGTH,
        placeholders=(WORKFLOW_INTERNAL_API_KEY_PLACEHOLDER,),
    )


def configured_workflow_internal_api_key() -> str:
    """Return the configured key or fail closed while deployment is incomplete."""
    api_key = optional_workflow_internal_api_key()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workflow internal API authentication is not configured",
        )
    return api_key


async def require_workflow_internal_api_key(
    supplied_api_key: Annotated[
        str | None, Security(_workflow_internal_api_key_header)
    ],
) -> None:
    """Require the same internal credential shared by Workflow and Agent."""
    expected_api_key = configured_workflow_internal_api_key()
    if not supplied_api_key or not secrets.compare_digest(
        supplied_api_key, expected_api_key
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid workflow internal API credentials",
        )
