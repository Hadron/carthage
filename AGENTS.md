# AGENTS

## What this AGENTS.md should contain

Keep this file focused on:
- How to initialize an environment.
- How to run tests in Codex CLI 
- How to run tests in cloud environments 
- Expectations for PRs and tests.
- Do not add transient test failure notes or debugging details; record those in PR summaries or issues instead.

## What to ignore

- Makefile: our standard processes do not use it.

## Environment setup

To set up a fresh checkout of Carthage, follow these steps. Setting up a fresh setup is expensive; only do this if explicitly instructed.
These steps prefer OS packages so `bin/carthage install_dependencies` can run successfully.

1. Install system packages (Ubuntu/Debian example):
   ```sh
   sudo apt update
   sudo apt install -y \
     ansible \
     btrfs-progs \
     fai-server \
     fai-setup-storage \
     git \
     genisoimage \
     kpartx \
     python3-dev \
     python3-dateutil \
     python3-lmdb \
     python3-mako \
     python3-netifaces \
     python3-pip \
     python3-pytest \
     python3-sh \
     python3-yaml \
     podman \
     qemu-system-x86 \
     qemu-utils \
     socat \
     sshfs \
     systemd-container \
     xorriso
   ```
2. Install Carthage dependencies via OS packages/plugins:
   ```sh
   sudo PYTHONPATH=$(pwd) ./bin/carthage install_dependencies
   ```

## Running tests

> **Note:** Most tests require elevated privileges. In Codex CLI runs, that means escaping the sandbox (privileged mode) rather than using `sudo`. If you see a "pty exhausted" error, elevated privileges are almost always the fix.

## Running tests in Codex CLI

When running in Codex CLI, use privileged mode (escape the sandbox) to avoid PTY exhaustion. Use a command that skips tests that require `sudo` or host-level container/VM capabilities (this is the set verified in Codex Cloud):

```sh
PYTHONPATH=$(pwd) pytest -v \
  --carthage-config=build_test/authorized.yml \
  -k "not no_rootless and not test_pki and not requires_podman_pod and not podman and not container and not vm and not image_unpack and not become" \
  tests
```

## Running tests in cloud environments

In a cloud environment with `sudo` available and the OS packages installed, you can run a broader slice of the suite. This command is currently green in Codex Cloud:

```sh
sudo PYTHONPATH=$(pwd) pytest -v \
  --carthage-config=build_test/authorized.yml \
  -k "not test_pki and not requires_podman_pod and not podman and not container and not vm and not image_unpack and not become" \
  tests
```

As more tests become green in the cloud environment, remove `-k` filters to expand coverage.


## PR and testing expectations

- When making code changes in Codex Cloud, run tests (at least one of the commands above) and report results.
- Document any test failures in the PR summary with the exact command used.
- For local changes running tests is also desired unless instructions imply quick turn around.

