FROM registry.access.redhat.com/ubi9/python-312-minimal:latest

USER 0
RUN microdnf install -y git gcc libpq-devel libpq-devel python3.12-devel tar dnf && microdnf clean all

ENV HOME=/root
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
mv /root/.local/bin/uv /usr/local/bin/uv && \
chmod +x /usr/local/bin/uv && \
uv --version

# Copy Power prebuild script
COPY prebuild-power-base.sh /prebuild-power.sh
RUN chmod +x /prebuild-power.sh

ENV UV_CACHE_DIR=/tmp/uv-cache
COPY requirements.txt requirements.txt

# Run prebuild script conditionally (Power only).
# set -e so a failed prebuild fails the layer; otherwise uv compiles pyarrow
# 25.x from an sdist (libcst/Rust) because no ppc64le wheel exists on PyPI.
RUN set -eux; \
    if [ "$(uname -m)" = "ppc64le" ]; then \
        echo "Power architecture detected. Running prebuild script..."; \
        /prebuild-power.sh; \
        echo "Listing built wheels:"; \
        ls -lh /wheelhouse; \
        uv pip install /wheelhouse/*.whl; \
        uv pip freeze | grep -E '^(pyarrow|grpcio)==' > /power-constraints.txt || true; \
        if [ ! -s /power-constraints.txt ]; then \
            echo "ERROR: pyarrow/grpcio missing after Power prebuild" >&2; \
            exit 1; \
        fi; \
        cat /power-constraints.txt; \
    else \
        echo "Non-Power architecture detected; skipping Power prebuild"; \
        touch /power-constraints.txt; \
    fi

# Pin prebuilt pyarrow/grpcio on ppc64le so uv does not resolve a newer sdist.
RUN set -eux; \
    if [ -s /power-constraints.txt ]; then \
        uv pip install -r requirements.txt -c /power-constraints.txt; \
    else \
        uv pip install -r requirements.txt; \
    fi

# modify permissions to support running with a random uid
RUN chmod g+w $(python -c "import feast.ui as ui; print(ui.__path__)" | tr -d "[']")/build/projects-list.json

# Switch back to the default non-root user
USER 1001
