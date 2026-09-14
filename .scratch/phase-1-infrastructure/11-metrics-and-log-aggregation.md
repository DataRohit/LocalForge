# 11: Metrics and log aggregation with provisioned dashboards

**What to build:** a working observability stack. A developer opens one UI and sees container, database, cache, and
broker metrics on dashboards that were provisioned from files, plus every container's logs queryable by label.

**Blocked by:** 04, 06, 07.

**Status:** done

- [x] Metrics collection scrapes every exporter and reports every target healthy on its targets page.
- [x] Two cache exporters run, one per Valkey instance, because the exporter supports only one password across a
      multi-target scrape and the two instances hold distinct credentials.
- [x] One database exporter covers both database nodes, which share a credential.
- [x] Database exporter credentials are supplied split out from the connection host, so the password is not
      embedded in a connection string.
- [x] The log collector is started with an explicit listen address, because its default binds to loopback and would
      be unreachable from outside its container while appearing perfectly healthy.
- [x] The log collector reads the Docker socket read-only and ships every container's logs with useful labels.
- [x] Log storage is queryable and a line emitted by a container is retrievable within thirty seconds. This also
      closes criterion 10 of ticket 05 and the request-log criterion of ticket 10: verify a line from
      `pgbackrest-pb2wj` and one from `traefik-tk2jp` are both retrievable.
- [x] Data sources and dashboards are provisioned from mounted files, so wiping the visualization volume loses
      nothing.
- [x] The visualization UI requires the generated admin credential.
- [x] Metric and log retention are bounded from the environment so the stack cannot fill the disk.
