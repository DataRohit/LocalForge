# 03: Compose foundation with explicit networks and volumes

**What to build:** the Compose skeleton both environments build on. Bringing it up creates every named network and
volume with the registry names, on two isolated project namespaces that can run at the same time, with no
Docker-generated defaults anywhere.

**Blocked by:** 02.

**Status:** done

- [x] Development and testing use distinct Compose project names, so both stacks can run concurrently without
      colliding on names, volumes, or host ports.
- [x] Every network from the registry exists, is explicitly declared, and carries the zone naming scheme.
- [x] Every network except the edge zone is internal, so nothing on it can reach the internet.
- [x] Every volume from the registry is explicitly named and declared; no anonymous volumes are created.
- [x] No service relies on the Compose default network, and no `_default` network is created for either project.
- [x] The obsolete top-level version key is absent and the project name is set by the supported key.
- [x] Environment values load only through per-environment env files; no literal value is set inline.
- [x] Bringing the stack up and down leaves no orphaned networks or volumes belonging to this project.
