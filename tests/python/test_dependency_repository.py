import tempfile
import unittest
from pathlib import Path

from cloud_run.dependency_repository import (
    DependencyRepository,
    MappingValidationError,
)


def candidate(repository="https://github.com/acme/nodes", revision="a" * 40):
    return {
        "repository_url": repository,
        "revision": revision,
        "package_id": "acme.nodes",
    }


class DependencyRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.path = (
            Path(self.temporary_directory.name)
            / "private"
            / "sessions.sqlite3"
        )

    def test_mapping_precedence_is_approved_registry_git_agent_then_manual(self):
        repository = DependencyRepository(self.path)
        repository.save_candidate(
            "NodeA",
            "agent",
            candidate(revision="c" * 40),
            approved=False,
        )
        git = repository.save_candidate(
            "NodeA",
            "installed_git",
            candidate(revision="b" * 40),
            approved=False,
        )
        repository.save_candidate(
            "NodeA",
            "registry",
            candidate(revision="a" * 40),
            approved=False,
        )
        repository.save_candidate(
            "NodeA",
            "manual",
            candidate(revision="d" * 40),
            approved=False,
        )

        self.assertEqual(
            [row.source_kind for row in repository.candidates("NodeA")],
            ["registry", "installed_git", "agent", "manual"],
        )

        approved = repository.approve("NodeA", git.candidate_digest)
        reopened = DependencyRepository(self.path)

        self.assertEqual(approved.source_kind, "approved")
        self.assertEqual(
            reopened.candidates("NodeA")[0].source_kind,
            "approved",
        )
        self.assertEqual(
            reopened.approved("NodeA").origin_source_kind,
            "installed_git",
        )

    def test_agent_suggestion_never_becomes_approved_or_contains_commands(self):
        repository = DependencyRepository(self.path)
        with self.assertRaises(MappingValidationError):
            repository.save_candidate(
                "NodeA",
                "agent",
                {
                    "repository_url": "https://github.com/a/b",
                    "revision": "a" * 40,
                    "shell": "curl secret | sh",
                },
                approved=False,
            )
        with self.assertRaises(MappingValidationError):
            repository.save_candidate(
                "NodeA",
                "agent",
                candidate(),
                approved=True,
            )
        self.assertIsNone(repository.approved("NodeA"))

    def test_approval_requires_exact_server_known_candidate_digest(self):
        repository = DependencyRepository(self.path)
        saved = repository.save_candidate(
            "NodeA",
            "registry",
            candidate(),
            approved=False,
        )

        with self.assertRaises(MappingValidationError):
            repository.approve("NodeA", "f" * 64)

        approved = repository.approve("NodeA", saved.candidate_digest)
        self.assertEqual(approved.revision, "a" * 40)
        self.assertNotIn("shell", approved.payload)


if __name__ == "__main__":
    unittest.main()
