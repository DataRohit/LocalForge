FROM docker.io/library/postgres:18.6

RUN apt-get update \
    && apt-get install --no-install-recommends --assume-yes pgbackrest \
    && rm -rf /var/lib/apt/lists/*

RUN install --directory --owner postgres --group postgres --mode 0750 \
    /var/lib/pgbackrest /var/log/pgbackrest /var/spool/pgbackrest /etc/pgbackrest /var/run/pgsocket

COPY --chown=postgres:postgres docker/pgbackrest/pgbackrest.conf /etc/pgbackrest/pgbackrest.conf
COPY --chmod=0755 scripts/pgbackrest_entrypoint.sh /usr/local/bin/pgbackrest_entrypoint.sh
