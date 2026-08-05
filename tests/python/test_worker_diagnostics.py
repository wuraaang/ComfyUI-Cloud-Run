import asyncio
from pathlib import Path
import sys
import tempfile
import unittest


class ChunkReader:
    def __init__(self, *chunks):
        self._chunks = list(chunks)

    async def read(self, _size=-1):
        await asyncio.sleep(0)
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


class ExitedProcess:
    def __init__(self):
        self.returncode = 17
        self.stdout = ChunkReader(
            b"loading /Users/private/models/model.safetensors\n",
            b"Authorization: Bearer worker-secret\n",
        )
        self.stderr = ChunkReader(
            b"download https://example.invalid/model?X-Amz-Signature=secret\n",
            b"invalid utf8: \xff\n",
        )

    def terminate(self):
        raise AssertionError("an exited process must not be terminated")

    def kill(self):
        raise AssertionError("an exited process must not be killed")

    async def wait(self):
        return self.returncode


class ProcessFactory:
    def __init__(self):
        self.calls = []
        self.process = ExitedProcess()

    async def __call__(self, *argv, **options):
        self.calls.append((argv, options))
        return self.process


class WorkerDiagnosticsTests(unittest.TestCase):
    def test_buffer_is_bounded_redacted_and_immutable(self):
        from cloud_run.run_errors import MAX_SAFE_LOG_LINES, MAX_SAFE_TEXT_BYTES
        from remote_worker.diagnostics import BoundedDiagnostics

        diagnostics = BoundedDiagnostics(
            local_roots=("/opt/private/comfy",),
        )
        for index in range(100):
            diagnostics.feed(
                "stdout" if index % 2 == 0 else "stderr",
                (
                    f"line-{index} /opt/private/comfy/models/model.bin "
                    "Bearer raw-secret "
                    "https://example.invalid/file?token=signed-secret\n"
                ).encode("utf-8"),
            )
        diagnostics.feed("stderr", b"invalid utf8: \xff\n")

        snapshot = diagnostics.snapshot(
            phase="failed",
            exit_code=17,
            restart_count=2,
            last_probe="System stats unavailable.",
        )

        self.assertEqual(snapshot.phase, "failed")
        self.assertEqual(snapshot.exit_code, 17)
        self.assertEqual(snapshot.restart_count, 2)
        self.assertEqual(snapshot.last_probe, "System stats unavailable.")
        lines = snapshot.stdout_tail + snapshot.stderr_tail
        self.assertLessEqual(len(lines), MAX_SAFE_LOG_LINES)
        self.assertLessEqual(
            len("\n".join(lines).encode("utf-8")),
            MAX_SAFE_TEXT_BYTES,
        )
        rendered = repr(snapshot)
        self.assertIn("[redacted]", rendered)
        self.assertIn("[local-root]", rendered)
        self.assertNotIn("raw-secret", rendered)
        self.assertNotIn("signed-secret", rendered)
        self.assertNotIn("/opt/private/comfy", rendered)
        self.assertNotIn("worker-secret", rendered)
        self.assertIn("�", rendered)
        with self.assertRaises(ValueError):
            diagnostics.feed("combined", b"not accepted")

    def test_comfy_startup_failure_carries_only_safe_bounded_diagnostics(self):
        from remote_worker.comfy import ComfyProcess, ComfyProcessError

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            comfy_root = root / "ComfyUI"
            comfy_root.mkdir()
            (comfy_root / "main.py").write_text(
                "raise SystemExit(17)\n",
                encoding="utf-8",
            )
            factory = ProcessFactory()
            comfy = ComfyProcess(
                comfy_root=comfy_root,
                working_root=root / "working",
                python_executable=sys.executable,
                process_factory=factory,
            )

            with self.assertRaises(ComfyProcessError) as caught:
                asyncio.run(comfy.start())

        _argv, options = factory.calls[0]
        self.assertIs(options["stdout"], asyncio.subprocess.PIPE)
        self.assertIs(options["stderr"], asyncio.subprocess.PIPE)
        diagnostic = caught.exception.diagnostic
        self.assertEqual(str(caught.exception), "Remote ComfyUI is unavailable.")
        self.assertEqual(diagnostic.phase, "failed")
        self.assertEqual(diagnostic.exit_code, 17)
        self.assertEqual(diagnostic.restart_count, 0)
        self.assertEqual(diagnostic.last_probe, "ComfyUI process exited.")
        rendered = repr(diagnostic)
        self.assertIn("[redacted]", rendered)
        self.assertNotIn("worker-secret", rendered)
        self.assertNotIn("secret", rendered.replace("[redacted]", ""))
        self.assertNotIn("/Users/private", rendered)
        self.assertNotIn(str(root), rendered)
        self.assertEqual(comfy.diagnostics(), diagnostic)


if __name__ == "__main__":
    unittest.main()
