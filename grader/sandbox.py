"""
Bubblewrap sandbox for student programs. Standard library only.

on_submission.py runs untrusted student code. Its process limits (time,
memory, output size) stop runaway programs, not hostile ones. Inside this
sandbox, each run also gets:

- no network (a fresh network namespace with nothing in it)
- its own process list, so the grader's processes, and their environment,
  where the AI subscription token lives, are invisible
- a read-only filesystem holding only /usr, the one submitted file (at
  /sandbox/<name>), fresh /proc and /dev, and an empty, writable /tmp.
  Not other students' files, not /home, /etc, /var, or the grader's config
- an empty environment apart from two Python encoding settings
- no capabilities, and it dies with the grader

It needs no root and no sudo rules: bubblewrap uses unprivileged user
namespaces (`apt install bubblewrap`). Before trusting it, call
on_submission.sandbox_self_test(), which runs a probe program inside the
sandbox and reports every property that does not hold.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

SANDBOX_DIR = "/sandbox"
ROOT_ENTRIES = ("/bin", "/lib", "/lib64", "/sbin")  # symlinks into /usr on merged-/usr systems
CHILD_ENV = {"PYTHONIOENCODING": "utf-8", "PYTHONHASHSEED": "0"}
# Python sets LC_CTYPE itself under the C locale (PEP 538); bwrap sets PWD to the working folder.
ALLOWED_ENV = {*CHILD_ENV, "LC_CTYPE", "PWD"}
HOST_DIRS = ("/home", "/root", "/etc", "/var", "/srv", "/opt", "/mnt", "/media")


class SandboxUnavailable(Exception):
    """The sandbox cannot run on this machine."""


def _under_usr(path: str) -> bool:
    return path == "/usr" or path.startswith("/usr/")


class Bwrap:
    """Builds the bubblewrap command line that wraps one student run."""

    name = "bwrap"

    def __init__(self, bwrap: str = "bwrap", prefixes: tuple[str, ...] | None = None, root: Path = Path("/")):
        found = shutil.which(bwrap)
        if found is None:
            raise SandboxUnavailable(f"`{bwrap}` was not found. Install it with: sudo apt install bubblewrap")
        self.bwrap = found
        # The Python install has to be visible inside. Anything outside /usr (a venv, /opt) is bound read-only.
        self.prefixes = tuple(sorted({sys.base_prefix, sys.prefix} if prefixes is None else set(prefixes)))
        self.root = root

    def _system_binds(self) -> list[str]:
        args = ["--ro-bind", "/usr", "/usr"]
        for entry in ROOT_ENTRIES:
            host = self.root / entry.lstrip("/")
            if host.is_symlink():
                args += ["--symlink", os.readlink(host), entry]
            elif host.is_dir():
                args += ["--ro-bind", str(host), entry]
        for prefix in self.prefixes:
            if not _under_usr(prefix):
                args += ["--ro-bind", prefix, prefix]
        return args

    def inside(self, path: Path) -> str:
        """Where the submitted file appears inside the sandbox."""
        return f"{SANDBOX_DIR}/{Path(path).name}"

    def prefix(self, path: Path) -> list[str]:
        """The bwrap arguments to put in front of the Python command."""
        env = [arg for key, value in sorted(CHILD_ENV.items()) for arg in ("--setenv", key, value)]
        return [
            self.bwrap,
            "--unshare-all",          # network, processes, IPC, hostname, user, cgroup
            "--die-with-parent",
            "--new-session",          # no access to the grader's terminal
            "--cap-drop", "ALL",
            "--clearenv", *env,
            *self._system_binds(),
            "--ro-bind", str(Path(path).resolve()), self.inside(path),
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--remount-ro", "/",      # everything but /tmp is read-only
            "--chdir", "/tmp",
            "--",
        ]


# The probe runs inside the sandbox and reports what it can reach. It is
# filled in with a file planted next to it and the grader's own process ID.
PROBE = '''
import json, os, socket
report = {{}}
try:
    socket.create_connection(("192.0.2.1", 9), timeout=1).close()
    report["network"] = True
except OSError:
    report["network"] = False
report["neighbor"] = os.path.exists({neighbor!r}) or os.path.exists("/sandbox/" + os.path.basename({neighbor!r}))
def only_the_path_to_python(d):
    # A folder that exists only because a Python install outside /usr is
    # bound below it (CI's /opt/hostedtoolcache/...) holds nothing else.
    needed = {{p[len(d) + 1:].split("/")[0] for p in {allowed!r} if p.startswith(d + "/")}}
    return bool(needed) and set(os.listdir(d)) <= needed
report["host_dirs"] = [d for d in {host_dirs!r} if os.path.exists(d) and not only_the_path_to_python(d)]
report["env"] = sorted(os.environ)
report["grader_process"] = os.path.exists("/proc/{parent}/environ")
def writable(path):
    try:
        open(path, "a").close()
        return True
    except OSError:
        return False
here = os.path.dirname(os.path.abspath(__file__))
report["writable_outside_tmp"] = [p for p in (os.path.abspath(__file__), os.path.join(here, "probe-new-file")) if writable(p)]
report["tmp_writable"] = writable("/tmp/scratch")
print(json.dumps(report))
'''


def probe_source(neighbor: Path, parent_pid: int, allowed: tuple[str, ...] = ()) -> str:
    """The probe, filled in. `allowed` are the read-only Python prefixes bound
    outside /usr: the folders above them may exist, holding only that path."""
    return PROBE.format(neighbor=str(neighbor), host_dirs=HOST_DIRS, parent=parent_pid, allowed=tuple(allowed))


def check_probe(exit_code: int | None, stdout: str, stderr: str) -> list[str]:
    """Every isolation property the probe found broken. Empty means the sandbox holds."""
    try:
        report = json.loads(stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
        return [f"sandbox did not run (exit {exit_code}): {lines[-1] if lines else 'no output'}"]
    problems = []
    if report["network"]:
        problems.append("the network is reachable")
    if report["neighbor"]:
        problems.append("files next to the submission are visible")
    if report["host_dirs"]:
        problems.append(f"host folders are visible: {', '.join(report['host_dirs'])}")
    extra = sorted(set(report["env"]) - ALLOWED_ENV)
    if extra:
        problems.append(f"unexpected environment variables: {', '.join(extra)}")
    if report["grader_process"]:
        problems.append("the grader's own process is visible")
    if report["writable_outside_tmp"]:
        problems.append(f"writable outside /tmp: {', '.join(report['writable_outside_tmp'])}")
    if not report["tmp_writable"]:
        problems.append("/tmp is not writable, so programs that save files would fail")
    return problems
