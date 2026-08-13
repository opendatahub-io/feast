FROM registry.access.redhat.com/ubi9/python-312-minimal:latest

USER 0
RUN microdnf install -y git gcc libpq-devel python3.12-devel && microdnf clean all
USER 1001

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

USER 0
ENV UV_CACHE_DIR=/tmp/uv-cache

# Copy feast source for local installation
COPY sdk/python/feast/infra/feature_servers/multicloud/requirements.txt /tmp/feast-version.txt
COPY pyproject.toml README.md /src/
COPY sdk/python /src/sdk/python

# Install feast from local source instead of PyPI
WORKDIR /src
RUN SETUPTOOLS_SCM_PRETEND_VERSION=$(awk -F'==' 'NR==2{gsub(/[[:space:]]/,"",$2); print $2}' /tmp/feast-version.txt) \
    uv pip install '.[minimal]'

# When building from source, setuptools does not include non-Python data files
# (static assets, UI build, templates, jinja2, sql) because there is no .git
# dir for setuptools-scm to list tracked files.  Copy them from the source tree.
COPY --chown=0:0 sdk/python/feast/static /opt/app-root/lib64/python3.12/site-packages/feast/static
COPY --chown=0:0 sdk/python/feast/templates /opt/app-root/lib64/python3.12/site-packages/feast/templates
COPY --chown=0:0 sdk/python/feast/api/registry/rest/templates /opt/app-root/lib64/python3.12/site-packages/feast/api/registry/rest/templates
COPY --chown=0:0 sdk/python/feast/infra/utils/snowflake /opt/app-root/lib64/python3.12/site-packages/feast/infra/utils/snowflake

# The PyPI wheel ships a pre-built React UI under feast/ui/build/; when
# building from source that directory does not exist.  Create the minimal
# structure the runtime needs (projects-list.json is overwritten at startup).
RUN UI_DIR=$(python -c "import feast.ui as ui; print(ui.__path__)" | tr -d "[']") && \
    mkdir -p "${UI_DIR}/build" && \
    echo '{"projects":[]}' > "${UI_DIR}/build/projects-list.json" && \
    chmod g+w "${UI_DIR}/build/projects-list.json"

WORKDIR /
RUN rm -rf /src

# Switch back to the default non-root user
USER 1001
