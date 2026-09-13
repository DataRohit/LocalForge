# 24: Email backend integration

**What to build:** mail sent by the application arriving in the local capture service, visible in its UI and
assertable through its API, with the testing environment defaulting to an in-memory backend so the suite needs no
mail container.

**Blocked by:** 09, 17.

**Status:** ready-for-agent

- [ ] The development environment sends through SMTP to the capture service, with host, port, and sender from the
      environment.
- [ ] The testing environment defaults to the in-memory backend, and the SMTP round-trip test runs only under the
      dedicated profile that starts the capture service.
- [ ] Sending a message in development makes it retrievable through the capture service's API with the expected
      recipient and subject.
- [ ] Email templates render both a plain-text and an HTML part, and the plain-text part is not empty.
- [ ] The sender address and the site name used in links come from the environment, so generated links point at
      the right host in each environment.
- [ ] Failures to send are logged and do not raise into a request path that would return a server error to a user.
- [ ] Integration tests assert on the captured message rather than on the framework's own outbox when running
      under the profile.
- [ ] Tests that assert on captured mail clear the capture service first, so they pass under the parallel runner.
