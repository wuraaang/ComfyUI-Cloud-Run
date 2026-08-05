import asyncio
from copy import deepcopy
import hashlib
import importlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile


AGENT_URL = (
    "https://cdn.comfy.org/artokun/comfyui-agent-panel/0.11.38/node.zip"
)
EFFICIENCY_URL = (
    "https://cdn.comfy.org/jags111/efficiency-nodes-comfyui/1.0.9/node.zip"
)
WHEEL_URL = (
    "https://files.pythonhosted.org/packages/0f/2f/"
    "f32aa85591882378bb43caa09363f3ed97df399369a5144c7f19f2275bc0/"
    "simpleeval-1.0.7-py3-none-any.whl"
)
FIXTURES = Path(__file__).parents[1] / "fixtures/certified-baseline"
LOADER = (
    b"NODE_CLASS_MAPPINGS = {}\n"
    b"NODE_DISPLAY_NAME_MAPPINGS = {}\n"
    b'WEB_DIRECTORY = "./web"\n\n'
    b"__all__ = [\"NODE_CLASS_MAPPINGS\", "
    b'"NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]\n'
)


def zip_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, body in files.items():
            archive.writestr(path, body)
    return output.getvalue()


def file_records(files):
    return [
        {
            "path": path,
            "size_bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }
        for path, body in sorted(files.items())
    ]


class CertifiedBaselineTests(unittest.TestCase):
    def setUp(self):
        try:
            self.baseline = importlib.import_module("cloud_run.certified_baseline")
        except ModuleNotFoundError as exc:
            self.fail(f"certified baseline module is missing: {exc}")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def _official_lock(self):
        return json.loads(
            (Path(self.baseline.__file__).with_name(
                "certified_baseline.lock.json"
            )).read_text(encoding="utf-8")
        )

    def _fixture_lock_and_fetcher(self, *, agent_files=None, efficiency_files=None):
        classes = tuple(self._official_lock()["custom_nodes"][0]["class_types"])
        agent_files = agent_files or {
            "LICENSE": b"MIT fixture\n",
            "web/panel.js": b"export const panel = true;\n",
        }
        efficiency_files = efficiency_files or {
            "LICENSE": b"GPL-3.0-only fixture\n",
            "__init__.py": b"from .efficiency_nodes import NODE_CLASS_MAPPINGS\n",
            "efficiency_nodes.py": (
                "NODE_CLASS_MAPPINGS = {\n"
                + "".join(f"    {name!r}: object,\n" for name in classes)
                + "}\n"
            ).encode("utf-8"),
            "js/efficiency.js": b"export const efficiency = true;\n",
        }
        wheel = b"synthetic simpleeval wheel\n"
        agent_source = zip_bytes(agent_files)
        efficiency_source = zip_bytes(efficiency_files)
        record = deepcopy(self._official_lock())
        agent = record["ui_packages"][0]
        efficiency = record["custom_nodes"][0]
        agent["source_archive"].update(
            size_bytes=len(agent_source),
            sha256=hashlib.sha256(agent_source).hexdigest(),
            files=file_records(agent_files),
        )
        agent["permitted_paths"] = ["LICENSE", "web/**"]
        agent["web"].update(
            file_count=1,
            size_bytes=len(agent_files["web/panel.js"]),
        )
        efficiency["source_archive"].update(
            size_bytes=len(efficiency_source),
            sha256=hashlib.sha256(efficiency_source).hexdigest(),
            files=file_records(efficiency_files),
        )
        efficiency["permitted_paths"] = sorted(efficiency_files)
        efficiency["web"].update(
            file_count=1,
            size_bytes=len(efficiency_files["js/efficiency.js"]),
        )
        efficiency["curated_archive"]["file_count"] = len(efficiency_files)
        efficiency["wheels"][0].update(
            size_bytes=len(wheel),
            sha256=hashlib.sha256(wheel).hexdigest(),
        )

        staging = Path(tempfile.mkdtemp(prefix="expected-", dir=self.root))
        (staging / "agent/web").mkdir(parents=True)
        (staging / "efficiency/js").mkdir(parents=True)
        for path, body in agent_files.items():
            target = staging / "agent" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        (staging / "agent/__init__.py").write_bytes(LOADER)
        for path, body in efficiency_files.items():
            target = staging / "efficiency" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        for package_name, package_record in (
            ("agent", agent),
            ("efficiency", efficiency),
        ):
            digest = self.baseline.build_package_archive(
                staging / package_name,
                staging / f"{package_name}.tar",
            )
            package_record["curated_archive"].update(
                size_bytes=digest.size_bytes,
                sha256=digest.sha256,
            )
        agent["web"]["sha256"] = self.baseline.canonical_tree_sha256(
            staging / "agent/web"
        )
        efficiency["web"]["sha256"] = self.baseline.canonical_tree_sha256(
            staging / "efficiency/js"
        )

        lock_path = self.root / "fixture.lock.json"
        lock_path.write_text(json.dumps(record), encoding="utf-8")
        fetched = {
            AGENT_URL: agent_source,
            EFFICIENCY_URL: efficiency_source,
            WHEEL_URL: wheel,
        }
        calls = []

        async def fetcher(url):
            calls.append(url)
            return fetched[url]

        return lock_path, fetcher, calls, record

    def _resolve(self, lock_path, fetcher):
        lock_digest = hashlib.sha256(lock_path.read_bytes()).hexdigest()
        with (
            mock.patch.object(self.baseline, "_LOCK_PATH", lock_path),
            mock.patch.object(
                self.baseline,
                "_OFFICIAL_LOCK_SHA256",
                lock_digest,
            ),
        ):
            resolver = self.baseline.CertifiedBaselineResolver()
        return asyncio.run(
            resolver.resolve(fetcher=fetcher, cache_root=self.root / "cache")
        )

    def _construct_from_lock(self, lock_path, *, trust_bytes=False):
        patches = [mock.patch.object(self.baseline, "_LOCK_PATH", lock_path)]
        if trust_bytes:
            patches.append(
                mock.patch.object(
                    self.baseline,
                    "_OFFICIAL_LOCK_SHA256",
                    hashlib.sha256(lock_path.read_bytes()).hexdigest(),
                )
            )
        with patches[0]:
            if len(patches) == 1:
                return self.baseline.CertifiedBaselineResolver()
            with patches[1]:
                return self.baseline.CertifiedBaselineResolver()

    def test_production_constructor_exposes_no_identity_bypass(self):
        official = Path(self.baseline.__file__).with_name(
            "certified_baseline.lock.json"
        )
        for keyword in ("lock_path", "assets_root", "_allow_test_artifacts"):
            with self.subTest(keyword=keyword):
                with self.assertRaises(TypeError):
                    self.baseline.CertifiedBaselineResolver(
                        **{keyword: official}
                    )

    def test_shipped_lock_is_accepted_with_exact_audited_identity(self):
        resolver = self.baseline.CertifiedBaselineResolver()

        self.assertEqual(resolver._lock["comfyui_frontend_version"], "1.47.10")
        self.assertEqual(
            resolver._lock["ui_packages"][0]["source_archive"]["sha256"],
            "81e33c49cd65cfb85cf42e604629e25e237f3903958329f015eeb785eb913df1",
        )
        self.assertEqual(
            resolver._lock["custom_nodes"][0]["curated_archive"]["sha256"],
            "0fc239d03f09fc9a78a087b7a6514dafb7b0a614ed5f73ef24f22dd56006eebe",
        )

    def test_shipped_web_claims_match_worker_canonical_records(self):
        lock = self._official_lock()
        for package in (
            lock["ui_packages"][0],
            lock["custom_nodes"][0],
        ):
            prefix = package["web"]["root"].rstrip("/") + "/"
            records = [
                {
                    "mode": 0o644,
                    "path": item["path"].removeprefix(prefix),
                    "sha256": item["sha256"],
                    "size_bytes": item["size_bytes"],
                }
                for item in package["source_archive"]["files"]
                if item["path"].startswith(prefix)
            ]
            measured = hashlib.sha256(
                json.dumps(
                    records,
                    allow_nan=False,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            self.assertEqual(measured, package["web"]["sha256"])

        hermes = lock["ui_packages"][1]
        hermes_root = (
            Path(self.baseline.__file__).with_name("baseline_assets")
            / hermes["package_id"]
            / hermes["web"]["root"]
        )
        self.assertEqual(
            self.baseline.canonical_tree_sha256(hermes_root),
            hermes["web"]["sha256"],
        )
        self.assertFalse(hasattr(self.baseline, "_KNOWN_WEB_DIGESTS"))

    def test_cache_child_symlinks_are_rejected_without_writing_outside(self):
        for child in ("downloads", "archives"):
            with self.subTest(child=child):
                lock_path, fetcher, _, _ = self._fixture_lock_and_fetcher()
                cache = self.root / ("cache-" + child)
                outside = self.root / ("outside-" + child)
                cache.mkdir(exist_ok=True)
                outside.mkdir()
                (cache / child).symlink_to(outside, target_is_directory=True)
                lock_digest = hashlib.sha256(lock_path.read_bytes()).hexdigest()
                with (
                    mock.patch.object(self.baseline, "_LOCK_PATH", lock_path),
                    mock.patch.object(
                        self.baseline,
                        "_OFFICIAL_LOCK_SHA256",
                        lock_digest,
                    ),
                    self.assertRaises(
                        self.baseline.CertifiedBaselineUnavailable
                    ),
                ):
                    asyncio.run(
                        self.baseline.CertifiedBaselineResolver().resolve(
                            fetcher=fetcher,
                            cache_root=cache,
                        )
                    )
                self.assertEqual(tuple(outside.iterdir()), ())

    def test_staging_failure_is_normalized_to_baseline_unavailable(self):
        lock_path, fetcher, _, _ = self._fixture_lock_and_fetcher()

        with mock.patch.object(
            self.baseline.tempfile,
            "TemporaryDirectory",
            side_effect=OSError("private cache unavailable"),
        ):
            try:
                self._resolve(lock_path, fetcher)
            except self.baseline.CertifiedBaselineUnavailable:
                pass
            except OSError as exc:
                self.fail(f"raw staging error escaped: {exc}")
            else:
                self.fail("staging failure was accepted")

    def test_resolves_exact_agent_01138_hermes_and_efficiency_109(self):
        lock_path, fetcher, calls, _ = self._fixture_lock_and_fetcher()

        result = self._resolve(lock_path, fetcher)

        self.assertEqual(
            [(item.package_id, item.revision) for item in result.ui_packages],
            [
                (
                    "comfyui-agent-panel",
                    "e4de6a5a2e8fbcde166b5fe2983bf3404ca10e0b",
                ),
                (
                    "hermes-nous",
                    "sha256:743a5a2505cab78c3502c0fcf50b83780790a5a5b14e7fc1be3e438a3ca7c82f",
                ),
            ],
        )
        self.assertEqual(
            [(item.package_id, item.revision) for item in result.custom_nodes],
            [
                (
                    "efficiency-nodes-comfyui",
                    "835bbe14627cccc871822e804c65c734960d3c6e",
                )
            ],
        )
        self.assertEqual(len(result.local_artifacts), 4)
        self.assertRegex(result.digest, r"^[0-9a-f]{64}$")
        self.assertEqual(calls, [AGENT_URL, EFFICIENCY_URL, WHEEL_URL])
        class_fixture = json.loads(
            (FIXTURES / "efficiency-nodes-1.0.9/CLASS_TYPES.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            tuple(class_fixture["class_types"]),
            result.custom_nodes[0].provided_class_types,
        )
        self.assertTrue(
            all(
                item.archive.source.kind == "local-upload"
                for item in (*result.ui_packages, *result.custom_nodes)
            )
        )

    def test_rejects_missing_revision_archive_digest_or_wheel_closure(self):
        for mutation in ("revision", "archive_digest", "wheel_closure"):
            with self.subTest(mutation=mutation):
                lock = self._official_lock()
                if mutation == "revision":
                    del lock["ui_packages"][0]["revision"]
                elif mutation == "archive_digest":
                    del lock["custom_nodes"][0]["source_archive"]["sha256"]
                else:
                    lock["custom_nodes"][0]["wheels"] = []
                path = self.root / f"{mutation}.json"
                path.write_text(json.dumps(lock), encoding="utf-8")
                with self.assertRaises(self.baseline.CertifiedBaselineUnavailable):
                    self._construct_from_lock(path)

    def test_rejects_changed_allowed_file_or_extra_executable_file(self):
        lock_path, fetcher, _, _ = self._fixture_lock_and_fetcher()
        original = asyncio.run(fetcher(AGENT_URL))

        async def changed(url):
            if url == AGENT_URL:
                files = {"LICENSE": b"MIT fixture\n", "web/panel.js": b"changed\n"}
                return zip_bytes(files)
            return await fetcher(url)

        with self.assertRaises(self.baseline.CertifiedBaselineUnavailable):
            self._resolve(lock_path, changed)

        self.assertTrue(original)
        agent_files = {
            "LICENSE": b"MIT fixture\n",
            "web/panel.js": b"export const panel = true;\n",
            "web/extra.py": b"raise RuntimeError('unreviewed')\n",
        }
        extra_lock, extra_fetcher, _, _ = self._fixture_lock_and_fetcher(
            agent_files=agent_files
        )
        with self.assertRaises(self.baseline.CertifiedBaselineUnavailable):
            self._resolve(extra_lock, extra_fetcher)

    def test_agent_archive_contains_only_minimal_loader_and_reviewed_web_root(self):
        lock_path, fetcher, _, _ = self._fixture_lock_and_fetcher()
        result = self._resolve(lock_path, fetcher)
        artifact = next(
            item for item in result.local_artifacts
            if item.artifact_id.startswith("ui-agent-panel-")
        )
        with tarfile.open(artifact.private_path, "r:") as archive:
            names = tuple(archive.getnames())
            loader = archive.extractfile("__init__.py").read()
        self.assertEqual(names, ("LICENSE", "__init__.py", "web/panel.js"))
        self.assertEqual(loader, LOADER)
        self.assertEqual(
            loader,
            (FIXTURES / "agent-panel-0.11.38/minimal_loader.py").read_bytes(),
        )
        self.assertNotIn("py/", "\n".join(names))

    def test_efficiency_never_uses_mutable_installed_directory(self):
        lock_path, fetcher, _, _ = self._fixture_lock_and_fetcher()
        forbidden = Path("/mutable/Desktop/custom_nodes/efficiency-nodes-comfyui")
        with mock.patch.object(Path, "home", return_value=forbidden):
            result = self._resolve(lock_path, fetcher)
        self.assertNotIn(str(forbidden), repr(result))
        self.assertTrue(
            all(
                Path(item.private_path).resolve().is_relative_to(
                    (self.root / "cache").resolve()
                )
                for item in result.local_artifacts
            )
        )

    def test_efficiency_proves_clip_interrogator_unused_before_excluding_it(self):
        files = {
            "LICENSE": b"GPL-3.0-only fixture\n",
            "__init__.py": b"from .efficiency_nodes import NODE_CLASS_MAPPINGS\n",
            "efficiency_nodes.py": b"import clip_interrogator\nNODE_CLASS_MAPPINGS = {}\n",
            "js/efficiency.js": b"export const efficiency = true;\n",
        }
        lock_path, fetcher, _, _ = self._fixture_lock_and_fetcher(
            efficiency_files=files
        )
        with self.assertRaises(self.baseline.CertifiedBaselineUnavailable):
            self._resolve(lock_path, fetcher)

    def test_protected_runtime_distribution_in_wheel_lock_is_rejected(self):
        lock = self._official_lock()
        lock["custom_nodes"][0]["wheels"][0]["filename"] = (
            "torch-2.8.0-py3-none-any.whl"
        )
        path = self.root / "protected.json"
        path.write_text(json.dumps(lock), encoding="utf-8")
        with self.assertRaises(self.baseline.CertifiedBaselineUnavailable):
            self._construct_from_lock(path, trust_bytes=True)

    def test_lock_requires_frontend_14710(self):
        lock = self._official_lock()
        lock["comfyui_frontend_version"] = "1.47.11"
        path = self.root / "frontend.json"
        path.write_text(json.dumps(lock), encoding="utf-8")
        with self.assertRaises(self.baseline.CertifiedBaselineUnavailable):
            self._construct_from_lock(path)


if __name__ == "__main__":
    unittest.main()
