---
status: accepted
date: 2026-09-13
---

# A dedicated access zone, because internal networks cannot publish host ports

Every service that must be reachable from the host joins a non-internal **access zone** in addition
to its internal service zone. Services that need no host access stay on internal zones alone and
remain provably unable to reach the internet.

**`traefik-tk2jp` is the exception, and joins no access zone.** Its own zone, `edge-net-ne2vk`, is
already non-internal, because the proxy's purpose is to carry traffic in from outside. Adding an
access zone to it would attach a second non-internal network for no gain, and would break the
requirement that the proxy sit on the edge network alone. Corrected 2026-09-14: this record was
first applied to every publishing service uniformly, which put the proxy on two.

This reverses a claim in [../platform/conventions.md](../platform/conventions.md) Section 2.4, which
stated that on an internal network "the gateway IP remains reachable". For published ports it is
not, and the failure is silent.

## The evidence

Measured on this machine on 2026-09-13, Docker Engine 29.7.2 with Compose v5.5.1.

A container publishing `55432:5432` while attached only to `data-net-nd9pc`, which carries
`internal: true`:

```console
$ docker ps --format "{{.Ports}}"
5432/tcp

$ docker exec probe-pg pg_isready -U postgres
/var/run/postgresql:5432 - accepting connections
```

The port mapping is **dropped without a warning, an error, or a non-zero exit**. The container is
healthy, the server accepts connections inside the network, and `Test-NetConnection 127.0.0.1
-Port 55432` fails. Binding the publication explicitly to `127.0.0.1` changes nothing.

Attaching one non-internal network restores it immediately:

```console
$ docker ps --format "{{.Ports}}"
0.0.0.0:55432->5432/tcp, [::]:55432->5432/tcp
```

Disabling NAT on a non-internal bridge was tested as a way to keep publishing while blocking egress,
and does **not** work here:

```console
$ docker network create --driver bridge \
    --opt com.docker.network.bridge.enable_ip_masquerade=false probe-nomasq
$ docker exec probe-nm bash -c "cat < /dev/null > /dev/tcp/104.20.23.154/80 && echo OUTBOUND_OK"
OUTBOUND_OK
```

Docker Desktop runs the engine inside a virtual machine that performs its own address translation,
so a bridge-level masquerade setting does not govern egress. There is no configuration that gives a
container a published host port and no route off the machine.

## Why this had to be resolved rather than absorbed

Three requirements collided, and all three are load-bearing:

1. [AGENTS.md](../../AGENTS.md) rule 2 — the stack runs with no internet access.
2. [../platform/service-inventory.md](../platform/service-inventory.md) Section 1 — sixteen
   development services publish host ports, which is what every dashboard ticket delivers.
3. [../platform/service-inventory.md](../platform/service-inventory.md) Section 4.2 — the suite must
   pass in host mode, reaching an **entirely internal** testing stack through published ports.
   AGENTS.md states that container mode passing alone is not a pass.

Left alone, the platform would have reached the phase 5 gate with twenty-five ports reported
`CLOSED` and no indication why, because nothing in the toolchain reports a dropped publication.

## The decision

Add one access zone per environment. A service joins it **only** if the inventory publishes a host
port for it; everything else keeps its internal zones alone.

| Zone | Environment | `internal` |
|---|---|---|
| `access-net-ha4mz` | development | no |
| `access-net-ht6pn` | testing | no |

The offline constraint is kept where it can be kept, and where it is given up the reason is now a
recorded trade-off rather than a silent failure:

- `celery-worker-cw8rt`, `celery-beat-cb4hq`, and `pgbackrest-pb2wj` publish nothing, so they stay
  internal-only and genuinely cannot reach the internet.
- Every service zone keeps `internal: true`, so a service that later stops publishing returns to
  being provably offline by dropping one network from its list.
- The constraint that actually matters operationally — that `docker compose up` succeeds with no
  external network, per [../build/prerequisites.md](../build/prerequisites.md) Section 4 — is
  unaffected, because it is a property of the images and configuration, not of routing.

## Considered options

**Make every zone non-internal.** One line, and it discards the enforcement mechanism wholesale,
including for the three services that never needed host access. Rejected: it converts a precise
trade-off into a blanket one.

**Publish nothing and reach services through `docker exec`.** Preserves the offline guarantee
completely and is viable for development dashboards. Rejected because it cannot satisfy host-mode
testing, which is mandatory and needs real published ports on the testing stack.

**Disable bridge masquerading.** Measured above. Does not work on this engine.

**A host-side proxy forwarding into the internal networks.** Would work, and costs a container whose
only job is to undo a Docker limitation, plus a second place where every port is written down.
Rejected on weight.

## Consequences

- Audit 6.5 in [../platform/service-inventory.md](../platform/service-inventory.md) changes meaning:
  it proves a service is offline only for services not on an access zone. It is retargeted at
  `pgbackrest-pb2wj`, which publishes nothing.
- A service that gains a published port must gain the access zone in the same change, or its port
  will be dropped silently. The convention audit checks the pairing.
