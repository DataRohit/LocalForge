# 11: Metrics and log aggregation with provisioned dashboards

**What to build:** a working observability stack. A developer opens one UI and sees container, database, cache, and
broker metrics on dashboards that were provisioned from files, plus every container's logs queryable by label.

**Blocked by:** 04, 06, 07.

**Status:** ready-for-agent

- [ ] Metrics collection scrapes every exporter and reports every target healthy on its targets page.
- [ ] Two cache exporters run, one per Valkey instance, because the exporter supports only one password across a
      multi-target scrape and the two instances hold distinct credentials.
- [ ] One database exporter covers both database nodes, which share a credential.
- [ ] Database exporter credentials are supplied split out from the connection host, so the password is not
      embedded in a connection string.
- [ ] The log collector is started with an explicit listen address, because its default binds to loopback and would
      be unreachable from outside its container while appearing perfectly healthy.
- [ ] The log collector reads the Docker socket read-only and ships every container's logs with useful labels.
- [ ] Log storage is queryable and a line emitted by a container is retrievable within thirty seconds.
- [ ] Data sources and dashboards are provisioned from mounted files, so wiping the visualization volume loses
      nothing.
- [ ] The visualization UI requires the generated admin credential.
- [ ] Metric and log retention are bounded from the environment so the stack cannot fill the disk.
