# Feast MCP server image

Packages the standalone MCP server (`feast mcp`) as a container whose
entrypoint *is* the server.

## Build

A thin wrapper over the published `feature-server` image. That image installs
`feast[minimal]`, which pulls `mcp-server-otel` — FastMCP and the OTLP
exporters — so everything `feast mcp` needs is already present. This image only
sets the entrypoint, and inherits the multi-arch build, the UBI base, and the
arbitrary-uid permission setup.

```bash
make build-feast-mcp-docker VERSION=0.66.0
```

The base defaults to the feature-server image at the same `VERSION`. To build
against a published tag instead:

```bash
make build-feast-mcp-docker VERSION=0.66.0 FEAST_MCP_BASE_TAG=0.65.0
```

`FEAST_MCP_BASE_IMAGE` overrides the repository, for downstream rebuilds.
Directly:

```bash
docker buildx build -f sdk/python/feast/mcp/docker/Dockerfile --build-arg BASE_TAG=0.65.0 -t feast-mcp:0.66.0 .
```

The base tag must be a feature-server build whose `minimal` extra already
includes `mcp-server-otel`; older tags have no `feast mcp` command.

## Configuration

Everything — transport, host, port, and the upstream Feast URLs — comes from
`feast_mcp.yaml`. The image's default `CMD` is `--config
/config/feast_mcp.yaml`, so mount the file there. Start from
[sample.feast_mcp.yaml](sample.feast_mcp.yaml).

Resolution order is CLI > environment > file > defaults. **Environment outranks
the file**, so avoid setting `FEAST_MCP_*` variables alongside a mounted config
— `FEAST_MCP_TRANSPORT` in a CR's `config.env` would silently override
`server.transport` in the yaml. The image deliberately bakes in no `FEAST_MCP_*`
defaults for that reason.

`--host` and `--port` have no environment equivalent at all; they come from the
yaml or the command line only.

## Run locally

```bash
docker run --rm -p 8000:8000 -v "$PWD/feast_mcp.yaml:/config/feast_mcp.yaml:ro" feast-mcp:0.66.0
```

To point at a different path, or to skip the file entirely, pass your own
arguments — they replace the default `CMD`:

```bash
docker run --rm -p 8000:8000 feast-mcp:0.66.0 --feast-url http://host.docker.internal:6566 --transport http
```

For a stdio client, set `server.transport: stdio` in the yaml (or pass
`--transport stdio`) and run with `-i`.

## Deploying with the mcp-lifecycle-operator

The [mcp-lifecycle-operator](https://github.com/kubernetes-sigs/mcp-lifecycle-operator)
`MCPServer` CRD exposes only `config.arguments` — there is no `command`
override — which is why the entrypoint is baked into the image rather than set
in the pod spec.

```yaml
apiVersion: mcp.x-k8s.io/v1alpha1
kind: MCPServer
metadata:
  name: feast-mcp
spec:
  source:
    type: ContainerImage
    containerImage:
      ref: quay.io/feastdev/feast-mcp:0.66.0
  config:
    # Must match server.port in feast_mcp.yaml.
    port: 8000
    path: /mcp
    storage:
      # The image's default CMD reads /config/feast_mcp.yaml. Leaving
      # config.arguments unset keeps that default in place.
      - path: /config
        source:
          type: ConfigMap
          configMap:
            name: feast-mcp-config
      # Python needs a writable temp dir under readOnlyRootFilesystem.
      - path: /tmp
        permissions: ReadWrite
        source:
          type: EmptyDir
  mcp:
    stateless: true
```

Four things that are easy to get wrong:

- **`spec.config.port` must match `server.port` in the yaml.** The operator uses
  its value for the Service and the container port, but only the yaml decides
  what the process actually binds.
- **Health probes must not use `httpGet: /healthz`.** The server exposes only
  `/mcp` (or `/sse`); there is no health route. Use a `tcpSocket` probe.
- **The MCP server runs in its own Deployment**, not alongside the feature
  server, so `features.url` has to be the Feast Service's cluster DNS name.
  `localhost` only works in the Feast-operator sidecar model.
- **`mcp.stateless: true` drops the Service's ClientIP affinity.** Fine for
  `auth.mode: passthrough`, but with `auth.mode: oidc` and more than one replica
  the OAuth authorize/callback state needs a shared `session_storage` backend,
  or a callback can land on a replica that never saw the authorize.

If you would rather keep the config path out of the image, set it explicitly —
`config.arguments` replaces the default `CMD`:

```yaml
    arguments: [--config, /etc/mcp-config/feast_mcp.yaml]
    storage:
      - path: /etc/mcp-config
        source:
          type: ConfigMap
          configMap:
            name: feast-mcp-config
```

A `--config` path that does not exist raises `FileNotFoundError`, so mount the
ConfigMap in the same CR that names the flag.
