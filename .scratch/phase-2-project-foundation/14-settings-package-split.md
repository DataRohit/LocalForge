# 14: Environment-aware settings package

**What to build:** replace the single settings module with a package that has a shared base and one module per
environment, so development and testing differ by declaration rather than by conditional logic, and every value
comes from the environment.

**Blocked by:** 13.

**Status:** ready-for-agent

- [ ] The single settings module becomes a package with a base module plus a development module and a testing
      module, each importing the base.
- [ ] Every value that differs between environments, or that is a credential or a hostname, is read from the
      environment through the parsing library. No literal credential remains in any settings module.
- [ ] A required variable that is missing raises at startup with a message naming the variable. No silent default
      stands in for a required value.
- [ ] The development module enables debug; the testing module disables it, so tests never depend on debug
      behaviour.
- [ ] Every tooling reference to the old settings path is updated in the same change: the test runner's settings
      module, the type stub plugin's settings module, and the linter's per-file ignore path. The repository's own
      quality gate passes afterward.
- [ ] Secret-like settings are marked so the linter's hardcoded-password rules apply to the package.
- [ ] Password hashing prefers a memory-hard algorithm, with its backing package installed.
- [ ] Timezone support is enabled and the timezone is set from the environment.
- [ ] Logging is configured to emit structured records to stdout so the log collector can ship them.
- [ ] A smoke test imports each environment module and asserts the settings that must differ actually differ.
