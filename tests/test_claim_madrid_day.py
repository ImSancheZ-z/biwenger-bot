"""La reserva del día de Madrid: un solo run real y el manual siempre pasa."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "claim_madrid_day.sh"


def _madrid_today() -> str:
    return subprocess.check_output(
        ["date", "+%F"],
        env={**os.environ, "TZ": "Europe/Madrid"},
        text=True,
    ).strip()


def _madrid_yesterday() -> str:
    return subprocess.check_output(
        ["date", "-d", "yesterday", "+%F"],
        env={**os.environ, "TZ": "Europe/Madrid"},
        text=True,
    ).strip()


class ClaimMadridDayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.origin = root / "origin.git"
        gitconfig = root / "gitconfig"
        gitconfig.write_text(
            "\n".join(
                [
                    "[user]",
                    "name = test",
                    "email = test@example.com",
                    "[init]",
                    "defaultBranch = master",
                    "[commit]",
                    "gpgsign = false",
                    "[safe]",
                    "directory = *",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": str(gitconfig),
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
        subprocess.run(
            ["git", "init", "--bare", "-b", "master", str(self.origin)],
            check=True,
            env=self.env,
            capture_output=True,
            text=True,
        )
        seed = root / "seed"
        self._git_init(seed)
        self._git(seed, "commit", "--allow-empty", "-m", "init")
        self._git(seed, "remote", "add", "origin", str(self.origin))
        self._git(seed, "push", "-u", "origin", "master")
        self.today = _madrid_today()
        self.yesterday = _madrid_yesterday()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _git_init(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "init", "-b", "master", str(path)],
            check=True,
            env=self.env,
            capture_output=True,
            text=True,
        )

    def _git(self, repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=check,
            env=self.env,
            capture_output=True,
            text=True,
        )

    def _clone(self, name: str) -> Path:
        path = Path(self.tmp.name) / name
        subprocess.run(
            ["git", "clone", str(self.origin), str(path)],
            check=True,
            env=self.env,
            capture_output=True,
            text=True,
        )
        return path

    def _run(self, repo: Path, event: str, *args: str) -> subprocess.CompletedProcess[str]:
        output = repo / "github_output.txt"
        output.write_text("", encoding="utf-8")
        env = {
            **self.env,
            "GITHUB_OUTPUT": str(output),
            "GITHUB_EVENT_NAME": event,
            "GITHUB_REF_NAME": "master",
            "GITHUB_WORKSPACE": str(repo),
        }
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
        )

    def _claim(self, repo: Path, marker: str, subject: str, event: str) -> subprocess.CompletedProcess[str]:
        return self._run(repo, event, marker, subject)

    def _flag(self, repo: Path) -> str:
        return (repo / "github_output.txt").read_text(encoding="utf-8").strip()

    def _tip_file(self, marker: str) -> str:
        proc = self._git(self.origin, "show", f"master:{marker}", check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_schedule_claims_today_once_and_second_run_skips(self) -> None:
        first = self._clone("first")
        marker = ".github/state/daily-snapshot-date"
        subject = "chore: reservar snapshot diario"
        proc = self._claim(first, marker, subject, "schedule")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self._flag(first), "run=true")
        self.assertEqual(self._tip_file(marker), self.today + "\n")
        message = self._git(self.origin, "log", "-1", "--format=%s").stdout.strip()
        self.assertEqual(message, f"{subject} {self.today}")

        second = self._clone("second")
        before = self._git(self.origin, "rev-parse", "master").stdout.strip()
        proc = self._claim(second, marker, subject, "schedule")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self._flag(second), "run=false")
        self.assertEqual(self._git(self.origin, "rev-parse", "master").stdout.strip(), before)
        self.assertIn("ya está reservado", proc.stdout)

    def test_yesterday_does_not_count_as_today(self) -> None:
        repo = self._clone("yesterday")
        marker = ".github/state/daily-snapshot-date"
        path = repo / marker
        path.parent.mkdir(parents=True)
        path.write_text(self.yesterday + "\n", encoding="utf-8")
        self._git(repo, "add", "--", marker)
        self._git(repo, "commit", "-m", "marcador viejo")
        self._git(repo, "push", "origin", "HEAD:master")

        proc = self._claim(repo, marker, "chore: reservar snapshot diario", "schedule")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self._flag(repo), "run=true")
        self.assertEqual(self._tip_file(marker), self.today + "\n")

    def test_manual_run_bypasses_todays_marker_without_another_claim(self) -> None:
        repo = self._clone("manual")
        marker = ".github/state/clause-alerts-date"
        subject = "chore: reservar avisos de cláusulas"
        proc = self._claim(repo, marker, subject, "schedule")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        before = self._git(self.origin, "rev-parse", "master").stdout.strip()

        again = self._clone("manual-again")
        proc = self._claim(again, marker, subject, "workflow_dispatch")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self._flag(again), "run=true")
        self.assertEqual(self._git(self.origin, "rev-parse", "master").stdout.strip(), before)
        self.assertIn("disparo manual", proc.stdout.lower())

    def test_claim_commit_contains_only_the_marker(self) -> None:
        repo = self._clone("dirty")
        (repo / "other.txt").write_text("uno\n", encoding="utf-8")
        self._git(repo, "add", "--", "other.txt")
        self._git(repo, "commit", "-m", "other")
        self._git(repo, "push", "origin", "HEAD:master")
        with (repo / "other.txt").open("a", encoding="utf-8") as fh:
            fh.write("sucio\n")
        (repo / "staged.txt").write_text("no entrar\n", encoding="utf-8")
        self._git(repo, "add", "--", "staged.txt")
        (repo / "junk.txt").write_text("untracked\n", encoding="utf-8")

        marker = ".github/state/daily-snapshot-date"
        proc = self._claim(repo, marker, "chore: reservar snapshot diario", "schedule")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        names = self._git(self.origin, "show", "--name-only", "--format=", "master").stdout.split()
        self.assertEqual(names, [marker])
        self.assertIn("sucio", (repo / "other.txt").read_text(encoding="utf-8"))
        self.assertTrue((repo / "junk.txt").exists())

    def test_parallel_same_marker_lets_exactly_one_run(self) -> None:
        left = self._clone("race-a")
        right = self._clone("race-b")
        marker = ".github/state/daily-snapshot-date"
        subject = "chore: reservar snapshot diario"
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._claim, left, marker, subject, "schedule"),
                pool.submit(self._claim, right, marker, subject, "schedule"),
            ]
            results = [future.result() for future in futures]
        for proc in results:
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        flags = {self._flag(left), self._flag(right)}
        self.assertEqual(flags, {"run=true", "run=false"})
        self.assertEqual(self._tip_file(marker), self.today + "\n")
        count = self._git(self.origin, "rev-list", "--count", "master").stdout.strip()
        # init + un solo commit de reserva
        self.assertEqual(count, "2")

    def test_parallel_distinct_markers_both_run(self) -> None:
        left = self._clone("snap")
        right = self._clone("alerts")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    self._claim,
                    left,
                    ".github/state/daily-snapshot-date",
                    "chore: reservar snapshot diario",
                    "schedule",
                ),
                pool.submit(
                    self._claim,
                    right,
                    ".github/state/clause-alerts-date",
                    "chore: reservar avisos de cláusulas",
                    "schedule",
                ),
            ]
            results = [future.result() for future in futures]
        for proc in results:
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self._flag(left), "run=true")
        self.assertEqual(self._flag(right), "run=true")
        self.assertEqual(self._tip_file(".github/state/daily-snapshot-date"), self.today + "\n")
        self.assertEqual(self._tip_file(".github/state/clause-alerts-date"), self.today + "\n")

    def test_push_rebases_when_the_other_workflow_moved_master(self) -> None:
        left = self._clone("push-a")
        right = self._clone("push-b")
        (left / "a.txt").write_text("a\n", encoding="utf-8")
        self._git(left, "add", "--", "a.txt")
        self._git(left, "commit", "-m", "commit a")
        (right / "b.txt").write_text("b\n", encoding="utf-8")
        self._git(right, "add", "--", "b.txt")
        self._git(right, "commit", "-m", "commit b")
        self._git(right, "push", "origin", "HEAD:master")

        env = {
            **self.env,
            "GITHUB_REF_NAME": "master",
            "GITHUB_WORKSPACE": str(left),
        }
        proc = subprocess.run(
            ["bash", str(SCRIPT), "push"],
            cwd=left,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self._git(self.origin, "show", "master:a.txt").stdout, "a\n")
        self.assertEqual(self._git(self.origin, "show", "master:b.txt").stdout, "b\n")

    def test_peek_reads_the_marker_without_committing(self) -> None:
        repo = self._clone("peek")
        marker = ".github/state/clause-alerts-date"
        before = self._git(self.origin, "rev-parse", "master").stdout.strip()
        proc = self._run(repo, "schedule", "peek", marker)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self._flag(repo), "run=true")
        self.assertEqual(self._git(self.origin, "rev-parse", "master").stdout.strip(), before)
        self.assertIn("aún no está reservado", proc.stdout)

        proc = self._claim(repo, marker, "chore: reservar avisos de cláusulas", "schedule")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        reserved = self._git(self.origin, "rev-parse", "master").stdout.strip()

        again = self._clone("peek-again")
        proc = self._run(again, "schedule", "peek", marker)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self._flag(again), "run=false")
        proc = self._run(again, "workflow_dispatch", "peek", marker)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self._flag(again), "run=true")
        self.assertIn("disparo manual", proc.stdout.lower())
        self.assertEqual(self._git(self.origin, "rev-parse", "master").stdout.strip(), reserved)

    def test_bad_args_fail_before_touching_git(self) -> None:
        proc = subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(proc.returncode, 2)
        proc = subprocess.run(
            ["bash", str(SCRIPT), "../secret", "asunto"],
            capture_output=True,
            text=True,
            env={**self.env, "GITHUB_OUTPUT": str(Path(self.tmp.name) / "out.txt"), "GITHUB_REF_NAME": "master"},
        )
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
