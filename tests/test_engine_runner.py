"""Authored process/wheel controls; not public engine accuracy qualification.

Synthetic wheels are explicitly patched into test pins only. Production pins
are never changed and no detector modules or subject programs are imported.
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.engine_runner import EngineRunError, EngineRunLimits, _environment, _execute, _wheel, run_engine
from delivery_review.adapters.engines import PINS
from delivery_review.source_snapshot import materialize_snapshot
from test_source_snapshot import Objects


PROGRAM = b'''import json,os,sys
assert "PYTHONPATH" not in os.environ and "GH_TOKEN" not in os.environ
assert os.environ["GIT_ALLOW_PROTOCOL"] == ""
base,head=sys.argv[2].split("..")
print(json.dumps({"pinwash_findings_version":1,"run":{"pinwash_version":"0.1.1","spec_version":29,"base":base,"head":head},"findings":[],"summary":{"info":0,"warn":0,"high":0,"critical":0},"config_errors":[],"unknown_coverage":[],"verdict":"pass","authored_quantity":0.25}))
'''


def synthetic_wheel(program=PROGRAM, *, extras=None, corrupt_record=False):
    info = "pinwash-0.1.1.dist-info/"
    files = {"pinwash/__init__.py": b"", "pinwash/__main__.py": program,
             info + "METADATA": b"Metadata-Version: 2.1\nName: pinwash\nVersion: 0.1.1\n",
             info + "WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    files.update(extras or {})
    rows = []
    for path, raw in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
        rows.append(path + ",sha256=" + digest + "," + str(len(raw)))
    rows.append(info + "RECORD,,")
    files[info + "RECORD"] = ("\n".join(rows) + "\n").encode()
    if corrupt_record:
        files["pinwash/__main__.py"] += b"\n# changed after RECORD\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, raw in files.items():
            archive.writestr(path, raw)
    return buffer.getvalue()


class EngineRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git = Path(shutil.which("git"))
        self.env = _environment(self.git, self.root, "2026-11-06")

    def execute(self, code, *, seconds=5, limits=None, coverage=None):
        return _execute([sys.executable, "-I", "-S", "-c", code], cwd=self.root, env=self.env,
                        deadline=time.monotonic() + seconds, limits=limits or EngineRunLimits(), coverage_path=coverage)

    def snapshot(self):
        objects = Objects()
        marker = self.root / "subject-executed"
        script = ("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('unsafe')\n").encode()
        base = objects.commit(objects.files({"test_app.py": script, "pinwash.py": b"raise RuntimeError('subject shadowed scanner')\n"}))
        head = objects.commit(objects.files({"test_app.py": script + b"# changed\n", "pinwash.py": b"raise RuntimeError('subject shadowed scanner')\n"}), [base])
        return materialize_snapshot(self.root / "subject", repository="fixture-owner/authored-engine-control",
                                    head_sha=head, base_tip_sha=base, objects=objects.objects, git_executable=self.git)

    def invoke(self, snapshot, data, **changes):
        wheel_path = self.root / "synthetic.whl"
        wheel_path.write_bytes(data)
        options = dict(engine_name="pinwash", wheel_path=wheel_path, python_executable=Path(sys.executable),
                       git_executable=self.git, destination=self.root / "execution",
                       native_policy={"threshold": "high", "evaluation_date": "2026-11-06", "quantity": 0.25})
        options.update(changes)
        pin = ("0.1.1", hashlib.sha256(data).hexdigest(), 1)
        with patch.dict(PINS, {"pinwash": pin}), patch.dict(os.environ, {"GH_TOKEN": "must-not-propagate", "PYTHONPATH": str(snapshot.root)}):
            return run_engine(snapshot, **options)

    def test_process_preserves_raw_bytes_and_native_exit(self):
        facts, raw = self.execute("import sys;sys.stdout.buffer.write(b'original\\r\\n');sys.stderr.buffer.write(b'diagnostic');sys.exit(1)")
        self.assertEqual(("exited", 1), (facts["termination"], facts["exit_code"]))
        self.assertEqual({"stdout": b"original\r\n", "stderr": b"diagnostic"}, raw)

    def test_spawn_failure_does_not_fabricate_native_stderr(self):
        facts, raw = _execute([str(self.root / "no-executable")], cwd=self.root, env=self.env,
                             deadline=time.monotonic() + 5, limits=EngineRunLimits(max_stderr_bytes=1))
        self.assertEqual(("spawn_error", None), (facts["termination"], facts["exit_code"]))
        self.assertEqual({"stdout": b"", "stderr": b""}, raw)

    def test_bounded_stdin_is_written_without_blocking_output_drain(self):
        payload = b"authored request\n" * 4096
        facts, raw = _execute([sys.executable, "-I", "-S", "-c", "import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())"],
            cwd=self.root, env=self.env, deadline=time.monotonic() + 5, limits=EngineRunLimits(), stdin_bytes=payload)
        self.assertEqual("exited", facts["termination"])
        self.assertEqual(payload, raw["stdout"])
        with self.assertRaisesRegex(EngineRunError, "process_stdin_limit"):
            _execute([], cwd=self.root, env=self.env, deadline=time.monotonic() + 5,
                     limits=EngineRunLimits(), stdin_bytes=b"x" * (1024 * 1024 + 1))

    def test_external_resource_monitor_refuses_and_cleans_process(self):
        calls = []
        def monitor():
            calls.append(True)
            return True
        facts, _ = _execute([sys.executable, "-I", "-S", "-c", "import time;time.sleep(30)"],
            cwd=self.root, env=self.env, deadline=time.monotonic() + 5, limits=EngineRunLimits(), monitor=monitor)
        self.assertTrue(calls)
        self.assertEqual("output_limit", facts["termination"])
        def fail():
            raise EngineRunError("authored_external_limit")
        with self.assertRaisesRegex(EngineRunError, "authored_external_limit"):
            _execute([sys.executable, "-I", "-S", "-c", "import time;time.sleep(30)"],
                cwd=self.root, env=self.env, deadline=time.monotonic() + 5, limits=EngineRunLimits(), monitor=fail)

    def test_stdout_and_stderr_are_bounded_and_stop_process(self):
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream):
                facts, raw = self.execute("import sys,time;sys." + stream + ".buffer.write(b'x'*1000000);sys." + stream + ".flush();time.sleep(30)",
                                          limits=EngineRunLimits(max_stdout_bytes=64, max_stderr_bytes=64))
                self.assertEqual("output_limit", facts["termination"])
                self.assertEqual(b"x" * 64, raw[stream])

    def test_deadline_kills_child_tree_after_parent_exits(self):
        marker = self.root / "background-survived"
        child = "import time,pathlib;time.sleep(1);pathlib.Path(" + repr(str(marker)) + ").write_text('survived');time.sleep(30)"
        code = "import subprocess,sys;subprocess.Popen([sys.executable,'-I','-S','-c'," + repr(child) + "],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
        facts, _ = self.execute(code, seconds=0.3)
        self.assertEqual("timeout", facts["termination"])
        time.sleep(1)
        self.assertFalse(marker.exists())

    def test_coverage_limit_preserves_only_original_prefix(self):
        path = self.root / "coverage"
        facts, raw = self.execute("import pathlib,time;pathlib.Path(" + repr(str(path)) + ").write_bytes(b'c'*10000);time.sleep(30)",
                                  limits=EngineRunLimits(max_coverage_bytes=17), coverage=path)
        self.assertEqual("output_limit", facts["termination"])
        self.assertEqual(b"c" * 17, raw["coverage"])

    def test_coverage_link_is_refused_and_child_cleaned(self):
        original = self.root / "original"
        original.write_bytes(b"outside")
        link = self.root / "coverage"
        os.link(original, link)
        with self.assertRaisesRegex(EngineRunError, "unsafe_engine_coverage_output"):
            self.execute("import time;time.sleep(30)", coverage=link)
        self.assertEqual(b"outside", original.read_bytes())

    def test_environment_is_an_allowlist(self):
        hostile = {key: "forbidden" for key in ("GH_TOKEN", "GITHUB_TOKEN", "HTTPS_PROXY", "LD_PRELOAD", "PYTHONPATH",
                   "PYTHONHOME", "GIT_CONFIG_PARAMETERS", "GIT_SSH_COMMAND", "GIT_OBJECT_DIRECTORY", "GIT_ASKPASS")}
        with patch.dict(os.environ, hostile):
            value = _environment(self.git, self.root, "2026-11-06")
        self.assertTrue(set(hostile).isdisjoint(value))
        self.assertEqual(str(self.git.parent), value["PATH"])
        self.assertEqual(os.devnull, value["GIT_CONFIG_GLOBAL"])

    def test_limits_cannot_be_weakened(self):
        for key, value in (("timeout_seconds", 301), ("max_stdout_bytes", 2097153), ("max_wheel_members", 1001),
                           ("max_coverage_bytes", True), ("max_stderr_bytes", 0)):
            with self.subTest(key=key), self.assertRaises(EngineRunError):
                EngineRunLimits(**{key: value})

    def test_wheel_hash_record_and_startup_injection_refused(self):
        for extra, corrupt, reason in (({}, True, "engine_record_digest_mismatch"),
            ({"pinwash/start.pth": b"import os\n"}, False, "unsupported_engine_wheel_layout"),
            ({"pinwash/sitecustomize.py": b"pass\n"}, False, "unsupported_engine_wheel_layout"),
            ({"../escape": b"x"}, False, "unsafe_engine_wheel_path")):
            data = synthetic_wheel(extras=extra, corrupt_record=corrupt)
            with self.subTest(reason=reason), patch.dict(PINS, {"pinwash": ("0.1.1", hashlib.sha256(data).hexdigest(), 1)}):
                with self.assertRaisesRegex(EngineRunError, reason):
                    _wheel(data, "pinwash", EngineRunLimits())
        with self.assertRaisesRegex(EngineRunError, "engine_wheel_digest_mismatch"):
            _wheel(synthetic_wheel(), "pinwash", EngineRunLimits())

    def test_fresh_environment_records_installation_and_preserves_native_floats(self):
        snapshot = self.snapshot()
        result = self.invoke(snapshot, synthetic_wheel())
        self.assertEqual(("completed", "pass"), (result.observation["status"], result.observation["verdict"]))
        self.assertEqual(0.25, result.observation["parsed_native"]["authored_quantity"])
        self.assertFalse((self.root / "subject-executed").exists())
        self.assertFalse((snapshot.root / ".git/index").exists())
        self.assertIn("pycache_prefix=", " ".join(result.manifest["process"]["argv"]))
        self.assertEqual(hashlib.sha256((result.directory / "installation.json").read_bytes()).hexdigest(), result.manifest["installation"]["sha256"])
        self.assertEqual(result.manifest_bytes, (result.directory / "execution.json").read_bytes())
        self.assertEqual(result.raw_files["stdout"], (result.directory / "raw/stdout.bin").read_bytes())
        result.observation["parsed_native"]["verdict"] = "changed"
        self.assertEqual("pass", result.observation["parsed_native"]["verdict"])
        with self.assertRaises(TypeError):
            result.raw_files["stdout"] = b"changed"
        self.assertFalse(list((result.directory / "environment").rglob("*.pyc")))
        with self.assertRaises(EngineRunError):
            self.invoke(snapshot, synthetic_wheel())

    def test_mutated_source_and_inside_subject_destination_are_refused(self):
        snapshot = self.snapshot()
        with self.assertRaisesRegex(EngineRunError, "engine_destination_inside_subject"):
            self.invoke(snapshot, synthetic_wheel(), destination=snapshot.root / "execution")
        (snapshot.root / "test_app.py").write_bytes(b"changed")
        with self.assertRaisesRegex(EngineRunError, "engine_execution_boundary_failed"):
            self.invoke(snapshot, synthetic_wheel())
        self.assertFalse((self.root / "execution").exists())

    def test_post_source_and_installation_failure_keep_known_native_block(self):
        snapshot = self.snapshot()
        finding = {"rule": "AUTHORED_BLOCK", "severity": "high", "fingerprint": "authored/v1:" + "a" * 64,
                   "path": "test_app.py", "message": "Authored native block", "before": "before", "after": "after"}
        code = PROGRAM.replace(b'"findings":[]', b'"findings":[' + json.dumps(finding).encode() + b']')
        code = code.replace(b'"high":0', b'"high":1').replace(b'"verdict":"pass"', b'"verdict":"block"')
        code += b"\nfrom pathlib import Path\nPath(__file__).write_text('changed')\nPath('test_app.py').write_text('changed')\nsys.exit(1)\n"
        result = self.invoke(snapshot, synthetic_wheel(program=code))
        observation = result.observation
        self.assertEqual(("error", "not_judged"), (observation["status"], observation["verdict"]))
        self.assertTrue(observation["known_block"])
        self.assertEqual("block", observation["parsed_native"]["verdict"])
        self.assertEqual(1, observation["process"]["exit_code"])
        self.assertEqual("exited", observation["process"]["termination"])
        self.assertEqual("AUTHORED_BLOCK", observation["engine"]["findings"][0]["rule"])
        self.assertEqual({"source_post_integrity_failed", "engine_installation_post_integrity_failed"}, set(observation["controller_integrity_errors"]))
        self.assertEqual("failed", result.manifest["source_integrity"]["after"])
        self.assertEqual("failed", result.manifest["installation_integrity"]["after"])
        self.assertTrue((self.root / "execution/raw/stdout.bin").exists())
        self.assertTrue((self.root / "execution/execution.json").exists())

    def test_invalid_policy_rejected_before_any_process(self):
        snapshot = self.snapshot()
        with patch("delivery_review.engine_runner.subprocess.Popen", side_effect=AssertionError("must not run")):
            with self.assertRaises(EngineRunError):
                self.invoke(snapshot, synthetic_wheel(), native_policy={"threshold": "high", "evaluation_date": "2026-11-06", "bad": float("nan")})


if __name__ == "__main__":
    unittest.main()
