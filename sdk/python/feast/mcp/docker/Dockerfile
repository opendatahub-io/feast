# Container image for the standalone Feast MCP server (`feast mcp`).
#
# A thin wrapper over the feature-server image: that image already installs
# feast and its dependencies, so this only sets the entrypoint.
#
#     make build-feast-mcp-docker VERSION=0.66.0

ARG BASE_IMAGE=quay.io/feastdev/feature-server
ARG BASE_TAG=latest

FROM ${BASE_IMAGE}:${BASE_TAG}

# Python needs a writable temp dir; under readOnlyRootFilesystem the usual
# candidates (/tmp, /var/tmp, /usr/tmp, /) are all read-only. /dev/shm is a
# tmpfs the kubelet mounts independently of the rootfs.
ENV TMPDIR=/dev/shm

EXPOSE 8000
ENTRYPOINT ["feast", "mcp"]
CMD ["--config", "/config/feast_mcp.yaml"]
