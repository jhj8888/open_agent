# Use the base image with Python 3.11 and Spark Link dependencies
FROM python:3.11-slim

# Set the working directory for the application
WORKDIR /opt/core

# Configure environment variables for Python path and UV cache
ENV PATH=$PATH:/opt/core
ENV PYTHONPATH /opt/core
ENV UV_NO_CACHE=1

# LangChain's official Pyodide sandbox launches a Deno subprocess for every
# Code node invocation.  Install a pinned, checksum-verified Deno binary for
# both architectures produced by the multi-platform image build.
ARG TARGETARCH
ENV DENO_DIR=/opt/deno-cache
ENV DENO_NO_UPDATE_CHECK=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && case "$TARGETARCH" in \
        amd64) deno_arch='x86_64-unknown-linux-gnu'; deno_sha256='cb573ef18e06b6f17db31b7a410143abfebe0c8d1faa99cb934a7c1351c75701' ;; \
        arm64) deno_arch='aarch64-unknown-linux-gnu'; deno_sha256='5d9dc69fbf324afe62f2c03c1241ae4629fecdb6032a1ded8a907ce480077f61' ;; \
        *) echo "Unsupported target architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac \
    && curl -fsSL --retry 5 --retry-delay 2 \
        "https://github.com/denoland/deno/releases/download/v2.3.3/deno-$deno_arch.zip" \
        -o /tmp/deno.zip \
    && printf '%s  %s\n' "$deno_sha256" /tmp/deno.zip | sha256sum -c - \
    && unzip -q /tmp/deno.zip deno -d /usr/local/bin \
    && chmod 0755 /usr/local/bin/deno \
    && /usr/local/bin/deno --version \
    && mkdir -p "$DENO_DIR" \
    && rm -f /tmp/deno.zip \
    && rm -rf /var/lib/apt/lists/*

# Warm the Deno module cache during the image build.  This keeps a fresh
# deployment independent of JSR/npm availability on its first Code-node run;
# Deno still verifies the cached module integrity at runtime.
RUN deno cache --node-modules-dir=auto \
    jsr:@langchain/pyodide-sandbox@0.0.4

# Pyodide's npm distribution contains the runtime and lockfile, but not the
# micropip and packaging wheels that the official LangChain wrapper loads on
# every invocation. Pre-seed the exact versions and checksums declared by the
# resolved lockfile so the runtime can keep Deno network permissions disabled.
# The npm range used by the wrapper can resolve to a newer patch release, so do
# not hard-code a Pyodide or wheel version here.
RUN set -eux; \
    pyodide_lock="$(find /opt/core/node_modules "$DENO_DIR" -type f -name pyodide-lock.json -print -quit 2>/dev/null)"; \
    test -n "$pyodide_lock"; \
    pyodide_dir="$(dirname "$pyodide_lock")"; \
    pyodide_version="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["info"]["version"])' "$pyodide_lock")"; \
    case "$pyodide_version" in *[!0-9.]*|'') echo "Invalid Pyodide version" >&2; exit 1 ;; esac; \
    for package in micropip packaging; do \
      wheel="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["packages"][sys.argv[2]]["file_name"])' "$pyodide_lock" "$package")"; \
      sha256="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["packages"][sys.argv[2]]["sha256"])' "$pyodide_lock" "$package")"; \
      case "$wheel" in */*|..*|'') echo "Invalid Pyodide wheel name" >&2; exit 1 ;; esac; \
      case "$sha256" in ''|*[!0-9a-fA-F]*) echo "Invalid Pyodide wheel checksum" >&2; exit 1 ;; esac; \
      test "${#sha256}" -eq 64; \
      curl -fsSL --retry 5 --retry-delay 2 \
        "https://cdn.jsdelivr.net/pyodide/v${pyodide_version}/full/${wheel}" \
        -o "$pyodide_dir/$wheel"; \
      printf '%s  %s\n' "$sha256" "$pyodide_dir/$wheel" | sha256sum -c -; \
    done

# Install UV package manager using Tsinghua mirror for faster downloads
RUN pip install uv --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/

# Copy dependency files for UV to resolve and install packages
COPY core/workflow/uv.lock core/workflow/pyproject.toml ./

# Install Python dependencies using UV with Tsinghua mirror
RUN uv sync -i https://mirrors.aliyun.com/pypi/simple/

# Copy the entire workflow source code to the container
COPY core/workflow ./workflow
COPY core/common ./common

# Set the default command to run the main application using UV
CMD ["uv", "run", "workflow/main.py"]
