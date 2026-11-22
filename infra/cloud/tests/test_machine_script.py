"""The machine's own `caseforge` command (infra/modules/common/machine-bootstrap/files/caseforge.sh), run locally with
systemctl and shutdown replaced by recorders, so every subcommand that does not need Docker is exercised."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "modules" / "common" / "machine-bootstrap" / "files" / "caseforge.sh"


class MachineScriptTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.conf = self.tmp / "machine.env"
        self.conf.write_text("STACK=full\nGIT_REF=main\nAUTO_STOP_HOURS=10\n")
        self.src = self.tmp / "src"
        self.src.mkdir()
        (self.src / "Makefile").write_text("full:\n\techo full\nsso:\n\techo sso\n")
        self.log = self.tmp / "calls"
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        for tool in ("systemctl", "shutdown"):
            stub = bin_dir / tool
            body = f'echo "{tool} $*" >> "{self.log}"'
            stub.write_text(f"#!/usr/bin/env bash\n{body}\n")
            stub.chmod(0o755)
        self.env = dict(os.environ, CASEFORGE_CONF=str(self.conf), CASEFORGE_SRC=str(self.src),
                        PATH=f"{bin_dir}:{os.environ['PATH']}", SYSTEMCTL="systemctl", SHUTDOWN="shutdown", CASEFORGE_SKIP_ROOT="1")

    def tearDown(self):
        self._tmp.cleanup()

    def run_script(self, *args, check=True):
        result = subprocess.run(["bash", str(SCRIPT), *args], env=self.env, text=True, capture_output=True, timeout=30)
        if check and result.returncode != 0:
            self.fail(f"caseforge {' '.join(args)} failed: {result.stderr}")
        return result

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_help_lists_the_commands(self):
        out = self.run_script("help").stdout
        for word in ("status", "logs", "restart", "update", "extend", "reset", "cloudcheck"):
            self.assertIn(f"caseforge {word}", out)

    def test_extend_reschedules_the_stop(self):
        out = self.run_script("extend", "3").stdout
        self.assertIn("stops itself in 3h", out)
        self.assertIn("shutdown -c", self.calls())
        self.assertTrue(any(c.startswith("shutdown -h +180 ") for c in self.calls()))

    def test_extend_zero_turns_it_off(self):
        self.assertIn("automatic stop is off", self.run_script("extend", "0").stdout)
        self.assertFalse(any(c.startswith("shutdown -h") for c in self.calls()))

    def test_extend_refuses_nonsense(self):
        self.assertNotEqual(self.run_script("extend", "soon", check=False).returncode, 0)

    def test_stack_switches_only_to_a_real_target(self):
        self.run_script("stack", "sso")
        self.assertIn("STACK=sso", self.conf.read_text())
        self.assertIn("systemctl restart caseforge.service", self.calls())
        bad = self.run_script("stack", "rm", check=False)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("no Makefile target", bad.stderr)

    def test_env_sets_and_replaces_values_privately(self):
        self.run_script("env", "ANTHROPIC_API_KEY=one")
        self.run_script("env", "OTHER=x")
        self.run_script("env", "ANTHROPIC_API_KEY=two")
        env_file = self.src / ".env"
        self.assertEqual(sorted(env_file.read_text().splitlines()), ["ANTHROPIC_API_KEY=two", "OTHER=x"])
        self.assertEqual(oct(env_file.stat().st_mode & 0o777), "0o600")
        self.assertNotEqual(self.run_script("env", "bad key=1", check=False).returncode, 0)

    def test_reset_needs_yes(self):
        result = self.run_script("reset", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reset --yes", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_escalation_cannot_loop(self):
        env = dict(self.env, CASEFORGE_SKIP_ROOT="0", CASEFORGE_ESCALATED="1")
        if os.getuid() == 0:
            self.skipTest("running as root: nothing to escalate")
        result = subprocess.run(["bash", str(SCRIPT), "extend", "1"], env=env, text=True, capture_output=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sudo did not provide it", result.stderr)

    def _stub(self, tool, body):
        stub = self.tmp / "bin" / tool
        stub.write_text(f"#!/usr/bin/env bash\n{body}\n")
        stub.chmod(0o755)

    def test_cloudcheck_on_aws_uses_the_setup_bucket_and_kms_through_the_host_network(self):
        doc = self.tmp / "identity.json"
        doc.write_text('{\n  "accountId" : "613112965612",\n  "region" : "us-east-1",\n  "instanceId" : "i-1"\n}\n')
        self._stub("curl", f'case "$*" in *api/token*) echo tok ;; *identity*) cat "{doc}" ;; *) exit 7 ;; esac')
        self._stub("docker", f'echo "docker $*" >> "{self.log}"; case "$1 $2" in "compose ps") echo c0ffee ;; inspect*) echo src-app ;; esac')
        self.run_script("cloudcheck")
        run = next(c for c in self.calls() if c.startswith("docker run"))
        self.assertIn("--network host", run)
        self.assertTrue(any(c.startswith("docker inspect --format {{.Config.Image}}") for c in self.calls()), self.calls())
        self.assertIn("KEY_PROVIDER=aws-kms", run)
        self.assertIn("AWS_REGION=us-east-1", run)
        self.assertTrue(run.endswith("src-app uv run --no-dev python -m app.cloudcheck all s3://caseforge-data-613112965612-us-east-1/"), run)

    def test_cloudcheck_elsewhere_needs_a_location(self):
        self._stub("curl", "exit 7")
        result = self.run_script("cloudcheck", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("give the storage location", result.stderr)
        self._stub("docker", f'echo "docker $*" >> "{self.log}"; case "$1 $2" in "compose ps") echo c0ffee ;; inspect*) echo src-app ;; esac')
        self.run_script("cloudcheck", "gs://bucket/check")
        run = next(c for c in self.calls() if c.startswith("docker run"))
        self.assertNotIn("aws-kms", run)
        self.assertTrue(run.endswith("all gs://bucket/check"))

    def test_update_installs_this_command_from_the_new_source(self):
        files = self.src / "infra" / "modules" / "common" / "machine-bootstrap" / "files"
        files.mkdir(parents=True)
        (files / "caseforge.sh").write_text("#!/usr/bin/env bash\necho new\n")
        self._stub("git", 'case "$*" in *"rev-parse --verify"*) exit 0 ;; *rev-parse*) echo abc123 ;; esac')
        (self.src / ".git").mkdir()
        import getpass
        with self.conf.open("a") as f:
            f.write(f"OWNER={getpass.getuser()}\n")
        target = self.tmp / "installed-caseforge"
        self.env["CASEFORGE_BIN"] = str(target)
        self.run_script("update", "main")
        self.assertEqual(target.read_text(), "#!/usr/bin/env bash\necho new\n")
        self.assertIn("systemctl restart caseforge.service", self.calls())

    def test_unknown_command(self):
        self.assertNotEqual(self.run_script("explode", check=False).returncode, 0)


if __name__ == "__main__":
    unittest.main()
