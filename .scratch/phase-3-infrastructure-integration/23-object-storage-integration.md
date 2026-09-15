# 23: Object storage integration

**What to build:** file uploads landing in the S3-compatible bucket rather than on a container filesystem, so any
application instance can serve any file and nothing is lost when a container is replaced.

**Blocked by:** 08, 17.

**Status:** done

- [x] The default file storage backend points at the S3 endpoint, with endpoint, bucket, region, and credentials
      from the environment.
- [x] Static files and uploaded media are configured separately, so collected static assets and user uploads do not
      share a location.
- [x] No writable local media directory remains; an instance holds no file state.
- [x] Uploaded objects are private by default; public read is not enabled on the bucket.
- [x] An integration test uploads a file through the storage API, reads it back, and asserts the bytes are
      identical.
- [x] A test covers overwrite behaviour for a colliding filename, so the outcome is a decision rather than an
      accident.
- [x] A test covers deletion.
- [x] The storage client choice follows `docs/adr/0016-accept-release-lag.md`: the maintained library is tried
      first, and if its gate fails, the thin first-party backend on the S3 client is written instead and the
      outcome recorded in that decision record.
- [x] Storage is reachable only over the internal network from the application; the endpoint variable is the only
      thing that changes to point at a different implementation.
