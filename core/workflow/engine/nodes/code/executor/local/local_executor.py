from typing import Any

from workflow.engine.nodes.code.executor.base_executor import BaseExecutor
from workflow.exception.e import CustomException
from workflow.exception.errors.err_code import CodeEnum
from workflow.extensions.otlp.trace.span import Span


class LocalExecutor(BaseExecutor):
    """Compatibility shim for the removed in-process code executor.

    User-provided code must never execute in the workflow service process. A
    child process and a timeout do not isolate the filesystem, credentials,
    network, or operating-system user from untrusted code.
    """

    async def execute(
        self, language: str, code: str, timeout: int, span: Span, **kwargs: Any
    ) -> str:
        raise CustomException(
            err_code=CodeEnum.CODE_EXECUTION_ERROR,
            err_msg=(
                "The local code executor is disabled for security. "
                "Configure an isolated code executor before running code nodes."
            ),
        )
