# transformation-service

![Version: 0.51.0](https://img.shields.io/badge/Version-0.51.0-informational?style=flat-square) ![AppVersion: v0.51.0](https://img.shields.io/badge/AppVersion-v0.51.0-informational?style=flat-square)

Transformation service: to compute on-demand features

**Homepage:** <https://github.com/feast-dev/feast>

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| envOverrides | object | `{}` | Extra environment variables to set |
| image.pullPolicy | string | `"IfNotPresent"` | Image pull policy |
| image.repository | string | `"quay.io/feastdev/feature-transformation-server"` | Docker image for Transformation Server repository |
| image.tag | string | `"0.51.0"` | Image tag |
| nodeSelector | object | `{}` | Node labels for pod assignment |
| podLabels | object | `{}` | Labels to be added to Feast Serving pods |
| replicaCount | int | `1` | Number of pods that will be created |
| resources | object | `{}` | CPU/memory [resource requests/limit](https://kubernetes.io/docs/concepts/configuration/manage-compute-resources-container/#resource-requests-and-limits-of-pod-and-container) |
| secrets | list | `[]` | List of Kubernetes secrets to be mounted. These secrets will be mounted on /etc/secrets/<secret name>. |
| service.grpc.nodePort | string | `nil` | Port number that each cluster node will listen to |
| service.grpc.port | int | `6566` | Service port for GRPC requests |
| service.grpc.targetPort | int | `6566` | Container port serving GRPC requests |
| service.type | string | `"ClusterIP"` | Kubernetes service type |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
