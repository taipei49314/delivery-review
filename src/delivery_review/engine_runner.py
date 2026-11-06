"""Create-once isolated execution of pinned engines over a verified snapshot.

The operator approves Python and Git; their installation is outside the subject.
Only locked pure-Python wheel files are installed, without pip/build hooks. This
is a bounded static-scanner boundary, not a general sandbox for arbitrary code.
"""
from __future__ import annotations

import base64
from collections.abc import Mapping
import csv
from dataclasses import dataclass, fields
from email.parser import BytesParser
from email.policy import default as email_policy
from functools import wraps
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import subprocess
import sys
import threading
import time
from types import MappingProxyType
import zipfile

from .adapters.engines import PINS, STREAM_LIMITS, normalize_engine_result
from .contracts import ContractError, canonical_bytes, loads_strict
from .evidence.store import _directories, _identity, _read, _regular, _scan, _write
from .source_snapshot import SourceSnapshot


class EngineRunError(ContractError):
    """A fixed, non-secret reason the execution boundary could not be established."""


def _need(condition, code):
    if not condition:
        raise EngineRunError(code)


def _boundary(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except EngineRunError:
            raise
        except (ContractError, OSError, ValueError, TypeError, RecursionError, subprocess.SubprocessError, zipfile.BadZipFile, csv.Error) as exc:
            raise EngineRunError("engine_execution_boundary_failed") from exc
    return wrapped


@dataclass(frozen=True)
class EngineRunLimits:
    timeout_seconds: int = 120
    max_stdout_bytes: int = STREAM_LIMITS["stdout"]
    max_stderr_bytes: int = STREAM_LIMITS["stderr"]
    max_coverage_bytes: int = STREAM_LIMITS["coverage"]
    max_wheel_bytes: int = 8 * 1024 * 1024
    max_expanded_bytes: int = 32 * 1024 * 1024
    max_wheel_members: int = 1000

    def __post_init__(self):
        caps = {"timeout_seconds": 300, "max_stdout_bytes": STREAM_LIMITS["stdout"],
                "max_stderr_bytes": STREAM_LIMITS["stderr"], "max_coverage_bytes": STREAM_LIMITS["coverage"],
                "max_wheel_bytes": 8 * 1024 * 1024, "max_expanded_bytes": 32 * 1024 * 1024,
                "max_wheel_members": 1000}
        _need(all(type(getattr(self, item.name)) is int and 0 < getattr(self, item.name) <= caps[item.name]
                  for item in fields(self)), "invalid_engine_run_limits")


@dataclass(frozen=True)
class EngineExecution:
    directory: Path
    manifest_bytes: bytes
    raw_files: Mapping[str, bytes]
    observation_bytes: bytes

    @property
    def manifest(self):
        return loads_strict(self.manifest_bytes)

    @property
    def observation(self):
        # Native finite floats are allowed, unlike the frozen contract parser.
        return json.loads(self.observation_bytes)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _wheel(raw, name, limits):
    """Verify exact locked archive, METADATA, every RECORD row and safe paths."""
    version, digest, _ = PINS[name]
    _need(len(raw) <= limits.max_wheel_bytes and _sha(raw) == digest, "engine_wheel_digest_mismatch")
    files, seen, total = {}, set(), 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        _need(len(entries) <= limits.max_wheel_members, "engine_wheel_member_limit")
        for entry in entries:
            path = entry.filename
            parts = path.rstrip("/").split("/")
            mode = entry.external_attr >> 16
            _need(entry.orig_filename == path and parts and len(parts) <= 16
                  and not PurePosixPath(path).is_absolute()
                  and all(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", part) and not part.endswith(".")
                          and not re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", part.split(".")[0], re.I)
                          for part in parts), "unsafe_engine_wheel_path")
            _need(path.casefold() not in seen, "duplicate_engine_wheel_path")
            seen.add(path.casefold())
            _need(not entry.flag_bits & 1 and not stat.S_ISLNK(mode), "unsafe_engine_wheel_member")
            if entry.is_dir():
                _need(stat.S_IFMT(mode) in {0, stat.S_IFDIR}, "unsafe_engine_wheel_member")
                continue
            _need(stat.S_IFMT(mode) in {0, stat.S_IFREG} and entry.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED},
                  "unsafe_engine_wheel_member")
            _need(parts[0] in {name, f"{name}-{version}.dist-info"}
                  and not path.endswith((".pth", ".pyc", ".pyo"))
                  and parts[-1] not in {"sitecustomize.py", "usercustomize.py"}, "unsupported_engine_wheel_layout")
            total += entry.file_size
            _need(entry.file_size <= limits.max_expanded_bytes and total <= limits.max_expanded_bytes,
                  "engine_wheel_expansion_limit")
            with archive.open(entry) as stream:
                data = stream.read(entry.file_size + 1)
            _need(len(data) == entry.file_size, "engine_wheel_member_size_mismatch")
            files[path] = data
    info = f"{name}-{version}.dist-info/"
    _need({info + "METADATA", info + "WHEEL", info + "RECORD", name + "/__main__.py"} <= files.keys(),
          "engine_wheel_metadata_missing")
    metadata = BytesParser(policy=email_policy).parsebytes(files[info + "METADATA"])
    _need(metadata.get_all("Name") == [name] and metadata.get_all("Version") == [version], "engine_wheel_identity_mismatch")
    _need(all(re.search(r";\s*extra\s*==\s*['\"]dev['\"]\s*$", value)
              for value in metadata.get_all("Requires-Dist", [])), "unsupported_engine_runtime_dependency")
    wheel = BytesParser(policy=email_policy).parsebytes(files[info + "WHEEL"])
    _need(wheel.get("Root-Is-Purelib") == "true" and wheel.get_all("Tag") == ["py3-none-any"],
          "unsupported_engine_wheel_platform")
    rows = list(csv.reader(io.StringIO(files[info + "RECORD"].decode("utf-8")), strict=True))
    recorded = set()
    for row in rows:
        _need(len(row) == 3, "invalid_engine_record")
        path, expected, size = row
        _need(path in files and path not in recorded, "invalid_engine_record_inventory")
        recorded.add(path)
        if path == info + "RECORD":
            _need(not expected and not size, "invalid_engine_record_self_entry")
            continue
        algorithm, separator, encoded = expected.partition("=")
        _need(separator and algorithm in {"sha256", "sha384", "sha512"}
              and re.fullmatch(r"0|[1-9][0-9]*", size) is not None, "invalid_engine_record_digest")
        actual = base64.urlsafe_b64encode(hashlib.new(algorithm, files[path]).digest()).rstrip(b"=").decode("ascii")
        _need(actual == encoded and int(size) == len(files[path]), "engine_record_digest_mismatch")
    _need(recorded == set(files), "engine_record_inventory_mismatch")
    return files


def _environment(git, home, date):
    # No inherited PATH, token, proxy, Python/loader/Git options reach a child.
    environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ}
    environment.update(PATH=str(git.parent), HOME=str(home), USERPROFILE=str(home),
        TEMP=str(home), TMP=str(home), TMPDIR=str(home), LC_ALL="C", LANG="C", TZ="UTC",
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_COUNT="0", GIT_ATTR_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1",
        GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0", GIT_ALLOW_PROTOCOL="", GIT_PROTOCOL_FROM_USER="0",
        CHECKWASH_TODAY=date, PINWASH_TODAY=date)
    return environment


def _linux_group_active(group):
    # Zombies cannot execute or write. The direct child is always wait()ed;
    # orphan descendants are reaped by the host's init process.
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            value = (entry / "stat").read_text()
            fields_ = value[value.rfind(")") + 2:].split()
            if len(fields_) > 2 and int(fields_[2]) == group and fields_[0] != "Z":
                return True
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return False


def _prefix(path, budget):
    """Retain a refused sidecar's original prefix without following aliases."""
    _directories(path.parent)
    before = path.lstat()
    _regular(before)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        _regular(opened)
        _need(_identity(before) == _identity(opened), "engine_coverage_changed")
        raw = stream.read(budget)
        after = os.fstat(stream.fileno())
    final = path.lstat()
    _regular(final)
    _need(_identity(before) == _identity(after) == _identity(final)
          and opened.st_ctime_ns == after.st_ctime_ns and before.st_ctime_ns == final.st_ctime_ns
          and len(raw) == min(budget, before.st_size), "engine_coverage_changed")
    return raw


def _coverage_info(path):
    if path is None:
        return None
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    _need(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
          and not getattr(info, "st_file_attributes", 0) & 0x400 and info.st_nlink == 1,
          "unsafe_engine_coverage_output")
    return info


class _WindowsJob:
    """Assign a suspended child before its first instruction, then resume it."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        self.ctypes, self.w = ctypes, w
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        class BasicLimit(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                               "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimit), ("IoInfo", IO),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in ("TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")]
            _fields_ += [(name, w.DWORD) for name in ("TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses")]
        self.Accounting = Accounting
        definitions = {
            "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            "QueryInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "CreateToolhelp32Snapshot": ([w.DWORD, w.DWORD], w.HANDLE),
            "OpenThread": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "ResumeThread": ([w.HANDLE], w.DWORD),
        }
        for name, (argtypes, restype) in definitions.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = argtypes, restype
        self.handle = self.kernel.CreateJobObjectW(None, None)
        _need(bool(self.handle), "process_tree_control_unsupported")
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE, no breakaway.
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close()
            raise EngineRunError("process_tree_control_unsupported")

    def attach(self, process):
        c, w, kernel = self.ctypes, self.w, self.kernel
        handle = kernel.OpenProcess(0x0100 | 0x0001 | 0x1000, False, process.pid)
        _need(bool(handle), "process_tree_assignment_failed")
        try:
            _need(kernel.AssignProcessToJobObject(self.handle, handle), "process_tree_assignment_failed")
        finally:
            kernel.CloseHandle(handle)
        class ThreadEntry(c.Structure):
            _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ThreadID", w.DWORD),
                        ("th32OwnerProcessID", w.DWORD), ("tpBasePri", w.LONG), ("tpDeltaPri", w.LONG), ("dwFlags", w.DWORD)]
        for name in ("Thread32First", "Thread32Next"):
            function = getattr(kernel, name)
            function.argtypes, function.restype = [w.HANDLE, c.POINTER(ThreadEntry)], w.BOOL
        snapshot = kernel.CreateToolhelp32Snapshot(4, 0)
        _need(snapshot not in {None, c.c_void_p(-1).value}, "process_thread_observation_failed")
        threads = []
        try:
            entry = ThreadEntry()
            entry.dwSize = c.sizeof(entry)
            success = kernel.Thread32First(snapshot, c.byref(entry))
            while success:
                if entry.th32OwnerProcessID == process.pid:
                    threads.append(entry.th32ThreadID)
                success = kernel.Thread32Next(snapshot, c.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
        _need(len(threads) == 1, "process_thread_observation_failed")
        thread = kernel.OpenThread(2, False, threads[0])
        _need(bool(thread), "process_thread_resume_failed")
        try:
            _need(kernel.ResumeThread(thread) == 1, "process_thread_resume_failed")
        finally:
            kernel.CloseHandle(thread)

    def active(self):
        value = self.Accounting()
        _need(self.kernel.QueryInformationJobObject(self.handle, 1, self.ctypes.byref(value), self.ctypes.sizeof(value), None),
              "process_tree_observation_failed")
        return bool(value.ActiveProcesses)

    def kill(self):
        _need(self.kernel.TerminateJobObject(self.handle, 1), "process_tree_termination_failed")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _execute(argv, *, cwd, env, deadline, limits, coverage_path=None, stdin_bytes=None, monitor=None):
    """Bounded pipes plus process-tree lifetime; never subprocess.run(buffer all)."""
    _need(os.name == "nt" or (sys.platform == "linux" and Path("/proc").is_dir()), "process_tree_control_unsupported")
    _need(stdin_bytes is None or (type(stdin_bytes) is bytes and len(stdin_bytes) <= 1024 * 1024), "process_stdin_limit")
    _need(monitor is None or callable(monitor), "invalid_process_monitor")
    begin = time.monotonic()
    _need(begin < deadline, "engine_deadline_exceeded")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    caps = {"stdout": limits.max_stdout_bytes, "stderr": limits.max_stderr_bytes}
    refused, read_failed = threading.Event(), threading.Event()
    job, process, threads = None, None, []
    termination = "exited"
    def drain(name, stream):
        try:
            while True:
                chunk = stream.read1(65536)
                if not chunk:
                    break
                remaining = caps[name] - len(buffers[name])
                buffers[name].extend(chunk[:remaining])
                if len(chunk) > remaining:
                    refused.set()
        except (OSError, ValueError):
            read_failed.set()
        finally:
            stream.close()
    def active():
        return job.active() if job is not None else _linux_group_active(process.pid)
    def feed(stream):
        try:
            stream.write(stdin_bytes)
            stream.flush()
        except BrokenPipeError:
            pass  # Child native exit/streams describe an early rejection.
        except (OSError, ValueError):
            read_failed.set()
        finally:
            try:
                stream.close()
            except BrokenPipeError:
                pass
    def kill():
        if job is not None:
            job.kill()
        elif process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    try:
        if os.name == "nt":
            job = _WindowsJob()
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL if stdin_bytes is None else subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, close_fds=True,
                creationflags=0x08000004 if os.name == "nt" else 0, start_new_session=os.name != "nt")
        except OSError:
            return {"argv": list(argv), "duration_ms": round((time.monotonic() - begin) * 1000),
                    "exit_code": None, "termination": "spawn_error"}, {"stdout": b"", "stderr": b""}
        if job is not None:
            job.attach(process)
        if stdin_bytes is not None:
            thread = threading.Thread(target=feed, args=(process.stdin,), daemon=True)
            thread.start()
            threads.append(thread)
        for name in buffers:
            thread = threading.Thread(target=drain, args=(name, getattr(process, name)), daemon=True)
            thread.start()
            threads.append(thread)
        while True:
            code = process.poll()
            if monitor is not None and monitor():
                refused.set()
            info = _coverage_info(coverage_path)
            if info is not None:
                if info.st_size > limits.max_coverage_bytes:
                    refused.set()
            if refused.is_set():
                termination = "output_limit"
                kill()
                break
            if time.monotonic() >= deadline:
                termination = "timeout"
                kill()
                break
            if code is not None and not any(thread.is_alive() for thread in threads) and not active():
                break
            time.sleep(0.01)
        process.wait(timeout=10)
        for thread in threads:
            thread.join(timeout=10)
        _need(not any(thread.is_alive() for thread in threads) and not read_failed.is_set(), "engine_pipe_cleanup_failed")
        stop = time.monotonic() + 10
        while active() and time.monotonic() < stop:
            time.sleep(0.01)
        _need(not active(), "engine_process_cleanup_failed")
        result = {"argv": list(argv), "duration_ms": round((time.monotonic() - begin) * 1000),
                  "exit_code": process.returncode, "termination": termination}
        raw = {name: bytes(value) for name, value in buffers.items()}
        info = _coverage_info(coverage_path)
        if info is not None:
            # A refused sidecar retains only its bounded original prefix.
            if info.st_size > limits.max_coverage_bytes:
                raw["coverage"] = _prefix(coverage_path, limits.max_coverage_bytes)
                result["termination"] = "output_limit"
            else:
                raw["coverage"] = _read(coverage_path, limits.max_coverage_bytes)
        return result, raw
    finally:
        if process is not None:
            if job is not None:
                job.close()  # KILL_ON_JOB_CLOSE also covers unexpected exceptions.
            else:
                kill()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            for thread in threads:
                thread.join(timeout=10)
            _need(not any(thread.is_alive() for thread in threads), "engine_pipe_cleanup_failed")
        elif job is not None:
            job.close()


@_boundary
def run_engine(snapshot: SourceSnapshot, *, engine_name: str, wheel_path: str | Path,
               python_executable: str | Path, git_executable: str | Path, destination: str | Path,
               native_policy: dict, limits: EngineRunLimits = EngineRunLimits()) -> EngineExecution:
    """Make one new isolated run; never install or execute anything from the PR."""
    from .source_snapshot import verify_snapshot
    _need(type(snapshot) is SourceSnapshot and type(limits) is EngineRunLimits, "invalid_engine_run_input")
    _need(engine_name in PINS and type(native_policy) is dict and native_policy.get("threshold") == "high",
          "unsupported_engine_policy")
    policy_bytes = json.dumps(native_policy, sort_keys=True, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8")
    _need(len(policy_bytes) <= 65536 and ("require_complete" not in native_policy
          or type(native_policy["require_complete"]) is bool), "invalid_engine_policy")
    date = native_policy.get("evaluation_date")
    _need(type(date) is str and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date), "missing_engine_policy_date")
    from datetime import date as calendar_date
    calendar_date.fromisoformat(date)
    root = Path(os.path.abspath(destination))
    _directories(root.parent)
    _need(root != snapshot.root and snapshot.root not in root.parents, "engine_destination_inside_subject")
    python, git = Path(python_executable), Path(git_executable)
    _need(all(path.is_absolute() and path.is_file() and path != snapshot.root and snapshot.root not in path.parents
              for path in (python, git)), "invalid_engine_executable")
    deadline = time.monotonic() + limits.timeout_seconds
    verify_snapshot(snapshot)
    wheel_raw = _read(Path(wheel_path).absolute(), limits.max_wheel_bytes)
    files = _wheel(wheel_raw, engine_name, limits)
    root.mkdir(mode=0o700)
    for path in (root / "home", root / "cache", root / "raw", root / "setup"):
        path.mkdir(mode=0o700)
    environment = _environment(git, root / "home", date)
    setup = []
    def setup_process(label, argv):
        process, streams = _execute(argv, cwd=root, env=environment, deadline=deadline, limits=limits)
        entries = {}
        for name, data in streams.items():
            path = f"setup/{label}.{name}"
            _write(root / path, data)
            entries[name] = {"path": path, "size_bytes": len(data), "sha256": _sha(data)}
        setup.append({"role": label, "process": process, "streams": entries})
        _need(process["termination"] == "exited" and process["exit_code"] == 0, "engine_environment_failed")
        return streams
    runtime = setup_process("runtime", [str(python), "-I", "-S", "-c",
        "import json,sys; print(json.dumps({'version':list(sys.version_info[:3]),'executable':sys.executable}))"])
    identity = json.loads(runtime["stdout"])
    _need(type(identity) is dict and identity["version"][:2] in ([3, 11], [3, 12]), "unsupported_engine_python")
    git_streams = setup_process("git", [str(git), "--version"])
    git_version = git_streams["stdout"].decode("utf-8").strip()
    _need(re.fullmatch(r"git version [A-Za-z0-9_.()+ -]{1,128}", git_version), "unsupported_git_version_output")
    environment_path = root / "environment"
    setup_process("venv", [str(python), "-I", "-S", "-m", "venv", "--without-pip", "--copies", str(environment_path)])
    major, minor = identity["version"][:2]
    site = environment_path / ("Lib/site-packages" if os.name == "nt" else f"lib/python{major}.{minor}/site-packages")
    _directories(site)
    _need(not list(site.iterdir()), "unexpected_engine_environment_content")
    cfg = _read(environment_path / "pyvenv.cfg", 65536).decode("utf-8")
    _need("include-system-site-packages = false" in cfg.splitlines(), "unsafe_engine_environment")
    for path, data in files.items():
        target = site.joinpath(*path.split("/"))
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write(target, data)
    _scan(site, set(files))
    installation = []
    for path, data in sorted(files.items()):
        row = {"path": path, "size_bytes": len(data), "sha256": _sha(data)}
        _read(site / path, limits.max_expanded_bytes, row)
        installation.append(row)
    installed_bytes = canonical_bytes(installation) + b"\n"
    _write(root / "installation.json", installed_bytes)
    engine_python = environment_path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    argv = [str(engine_python), "-I", "-X", "utf8", "-B", "-X", "pycache_prefix=" + str(root / "cache"), "-m", engine_name]
    coverage_path = None
    if engine_name == "checkwash":
        coverage_path = root / "coverage.output"
        argv += ["check", snapshot.merge_base_sha + ".." + snapshot.head_sha, "--repo", str(snapshot.root),
                 "--format", "json", "--coverage-report", str(coverage_path), "--fail-on", "high"]
    elif engine_name == "pinwash":
        argv += ["check", snapshot.merge_base_sha + ".." + snapshot.head_sha]
    else:
        argv += ["scan", str(snapshot.root), "--at", snapshot.head_sha, "--adapter", "ec-ledger", "--fail-on", "high"]
        if native_policy.get("require_complete") is True:
            argv += ["--require-complete"]
    process, streams = _execute(argv, cwd=snapshot.root if engine_name == "pinwash" else root,
        env=environment, deadline=deadline, limits=limits, coverage_path=coverage_path)
    inventory = {}
    for name, data in sorted(streams.items()):
        path = "raw/" + name + ".bin"
        _write(root / path, data)
        inventory[name] = {"path": path, "size_bytes": len(data), "sha256": _sha(data)}
    integrity_errors = []
    try:
        verify_snapshot(snapshot)
    except (ContractError, OSError):
        integrity_errors.append("source_post_integrity_failed")
    try:
        _scan(site, set(files))
        for row in installation:
            _read(site / row["path"], limits.max_expanded_bytes, row)
    except (ContractError, OSError):
        integrity_errors.append("engine_installation_post_integrity_failed")
    subject = {"head_sha": snapshot.head_sha, "base_tip_sha": snapshot.base_tip_sha, "merge_base_sha": snapshot.merge_base_sha}
    version, digest, _ = PINS[engine_name]
    observation = normalize_engine_result(engine_name=engine_name, engine_version=version, wheel_sha256=digest,
        subject=subject, process=process, raw_files=streams, native_policy=native_policy)
    observation["controller_integrity_errors"] = list(integrity_errors)
    if integrity_errors:
        # Integrity failure invalidates provenance, not the already observed
        # native block/findings/exit. Do not erase them or fabricate a new exit.
        observation.update(status="error", verdict="not_judged")
        observation["reason_codes"] = sorted(set(observation["reason_codes"]) | set(integrity_errors))
        observation["engine"].update(status="error", verdict="not_judged")
    observed = (json.dumps(observation, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    _write(root / "observation.json", observed)
    manifest = {"kind": "delivery-review-engine-execution", "execution_version": 1,
        "engine": {"name": engine_name, "version": version, "wheel_sha256": digest}, "runtime": identity,
        "git": {"executable": str(git), "version": git_version},
        "source_inventory_sha256": _sha(snapshot.inventory_bytes), "subject": subject, "setup": setup,
        "source_integrity": {"before": "verified", "after": "failed" if "source_post_integrity_failed" in integrity_errors else "verified"},
        "installation_integrity": {"before": "verified", "after": "failed" if "engine_installation_post_integrity_failed" in integrity_errors else "verified"},
        "controller_integrity_errors": list(integrity_errors),
        "installation": {"path": "installation.json", "size_bytes": len(installed_bytes), "sha256": _sha(installed_bytes)},
        "process": process,
        "native_policy_sha256": _sha(policy_bytes), "streams": inventory,
        "observation": {"path": "observation.json", "size_bytes": len(observed), "sha256": _sha(observed)}}
    manifest_bytes = canonical_bytes(manifest) + b"\n"
    _write(root / "execution.json", manifest_bytes)
    return EngineExecution(root, manifest_bytes, MappingProxyType(dict(streams)), observed)
