FROM registry.access.redhat.com/ubi9/python-312-minimal:latest

USER 0
RUN microdnf install -y git gcc gcc-c++ libpq-devel python3.12-devel tar dnf && microdnf clean all

ENV HOME=/root
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
mv /root/.local/bin/uv /usr/local/bin/uv && \
chmod +x /usr/local/bin/uv && \
uv --version

# Copy Power prebuild script (pyarrow/re2/grpcio pins for ppc64le)
COPY prebuild-power-base.sh /prebuild-power-base.sh
RUN chmod +x /prebuild-power-base.sh

ENV UV_CACHE_DIR=/tmp/uv-cache
COPY requirements.txt requirements.txt

# Power (ppc64le) only: pin pyarrow, re2, and grpcio via IBM wheels 
RUN set -eux; \
    if [ "$(uname -m)" = "ppc64le" ]; then \
        echo "Power architecture detected. Installing pinned pyarrow and re2 version..."; \
        /prebuild-power-base.sh; \
    else \
        echo "Non-Power architecture detected; skipping Power-specific pyarrow installation."; \
    fi

# On ppc64le: feast[minimal] extras minus milvus (no faiss-cpu wheel).
# Keep the version pin from requirements.txt; only swap extras.
# On other arches: standard requirements.txt install.
RUN set -eux; \
    if [ "$(uname -m)" = "ppc64le" ]; then \
        test -s /power-constraints.txt; \
        sed -E 's/^feast\[minimal\]/feast[aws,gcp,snowflake,redis,go,mysql,postgres-c,opentelemetry,grpcio,k8s,duckdb,mcp]/' \
            requirements.txt > /tmp/requirements-ppc64le.txt; \
        grep -q '^feast\[aws,' /tmp/requirements-ppc64le.txt; \
        uv pip install --index-strategy unsafe-best-match \
            --extra-index-url https://wheels.developerfirst.ibm.com/ppc64le/linux \
            -c /power-constraints.txt \
            -r /tmp/requirements-ppc64le.txt; \
    else \
        uv pip install -r requirements.txt; \
    fi

# modify permissions to support running with a random uid
RUN chmod g+w $(python -c "import feast.ui as ui; print(ui.__path__)" | tr -d "[']")/build/projects-list.json

# Switch back to the default non-root user
USER 1001
