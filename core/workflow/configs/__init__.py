import os
from abc import ABC, abstractmethod
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from loguru import logger

from workflow.configs.app_config import DEFAULT_CODE_EXECUTOR_TYPE, WorkflowConfig
from workflow.consts.config_env import EnvStrategy

LEGACY_CODE_EXECUTOR_DEFAULT_COMMENTS = (
    # Directly previous releases shipped this header.
    "supported fallback types: disabled, ifly, ifly-v2, langchain (default: disabled)",
    # Keep compatibility with the earlier security release as well.
    "supported types: disabled, ifly, ifly-v2, langchain, e2b (default: disabled)",
)


def _read_simple_env_values(env_file: Path, keys: set[str]) -> dict[str, str]:
    """Read the small set of deployment settings needed before dotenv loading.

    ``load_dotenv`` intentionally keeps existing process variables authoritative.
    We need to inspect the mounted workflow config once before loading it so that
    an untouched config from the pre-Pyodide release can be migrated safely.  A
    non-interpolating read through python-dotenv keeps the compatibility check
    consistent with the actual load below (including quoting and comments).
    """
    try:
        parsed_values = dotenv_values(
            dotenv_path=env_file, interpolate=False, encoding="utf-8"
        )
    except (OSError, UnicodeError, ValueError):
        return {}

    return {key: value for key in keys if (value := parsed_values.get(key)) is not None}


def _has_legacy_code_executor_signature(env_file: Path) -> bool:
    """Return whether *env_file* has the historical generated-default header."""
    try:
        content = env_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    normalized_content = " ".join(content.lower().split())
    return any(
        signature in normalized_content
        for signature in LEGACY_CODE_EXECUTOR_DEFAULT_COMMENTS
    )


def _migrate_legacy_code_executor_default(env_file: Path) -> None:
    """Keep upgrades from the old disabled template zero-configuration.

    Before the built-in Pyodide sandbox became the default, the generated
    workflow config contained ``CODE_EXEC_TYPE=disabled`` and had no memory
    limit setting.  Compose deliberately preserves user-mounted config files,
    so an upgrade would otherwise keep that historical default forever and a
    fresh Code node would fail even though the new image contains the isolated
    executor.  Treat only that recognizable, unversioned template as a legacy
    default.  The historical header is required as an additional fingerprint
    so a customized config that explicitly sets ``disabled`` is not silently
    changed.  An explicit process-level ``CODE_EXEC_TYPE`` (including
    ``disabled``) always wins and remains fail-closed.
    """
    if "CODE_EXEC_TYPE" in os.environ:
        return

    values = _read_simple_env_values(
        env_file, {"CODE_EXEC_TYPE", "CODE_EXEC_MEMORY_LIMIT_MB"}
    )
    if (
        values.get("CODE_EXEC_TYPE", "").strip().lower() == "disabled"
        and "CODE_EXEC_MEMORY_LIMIT_MB" not in values
        and _has_legacy_code_executor_signature(env_file)
    ):
        os.environ["CODE_EXEC_TYPE"] = DEFAULT_CODE_EXECUTOR_TYPE
        logger.warning(
            "Migrating the legacy workflow code executor default from disabled "
            "to the built-in isolated LangChain/Pyodide sandbox. Set "
            "CODE_EXEC_TYPE=disabled as a process environment variable to "
            "explicitly disable Code nodes."
        )


class EnvLoader(ABC):
    """
    Abstract base class for environment variable loaders.
    Defines the interface for loading environment variables.
    """

    @abstractmethod
    def load(self) -> None:
        pass


class LocalLoader(EnvLoader):
    """
    Load environment variables from local .env files.
    """

    def __init__(self) -> None:
        """
        Initialize the LocalLoader by determining the appropriate .env file
        based on the runtime environment.
        """
        self.env_file = Path(__file__).parent.parent / "config.env"
        logger.debug(f"config.env: {self.env_file}")

    def load(self) -> None:
        """
        Load environment variables from the selected .env file.
        :raises ValueError: If no configuration file is found
        """
        if os.path.exists(self.env_file):
            # Compose may deliberately pass an empty optional override from
            # ``.env``.  Treat an empty value as unset so the mounted workflow
            # config can still supply its defaults.
            if not os.getenv("CODE_EXEC_TYPE", "").strip():
                os.environ.pop("CODE_EXEC_TYPE", None)
            _migrate_legacy_code_executor_default(self.env_file)
            load_dotenv(self.env_file, override=False)
            logger.debug("Using config.env file.")
        else:
            raise ValueError("No config.env file found.")


class PolarisLoader(EnvLoader):
    """
    Load environment variables from Polaris configuration management system.
    """

    def __init__(self) -> None:
        """
        Initialize the PolarisLoader with necessary Polaris connection parameters
        """
        self.base_url = os.getenv("POLARIS_URL", "")
        self.username = os.getenv("POLARIS_USERNAME", "")
        self.password = os.getenv("POLARIS_PASSWORD", "")
        self.project_name = os.getenv("PROJECT_NAME", "hy-spark-agent-builder")
        self.cluster_group = os.getenv("POLARIS_CLUSTER", "")
        self.service_name = os.getenv("POLARIS_SERVICE_NAME", "spark-flow")
        self.version = os.getenv("POLARIS_VERSION", "1.0.0")
        self.config_file = os.getenv("POLARIS_CONFIG_FILE", "config.env")
        logger.info(
            f"🔍 Polaris config info: "
            f"project name = {self.project_name}, "
            f"cluster group = {self.cluster_group}, "
            f"service name = {self.service_name}, "
            f"version = {self.version}, "
            f"config file = {self.config_file}"
        )

    def load(self) -> None:
        """
        Load environment variables from Polaris.
        :raises ConnectionError: If unable to connect to Polaris
        :raises TimeoutError: If the request to Polaris times out
        :raises ValueError: If Polaris returns invalid data
        """
        from common.settings.polaris import ConfigFilter, Polaris

        config_filter = ConfigFilter(
            project_name=self.project_name,
            cluster_group=self.cluster_group,
            service_name=self.service_name,
            version=self.version,
            config_file=self.config_file,
        )
        polaris = Polaris(
            base_url=self.base_url, username=self.username, password=self.password
        )
        try:
            _ = polaris.pull(
                config_filter=config_filter,
                retry_count=3,
                retry_interval=5,
                set_env=True,
            )
            return
        except (ConnectionError, TimeoutError, ValueError) as e:
            raise ValueError(
                f"⚠️ Polaris configuration loading failed, "
                f"continuing with local configuration: {e}"
            )


class EnvLoaderFactory:
    """
    Factory class to create EnvLoader instances based on strategy.
    """

    @staticmethod
    def create(strategy: str) -> "EnvLoader":
        """
        Create an EnvLoader instance based on the given strategy.
        :param strategy: The environment loading strategy (e.g., 'local', 'polaris')
        :return: An instance of EnvLoader
        """
        if strategy == EnvStrategy.Local.value:
            logger.info("🔍 Using Local file for configuration management.")
            return LocalLoader()
        if strategy == EnvStrategy.Polaris.value:
            logger.info("🔍 Using Polaris for configuration management.")
            return PolarisLoader()
        raise ValueError(f"Unknown strategy: {strategy}")


def set_env() -> None:
    """
    Set environment variables by loading configuration from environment files.

    This function determines the appropriate configuration file based on the
    runtime environment (local vs production) and loads the environment
    variables from the corresponding .env file.

    :raises ValueError: If no configuration file is found
    :raises Exception: Re-raises any other exceptions that occur during loading
    """
    strategy = os.getenv("CONFIG_TYPE", EnvStrategy.Local.value)
    loader = EnvLoaderFactory.create(strategy)
    loader.load()


set_env()
workflow_config = WorkflowConfig()
