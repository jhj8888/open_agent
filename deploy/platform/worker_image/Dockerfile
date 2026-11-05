ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS dependencies
COPY wheels /wheels
COPY requirements.lock /input/requirements.lock
RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-index --no-cache-dir --only-binary=:all: --require-hashes --find-links=/wheels -r /input/requirements.lock \
    && /opt/venv/bin/python -m pip check

FROM ${BASE_IMAGE}
COPY --from=dependencies /opt/venv /opt/venv
COPY source/core /opt/open-agent/core
COPY licenses /opt/open-agent/licenses
COPY inventory /opt/open-agent/inventory
RUN groupadd --gid 10001 open-agent \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin open-agent
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/opt/open-agent/core:/opt/open-agent/core/common" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_INDEX=1 \
    LANGSMITH_TRACING=false \
    LANGCHAIN_TRACING_V2=false
WORKDIR /opt/open-agent
USER 10001:10001
ENTRYPOINT ["/opt/venv/bin/python"]
CMD ["-m", "agent.durable.cluster_child"]
