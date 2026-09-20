# 08: S3-compatible object storage

**What to build:** a running S3-compatible endpoint with a pre-created bucket and credentials, so uploads have
somewhere to go. A developer can list the bucket with any S3 client and browse stored files in a web UI.

**Blocked by:**

- [03](03-compose-foundation-networks-volumes.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build prerequisites](../../docs/build/prerequisites.md)
- [Build plan](../../docs/build/plan.md)
- [Platform conventions](../../docs/platform/conventions.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Architecture decision index](../../docs/adr/README.md)

**Status:** done

- [x] The service runs all components in one container with the registry name and its own named volume.
- [x] The two extra catalog services that bind by default are explicitly disabled, because one of their ports
      collides with a conventional exporter port and neither service is used.
- [x] Access key and secret are supplied from the environment through an identities configuration, not baked into
      the image.
- [x] A seeding step creates the media bucket and is idempotent: running it against an existing bucket succeeds.
- [x] The health check uses the endpoint that exists on the component being checked, rather than assuming one path
      works everywhere.
- [x] The S3 API, the master UI, and the file browser UI are each reachable from the host on the documented ports.
- [x] Uploading an object with an S3 client and reading it back returns identical bytes.
- [x] No anonymous access is permitted to the bucket.
