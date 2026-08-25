# Container image for the standalone Feast MCP server (`feast mcp`).
#
# A thin wrapper over the feature-server image: that image already installs
# feast and its dependencies, so this only sets the entrypoint.
#
# The entrypoint has to live in the image because the kubernetes-sigs
# mcp-lifecycle-operator MCPServer CRD exposes only `config.arguments` -- there
# is no `command` override, so the CR's arguments are appended to whatever the
# image already runs.
#
#     make build-feast-mcp-docker VERSION=0.66.0

ARG BASE_IMAGE=quay.io/feastdev/feature-server
ARG BASE_TAG=latest

FROM ${BASE_IMAGE}:${BASE_TAG}

# Documents the port feast_mcp.yaml is expected to set; `server.port` in that
# file is what actually binds. Deliberately no FEAST_MCP_* defaults here --
# environment outranks the config file in feast.mcp.config, so anything baked
# in would silently override the mounted yaml.
EXPOSE 8000

# transport, host, port, and the upstream Feast URLs all come from the mounted
# feast_mcp.yaml -- see sample.feast_mcp.yaml in this directory. Mount it at
# /config, or override the path by setting `config.arguments` on the CR, which
# replaces this CMD.
ENTRYPOINT ["feast", "mcp"]
CMD ["--config", "/config/feast_mcp.yaml"]
