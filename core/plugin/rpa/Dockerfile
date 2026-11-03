FROM python:3.11-slim

WORKDIR /opt/core

ENV PATH=$PATH:/opt/core
ENV PYTHONPATH /opt/core
ENV UV_NO_CACHE=1

# Use the official index by default.  The image is built concurrently for two
# architectures in CI, and public mirrors can reject one of the parallel
# requests (for example, with HTTP 403).  Builders that require an internal
# mirror can override this argument without changing the deployment contract.
ARG PYPI_INDEX_URL=https://pypi.org/simple/
RUN pip install uv --no-cache-dir --index-url "${PYPI_INDEX_URL}"

COPY core/plugin/rpa/pyproject.toml ./
COPY core/plugin/rpa/uv.lock ./

RUN uv sync --default-index "${PYPI_INDEX_URL}"

COPY core/common ./common
COPY core/plugin/rpa ./plugin/rpa

CMD ["uv", "run", "plugin/rpa/main.py"]
