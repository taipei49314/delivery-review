# Pinned container syscall profile

`moby-default.json` is the unmodified Apache-2.0 default seccomp profile from
[moby/profiles at 4b8214fc4971e06172938a1f1554ccff6647ee80](https://github.com/moby/profiles/blob/4b8214fc4971e06172938a1f1554ccff6647ee80/seccomp/default.json).
The upstream license is included as `LICENSE`; upstream has no `NOTICE` file at
that commit. `source.json` records exact source URLs, byte sizes and SHA-256.

The profile denies unknown system calls with `EPERM` and allows enumerated
calls, including argument- and capability-constrained rules. All container
capabilities are separately dropped. The host verifies its exact bytes, passes
an explicit copy to Docker and checks the daemon's effective configuration;
the guest checks seccomp mode, privilege/capability and resource boundaries
before preparation and test execution. A mode-2 flag alone is insufficient.

This is a fixed reviewed dependency, not a dynamic download. Changing the
profile requires a new producer qualification. Container isolation still
depends on the trusted host kernel and Docker daemon.
