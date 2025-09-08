FROM registry.access.redhat.com/ubi9/python-311:1

# OS Packages needs to be installed as root
USER 0

# Copy Power prebuild script
COPY prebuild-power.sh /prebuild-power.sh
RUN chmod +x /prebuild-power.sh

# Run prebuild script conditionally (Power only)
RUN if [ "$(uname -m)" = "ppc64le" ]; then \
        echo "Power architecture detected. Running prebuild script..."; \
        /prebuild-power.sh && pip install /wheelhouse/*.whl; \
        echo "Listing built wheels:" && ls -lh /wheelhouse; \
    else \
        echo "x86 detected, skipping Power prebuild"; \
    fi

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

# modify permissions to support running with a random uid
RUN chmod g+w $(python -c "import feast.ui as ui; print(ui.__path__)" | tr -d "[']")/build/projects-list.json
