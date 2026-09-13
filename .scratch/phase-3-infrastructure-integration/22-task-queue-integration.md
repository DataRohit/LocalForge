# 22: Task queue integration

**What to build:** Django able to enqueue a task onto the broker and read its result back, with the application
object discovered automatically so tasks defined in any app are registered.

**Blocked by:** 07, 17.

**Status:** ready-for-agent

- [ ] A task queue application object is created and initialised with the project package, with autodiscovery of
      tasks across installed apps.
- [ ] The broker URL and the result backend are composed from the environment; neither is a literal.
- [ ] The result backend uses the reserved logical database that is not the cache database.
- [ ] Serialization is restricted to a safe content type; pickle is not accepted.
- [ ] Task acknowledgement happens after completion so a worker crash redelivers rather than loses work, and the
      consequence — tasks must be idempotent — is stated in the task base class docstring.
- [ ] Time limits and a retry policy with bounded backoff are configured, so a failing task cannot retry forever.
- [ ] A round-trip integration test enqueues a task against the real broker and asserts the result.
- [ ] The testing environment runs tasks eagerly by default, and the one test that needs a real broker overrides
      that explicitly.
- [ ] Task failures are logged with enough context to identify the task and its arguments, without logging
      credentials.
- [ ] If the queue package fails on this Python version, the escape in `docs/adr/0016-accept-release-lag.md` is
      applied and the outcome recorded there.
