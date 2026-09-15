# Standalone MCP server

## Overview

The standalone MCP server runs the Model Context Protocol in its own process, separate from the feature server. It is started with `feast mcp` and holds no registry or online store of its own. Instead, it proxies to a running Feast deployment and exposes its HTTP APIs as MCP tools, so that AI agents and MCP-capable clients can retrieve features and browse the registry.

The server is composed of two sub-servers, each mounted only when its upstream URL is configured:

| Sub-server | Proxies | Mounted when |
| ---------- | ------- | ------------ |
| `features` | The Python feature server (`feast serve`) | `--feast-url` or `features.url` is set |
| `registry` | The REST registry server (`feast serve_registry --rest-api`) | `--registry-url` or `registry.url` is set |

At least one of the two must be configured, otherwise the server exits with a usage error.

This is not the same as the [MCP Feature Server](mcp-feature-server.md), which sets `mcp_enabled: true` in `feature_store.yaml` to mount an OpenAPI-derived MCP endpoint inside the feature server process. The standalone server is a separate deployable that can front both the feature server and the registry at once, with its own tools, authentication mode, and observability settings. The two can be used together.

## Installation

```bash
pip install 'feast[mcp-server]'
```

For OTLP log and trace export, install the `mcp-server-otel` extra instead:

```bash
pip install 'feast[mcp-server-otel]'
```

The `minimal` extra pulls in `mcp-server-otel`, so the published `feature-server` image already includes `feast mcp` and the OTLP exporters.

## CLI

There is a CLI command that starts the server: `feast mcp`.

```bash
feast mcp --feast-url http://localhost:6566 --registry-url http://localhost:6572 --transport http --port 8000
```

A `feast-mcp` console script is also installed. It is equivalent to `feast mcp`, but skips loading the rest of the Feast CLI.

**Connection options:**
* `--config`: Path to the `feast_mcp.yaml` config file
* `--feast-url`: URL of the feature server to proxy. Mounts the `features` tools
* `--registry-url`: URL of the REST registry server to proxy. Mounts the `registry` tools
* `--timeout`: HTTP timeout in seconds for upstream calls (default: 30)

**Server options:**
* `--transport`: MCP transport to serve: `stdio`, `http`, `streamable-http`, or `sse` (default: `stdio`)
* `--host`: Bind address for HTTP transports (default: `0.0.0.0`)
* `--port`: Bind port for HTTP transports (default: 8000)
* `--workers`: Run under gunicorn with this many workers. Not supported by the `sse` transport

**Authentication options:**
* `--auth-mode`: `passthrough` or `oidc` (default: `passthrough`)
* `--oidc-discovery-url`: OIDC discovery document URL. Required with `--auth-mode oidc`
* `--oidc-client-id`: OIDC client id. Required with `--auth-mode oidc`
* `--oidc-client-secret`: OIDC client secret
* `--oidc-audience`: Expected OIDC token audience
* `--base-url`: Public base URL of this server, used to build OAuth redirect URIs (default: `http://localhost:<port>`)
* `--session-storage-backend`: Shared backend for OAuth state: `redis`, `valkey`, `postgresql`, `mongodb`, `disk`, or `memory`

**Observability options:**
* `--log-level`: Log level (default: `INFO`)
* `--log-format`: Console log format, `text` or `json` (default: `text`)
* `--otel-endpoint`: OTLP endpoint for log and span export. Setting it enables OTEL export
* `--otel-protocol`: OTLP protocol, `grpc` or `http` (default: `grpc`)
* `--otel-service-name`: `service.name` reported to OTEL (default: `feast-mcp`)

## Endpoints

For the HTTP transports, MCP is mounted at:

- `/mcp` for `http` and `streamable-http`
- `/sse` for `sse`

The server also exposes an unauthenticated health endpoint at `GET /health`.

## Configuration

Settings are resolved in priority order: CLI options, then environment variables, then the config file, then defaults. Note that environment variables outrank the config file, so a `FEAST_MCP_*` variable set in the environment will override the same setting in `feast_mcp.yaml`.

If `--config` is not passed, `feast_mcp.yaml` (then `feast_mcp.yml`) is read from the current working directory when present.

```yaml
server:
  transport: http           # stdio | http | streamable-http | sse
  host: 0.0.0.0
  port: 8000
  # workers: 4              # gunicorn; http transport only

# At least one of features.url / registry.url is required.
features:
  url: http://localhost:6566
registry:
  url: http://localhost:6572

timeout: 30

observability:
  level: INFO               # DEBUG | INFO | WARNING | ERROR
  format: json              # text | json
  # otel_endpoint: http://localhost:4317
  # otel_protocol: grpc     # grpc | http
  # otel_service_name: feast-mcp

# auth:
#   mode: oidc              # passthrough | oidc
#   discovery_url: https://keycloak.example.com/realms/feast/.well-known/openid-configuration
#   client_id: feast-mcp
#   client_secret: null
#   audience: null
#   base_url: https://mcp.example.com

# session_storage:
#   backend: redis          # redis | valkey | postgresql | mongodb | disk | memory
#   options:
#     url: redis://localhost:6379
```

### Environment variables

| Variable | Maps to |
| -------- | ------- |
| `FEAST_MCP_FEATURE_SERVER_URL` | `features.url` |
| `FEAST_MCP_REGISTRY_URL` | `registry.url` |
| `FEAST_MCP_TRANSPORT` | `server.transport` |
| `FEAST_MCP_WORKERS` | `server.workers` |
| `FEAST_MCP_TIMEOUT` | `timeout` |
| `FEAST_MCP_AUTH_MODE` | `auth.mode` |
| `FEAST_MCP_OIDC_DISCOVERY_URL` | `auth.discovery_url` |
| `FEAST_MCP_OIDC_CLIENT_ID` | `auth.client_id` |
| `FEAST_MCP_OIDC_CLIENT_SECRET` | `auth.client_secret` |
| `FEAST_MCP_OIDC_AUDIENCE` | `auth.audience` |
| `FEAST_MCP_BASE_URL` | `auth.base_url` |
| `FEAST_MCP_SESSION_STORAGE_BACKEND` | `session_storage.backend` |
| `FEAST_MCP_LOG_LEVEL` | `observability.level` |
| `FEAST_MCP_LOG_FORMAT` | `observability.format` |
| `FEAST_MCP_OTEL_ENDPOINT` | `observability.otel_endpoint` |
| `FEAST_MCP_OTEL_PROTOCOL` | `observability.otel_protocol` |
| `FEAST_MCP_OTEL_SERVICE_NAME` | `observability.otel_service_name` |

The OTEL variables fall back to the standard `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_PROTOCOL`, and `OTEL_SERVICE_NAME`, so existing OpenTelemetry tooling continues to work.

> **Note:** `server.host` and `server.port` have no environment equivalent. They can only be set on the command line or in the config file.

## Available tools

Tools are namespaced by the sub-server that provides them, for example `features_get_online_features` and `registry_list_projects`.

**Tools provided by the `features` sub-server:**
* `get_online_features`: Retrieve online feature values for a set of entities
* `search`: Vector similarity search against online document embeddings
* `list_vector_stores`, `get_vector_store`: List and inspect available vector stores
* `vector_store_search`: OpenAI-compatible vector store search
* `push`: Push features into the online or offline store
* `materialize`, `materialize_incremental`: Materialize features from the offline store to the online store
* `health`: Check the health of the Feast feature server

**Tools provided by the `registry` sub-server:**
* `list_projects`, `get_project`: Browse projects
* `list_entities`, `get_entity`: Browse entities
* `list_feature_views`, `get_feature_view`: List and inspect feature views
* `list_features`: List individual features (columns) across all feature views
* `list_feature_services`, `get_feature_service`: Browse feature services
* `list_data_sources`, `get_data_source`: Browse data sources
* `search_registry`: Full-text search across all registry objects
* `get_lineage`: Retrieve lineage relationships between registry objects

Upstream HTTP errors are raised as MCP tool errors rather than returned as tool results, so a `403` from Feast's permission model reaches the client as a failure instead of being passed to the model as feature data.

## Authentication

The MCP server does not enforce its own RBAC. It forwards the caller's bearer token to the upstream Feast servers, which validate the token and apply their own [permission model](../../getting-started/concepts/permission.md).

Two modes are supported:

* **`passthrough`** (default): connections are accepted without a token. A token supplied by the client is still forwarded upstream. Use this for development, or when the client already holds a valid Feast token.
* **`oidc`**: the server fronts an OIDC provider so that IDE clients such as Cursor and VS Code can complete a browser login flow. The resulting upstream token is forwarded on every tool call. Programmatic clients can also send OIDC provider tokens directly as bearer tokens, which are validated against the provider's JWKS. This mode requires `--oidc-discovery-url` and `--oidc-client-id`, typically the same values already configured as `auth.oidc_discovery_url` in `feature_store.yaml`.

> **Note:** With `oidc` and more than one replica, set `session_storage.backend` to a shared backend. The default OAuth state store is per-node and on disk, so a callback routed to a different replica than the authorize request will fail.

### Kubernetes authentication

There is no `kubernetes` value for `--auth-mode`. Kubernetes authentication is instead handled by the upstream Feast servers, using the `passthrough` mode: the client sends its Kubernetes Service Account or user token as a bearer token, the MCP server forwards it unchanged, and Feast validates it through the Token Access Review API and applies its [permission model](../auth/kubernetes_auth_setup.md).

```bash
curl -H "Authorization: Bearer $(kubectl create token my-service-account)" http://localhost:8000/mcp
```

Because the MCP server only relays the token, the caller must supply one. The MCP container does not read its own pod Service Account token, and the operator does not inject one for it.

> **Note:** The Feast Operator enables Kubernetes authentication by default on all deployed services. An MCP client that connects without a token will therefore receive `401 Unauthorized` errors raised from the upstream feature server or registry, even though the MCP server itself accepted the connection. Either supply a token, or set `spec.authz.noAuth: true` on the FeatureStore for development.

## Running with Docker

The image entrypoint is the server itself, and its default `CMD` is `--config /config/feast_mcp.yaml`:

```bash
make build-feast-mcp-docker VERSION=0.66.0
```

```bash
docker run --rm -p 8000:8000 -v "$PWD/feast_mcp.yaml:/config/feast_mcp.yaml:ro" feast-mcp:0.66.0
```

Passing arguments replaces the default `CMD`:

```bash
docker run --rm -p 8000:8000 feast-mcp:0.66.0 --feast-url http://host.docker.internal:6566 --transport http
```

See [sdk/python/feast/mcp/docker/README.md](https://github.com/feast-dev/feast/blob/master/sdk/python/feast/mcp/docker/README.md) for the build arguments and for deploying with the mcp-lifecycle-operator.

## Deploying with the Feast Operator

Setting `spec.services.mcpServer` adds a dedicated `feast mcp` container to the FeatureStore deployment, exposed on its own Service on port 8100. The operator sets `--host` and `--port` so that they match the generated Service. Every other setting comes from a `feast_mcp.yaml` supplied in a ConfigMap, which the operator mounts read-only at `/etc/feast/mcp`.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: sample-mcpserver-config
data:
  feast_mcp.yaml: |
    server:
      # host and port are set by the operator and override this file.
      # transport is honored from here.
      transport: http
    # The MCP container runs in the same pod as the online and registry
    # servers, so it reaches them over localhost.
    features:
      url: http://localhost:6566
    registry:
      url: http://localhost:6572
    timeout: 30
    observability:
      level: INFO
      format: json
---
apiVersion: feast.dev/v1
kind: FeatureStore
metadata:
  name: sample-mcpserver
spec:
  feastProject: my_project
  services:
    onlineStore:
      server: {}
    registry:
      local:
        server:
          restAPI: true
    mcpServer:
      config:
        configMapRef:
          name: sample-mcpserver-config
```

| Field | Type | Default | Description |
| ----- | ---- | ------- | ----------- |
| `config.configMapRef.name` | string | — | ConfigMap in the same namespace holding the MCP config |
| `config.configMapKey` | string | `feast_mcp.yaml` | Key in the ConfigMap holding the config content |

`mcpServer` also accepts the standard container settings shared by the other servers: `image`, `env`, `envFrom`, `imagePullPolicy`, `resources`, `nodeSelector`, and `logLevel`. Readiness is reported on the `McpServer` status condition, and the generated Service hostname on `status.serviceHostnames.mcpServer`.

A CEL validation rule enforces that at least one upstream is available: either `onlineStore` is present and not disabled, or `registry.local.server.restAPI` is `true`.

> **Note:** Operator-managed TLS is not yet supported for the MCP server. The `tls` field is ignored.

## Connecting an MCP client

For an HTTP transport, point the client at the MCP endpoint. For example, if the server runs at `http://localhost:8000`, use:

- `http://localhost:8000/mcp`

For a stdio client, let the client spawn the process:

```json
{
  "mcpServers": {
    "feast": {
      "command": "feast",
      "args": ["mcp", "--feast-url", "http://localhost:6566", "--registry-url", "http://localhost:6572"]
    }
  }
}
```

## Example

See [examples/feast_mcp_server](https://github.com/feast-dev/feast/tree/master/examples/feast_mcp_server) for an end-to-end walkthrough that starts a local feature store, runs the MCP server against it, and calls its tools from a client.

## Troubleshooting

- If you see `The standalone MCP server could not be imported`, the `mcp-server` extra is not installed. Install it with `pip install 'feast[mcp-server]'`.
- If the server exits with `At least one of --feast-url or --registry-url must be provided`, no upstream URL was resolved from the CLI, the environment, or the config file. If you expected the file to supply it, check that you are running from the directory that holds `feast_mcp.yaml`, or pass `--config` explicitly.
- If a setting in `feast_mcp.yaml` appears to be ignored, check for a `FEAST_MCP_*` environment variable, which takes precedence over the file.
- If the server rejects `--workers` with `SSE transport does not support multiple workers`, switch to `--transport http` or omit `--workers`.
- If a tool namespace is missing, its upstream URL was not configured. The `features_*` tools require `--feast-url`, and the `registry_*` tools require `--registry-url` together with a registry server started using `--rest-api`.
