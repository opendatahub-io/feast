FROM registry.access.redhat.com/ubi9/python-312-minimal:latest

USER 0
RUN microdnf install -y git gcc gcc-c++ libpq-devel python3.12-devel tar dnf && microdnf clean all

ENV HOME=/root
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
mv /root/.local/bin/uv /usr/local/bin/uv && \
chmod +x /usr/local/bin/uv && \
uv --version

# Copy Power prebuild script (pyarrow/re2 pins only)
COPY prebuild-power-base.sh /prebuild-power.sh
RUN chmod +x /prebuild-power.sh

ENV UV_CACHE_DIR=/tmp/uv-cache
COPY requirements.txt requirements.txt

# Power (ppc64le) only: pin pyarrow and re2
RUN set -eux; \
    if [ "$(uname -m)" = "ppc64le" ]; then \
        echo "Power architecture detected. Installing pinned pyarrow and re2 version..."; \
        /prebuild-power.sh; \
    else \
        echo "Non-Power architecture detected; skipping Power-specific pyarrow installation."; \
        touch /power-constraints.txt; \
    fi

# Pin IBM pyarrow/re2/grpcio on ppc64le so uv does not resolve a newer sdist.
# feast[minimal] includes milvus -> faiss-cpu. PyPI has no ppc64le faiss wheel;
# the sdist is a thin SWIG wrapper (needs libfaiss + swig). Same omission as
# hermetic sdist locks (--no-emit-package faiss-cpu/milvus-lite). Install the
# minimal extras minus milvus; version still comes from requirements.txt.
RUN set -eux; \
    if [ -s /power-constraints.txt ]; then \
        FEAST_VER=$(awk -F'==' 'NR==2{gsub(/[[:space:]]/,"",$2); print $2}' requirements.txt); \
        uv pip install --index-strategy unsafe-best-match \
            --extra-index-url https://wheels.developerfirst.ibm.com/ppc64le/linux \
            -c /power-constraints.txt \
            "feast[aws,gcp,snowflake,redis,go,mysql,postgres-c,opentelemetry,grpcio,k8s,duckdb,mcp]==${FEAST_VER}"; \
    else \
        uv pip install -r requirements.txt; \
    fi

# modify permissions to support running with a random uid
RUN chmod g+w $(python -c "import feast.ui as ui; print(ui.__path__)" | tr -d "[']")/build/projects-list.json

# Switch back to the default non-root user
USER 1001
