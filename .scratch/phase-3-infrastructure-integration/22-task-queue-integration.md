# 22: Task queue integration

**What to build:** Django able to enqueue a task onto the broker and read its result back, with the application
object discovered automatically so tasks defined in any app are registered.

**Blocked by:** 07, 17.

**Status:** done

- [x] A task queue application object is created and initialised with the project package, with autodiscovery of
      tasks across installed apps.
- [x] The broker URL and the result backend are composed from the environment; neither is a literal.
- [x] The result backend uses the reserved logical database that is not the cache database.
- [x] Serialization is restricted to a safe content type; pickle is not accepted.
- [x] Task acknowledgement happens after completion so a worker crash redelivers rather than loses work, and the
      consequence — tasks must be idempotent — is stated in the task base class docstring.
- [x] Time limits and a retry policy with bounded backoff are configured, so a failing task cannot retry forever.
- [x] A round-trip integration test enqueues a task against the real broker and asserts the result.
- [x] The testing environment runs tasks eagerly by default, and the one test that needs a real broker overrides
      that explicitly.
- [x] Task failures are logged with enough context to identify the task and its arguments, without logging
      credentials.
- [x] If the queue package fails on this Python version, the escape in `docs/adr/0016-accept-release-lag.md` is
      applied and the outcome recorded there.
