# Kubernetes mapping

Authoritative for: how each Compose service maps to a Kubernetes primitive, stateful versus stateless
classification, and what changes at scale.

Kubernetes is **not** part of the development or testing workflow. Nothing here is deployed. This document exists so
that the Compose stack is designed in a shape that can move to Kubernetes later without a redesign, and so that the
executing agent does not write code that would have to be deleted at that point.

## 1. The rule that keeps this possible

**No manual scaling or replica logic may exist in application code or in Compose.**

Concretely, the following are forbidden in this platform:

| Forbidden | Why | What to do instead |
|---|---|---|
| A `replicas:` count in a Compose service, or `docker compose up --scale` baked into a script | Encodes a scaling decision in the wrong layer | Run one container per service; let the orchestrator decide the count later |
| Code that asks "am I worker 3 of 5?" | Requires stable ordinal identity that no stateless service should depend on | Have every instance behave identically |
| A singleton guard implemented by a hostname or container-name check | Breaks the moment the name changes | Use a lease, a broker queue, or a dedicated single-replica Deployment |
| Application-level shard assignment or hash-based partitioning of work | Duplicates what a queue and an orchestrator already do | Let the broker distribute |
| Sticky sessions implemented in application state | Ties a user to a pod | Sessions live in the Valkey cache; any instance can serve any request |
| Scheduling loops implemented with `time.sleep` inside a worker | Cannot be scaled or made highly available | `celery-beat` for app schedules, `CronJob` for platform schedules |

Design consequences already applied to the Compose stack:

1. Django holds no local state. Sessions and cache go to `valkey-cache-vc5tn`. Uploads go to `seaweedfs-sw9cr`, not
   to a local `MEDIA_ROOT`. Static files are collected into a volume at build time.
2. The channel layer is external (`valkey-channels-vh8dm`), so two ASGI processes can already serve the same
   WebSocket group. This is the property that makes horizontal scaling work later.
3. Migrations run in an entrypoint that is separable from the server process, so it can become an `initContainer`
   or a `Job` without touching application code.
4. Every service already has a real health probe (see [service-inventory.md](./service-inventory.md) Section 2.1),
   which becomes a `readinessProbe` verbatim.
5. Every configuration value is an environment variable, so a `ConfigMap` or `Secret` substitutes for `env_file`
   with no code change.

## 2. Service-by-service mapping

`SS` = StatefulSet, `D` = Deployment, `DS` = DaemonSet, `CJ` = CronJob.

| Compose service | Primitive | Stateful? | Kubernetes Service | Storage | Notes |
|---|---|---|---|---|---|
| `traefik-tk2jp` | `D` + `IngressClass` | no | `LoadBalancer` | none | Becomes the ingress controller. Docker label routing becomes `Ingress` or `IngressRoute` objects. RBAC for the Kubernetes provider replaces the Docker socket. |
| `django-uv5n2` | `D` | no | `ClusterIP` behind `Ingress` | none | The scale target. `HorizontalPodAutoscaler` on CPU and on request rate. `PodDisruptionBudget` with `minAvailable: 1`. |
| `postgres-pg3ka` | `SS`, 1 replica | **yes** | headless `ClusterIP` | `PVC` from `volumeClaimTemplates`, `ReadWriteOnce` | At real scale this is replaced by an operator — CloudNativePG or the Zalando operator — which owns primary election. Do not hand-roll failover. |
| `postgres-replica-pg6vy` | part of the same `SS` | **yes** | second headless `Service` selecting non-primary pods | `PVC` per pod | In Compose this is a separate container. Under an operator it becomes replica `N` of one cluster resource, and the Django `replica` alias points at the read-only Service. |
| `pgbackrest-pb2wj` | `CJ` | writes to state | none | `PVC`, `ReadWriteMany` or an object-store repo | The schedule moves from the container's own loop to `spec.schedule`. This is why the backup loop lives in a script and not in application code. |
| `pgadmin-pa7fe` | `D`, 1 replica | trivial | `ClusterIP` | small `PVC` | Development-only. Not deployed in a real cluster; listed for completeness. |
| `valkey-cache-vc5tn` | `SS`, 1 replica | semi | headless `ClusterIP` | `PVC` | Cache data is reconstructible. At scale this becomes a Valkey Cluster or a replicated Sentinel setup. |
| `valkey-channels-vh8dm` | `SS`, 1 replica | semi | headless `ClusterIP` | `PVC` | Must stay a separate workload from the cache. Eviction on the channel layer loses WebSocket messages. |
| `rabbitmq-rq4sx` | `SS`, 3 replicas at scale | **yes** | headless `ClusterIP` + a client `ClusterIP` | `PVC` per pod | Quorum queues need stable network identity, which is exactly what a StatefulSet provides and a Deployment does not. |
| `celery-worker-cw8rt` | `D` | no | none | none | Second scale target. `HorizontalPodAutoscaler` driven by queue depth via KEDA, not by CPU. |
| `celery-beat-cb4hq` | `D`, **exactly 1 replica** | holds a schedule lock | none | none | Two beat instances double-fire every periodic task. Keep `replicas: 1` with `strategy: Recreate`. This is the one place a replica count is legitimately fixed, and it is fixed in the manifest, never in code. |
| `flower-fl9zd` | `D`, 1 replica | no | `ClusterIP` | none | Development-only. |
| `mailpit-mp6gb` | `D`, 1 replica | no | `ClusterIP` | `emptyDir` | Development-only. A real cluster uses a real relay. |
| `seaweedfs-sw9cr` | `SS` per component | **yes** | headless per component | `PVC` per volume server | The all-in-one container splits into master, volume, filer, and S3 gateway workloads. Plan for this by addressing SeaweedFS only through its S3 endpoint variable. |
| `prometheus-pm5db` | `SS`, 1 replica | **yes** | `ClusterIP` | `PVC` | Or the Prometheus Operator's `Prometheus` CR. Static scrape config becomes `ServiceMonitor` / `PodMonitor`. |
| `grafana-gf7qv` | `D`, 1 replica | semi | `ClusterIP` | `PVC` or external database | Provisioning files become `ConfigMap`s, which is why they are already files on disk rather than clicked-in settings. |
| `loki-lk3ny` | `SS` | **yes** | `ClusterIP` | `PVC`, or object storage in scalable mode | Single-binary mode becomes read/write/backend components. |
| `alloy-al6wz` | **`DS`** | no | none | `hostPath` for positions | One per node. This is the only workload in the stack that is a DaemonSet, and it is the reason Alloy reads the Docker socket in Compose — that becomes the container-runtime log path on a node. |
| `cadvisor-cv8mh` | **`DS`** | no | `ClusterIP` for scraping | `hostPath`, read-only | In most clusters this is dropped entirely: kubelet already exposes cAdvisor metrics on `/metrics/cadvisor`. |
| `postgres-exporter-pe4rk` | `D` or sidecar | no | `ClusterIP` | none | Becomes a sidecar in the database pod under an operator. |
| `valkey-cache-exporter-ve7ts` | `D` or sidecar | no | `ClusterIP` | none | Same. Two exporters exist because the two Valkey instances have distinct passwords and `redis_exporter` supports only one password across multi-target scrapes. |
| `valkey-channels-exporter-vx4nq` | `D` or sidecar | no | `ClusterIP` | none | Same. Collapses into one exporter only if the instances ever share a credential, which the secret rules forbid. |

Testing-stack services do not map to Kubernetes. They are ephemeral CI fixtures; the equivalent in a cluster is a
per-job namespace, not a permanent workload.

## 3. Stateful versus stateless summary

| Classification | Services | Consequence |
|---|---|---|
| Stateless, freely scalable | `django-uv5n2`, `celery-worker-cw8rt`, `traefik-tk2jp` | Deployment + HPA. Any instance can serve any request or task. |
| Stateless, deliberately pinned to one replica | `celery-beat-cb4hq` | Deployment with `replicas: 1` and `strategy: Recreate`. Correctness, not capacity. |
| Stateful, needs stable identity and storage | `postgres-pg3ka`, `postgres-replica-pg6vy`, `rabbitmq-rq4sx`, `seaweedfs-sw9cr`, `loki-lk3ny`, `prometheus-pm5db` | StatefulSet or an operator CR. Never a Deployment. |
| Semi-stateful, reconstructible | `valkey-cache-vc5tn`, `valkey-channels-vh8dm`, `grafana-gf7qv` | StatefulSet for convenience; data loss is recoverable. |
| Node-scoped agents | `alloy-al6wz`, `cadvisor-cv8mh` | DaemonSet. Replica count is a function of cluster size, never a chosen number. |
| Scheduled work | `pgbackrest-pb2wj` | CronJob. |
| Development-only, never deployed | `pgadmin-pa7fe`, `flower-fl9zd`, `mailpit-mp6gb` | Excluded from any cluster manifest. |

## 4. Configuration and secret mapping

| Compose mechanism | Kubernetes equivalent | Migration friction |
|---|---|---|
| `env_file: .env.development` | `ConfigMap` via `envFrom` for non-secrets | none, variable names are identical |
| Secret variables in the same file | `Secret` via `envFrom`, or External Secrets Operator | none, because nothing reads a file path directly |
| `.env.*.sops` encrypted at rest | SOPS-encrypted manifests with Flux or Argo CD, or Sealed Secrets | none, the same age key model applies |
| Named volume | `PersistentVolumeClaim` | none, provided nothing depends on the volume's *name* |
| Named network with `internal: true` | `NetworkPolicy` with a default-deny egress rule | The four-network zoning translates to four policy groups |
| `depends_on: service_healthy` | `initContainers` plus `readinessProbe` | The probes already exist and are reused verbatim |
| Docker label routing on Traefik | `Ingress` or Traefik `IngressRoute` CRDs | Labels become annotations or CRs |
| `container_name` | Pod names are generated; the **Service** name carries identity | The five-character IDs move to Service names. Do **not** try to force pod names to match. |

That last row is the one place the naming convention meets a hard Kubernetes constraint: pod names always carry a
generated suffix. The registry IDs therefore live on Services, Deployments, and StatefulSets, and pods inherit them
as a prefix. This is a naming translation, not a redesign, and it does not affect any code.

## 5. What actually changes at scale

| Concern | Compose today | Kubernetes later | Does app code change? |
|---|---|---|---|
| Web capacity | one `django-uv5n2` container | HPA on the Deployment | no |
| Worker capacity | one `celery-worker-cw8rt` container | KEDA scaling on RabbitMQ queue depth | no |
| Database HA | one primary, one standby, no failover | CloudNativePG or similar, automated failover | no — Django keeps two `DATABASES` aliases; the Service behind `replica` changes |
| Broker HA | single node | 3-node quorum queues | no — only `CELERY_BROKER_URL` changes |
| Channel layer | single Valkey | Valkey Cluster or Sentinel | no, if `RedisPubSubChannelLayer` is configured with a host list |
| Object storage | SeaweedFS all-in-one | split components, or a real S3 | no — only `S3_ENDPOINT_URL` changes |
| Log collection | one Alloy container reading the Docker socket | DaemonSet reading node log paths | no |
| Backups | container-internal schedule loop | CronJob | no, because the schedule lives in a script, not in Django |
| Ingress | Traefik reading Docker labels | Traefik reading Ingress objects | no |
| Secrets | `.env` files | Secrets / External Secrets | no, because names are identical |

The intended reading of this table: **the "does app code change?" column is `no` on every row.** If a future change
would make any row `yes`, that change is the wrong shape and should be reconsidered before it is written.

## 6. Local Kubernetes for validation only

If and when the mapping is validated, use `kind` v0.33.0 (2026-08-26), which supports Kubernetes v1.34–v1.37 and
defaults to v1.37.0 — matching the `kubectl` v1.37.0 already on this machine. Its cadence is healthier than `k3d`
v5.9.0, whose only release since February 2025 was 2026-06-02, and it is lighter than `minikube` v1.39.0
(2026-09-01, tested against v1.28–v1.37, now defaulting to containerd). Verified 2026-09-13. Pin the node image by
digest: kind's own release notes disagree with themselves about the default Kubernetes version.

This is explicitly **out of scope for the build phase**. No cluster is created, no manifest is applied, and
`kubectl` is checked only so the table in [../build/prerequisites.md](../build/prerequisites.md) is complete.
