import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fakes  # noqa: F401  (puts the package on the path)

from caseforge_cloud import cli, costs, netaddr
from caseforge_cloud import ssh as sshmod
from caseforge_cloud.clouds import get_cloud
from caseforge_cloud.clouds.aws import Aws, operator_profile_block, write_operator_profile
from caseforge_cloud.clouds.base import NotSupported
from caseforge_cloud.lifecycle import UserError
from caseforge_cloud.tofu import Tofu, ToolError, parse_version, write_backend_config


class NetaddrTest(unittest.TestCase):
    def test_first_public_answer_wins(self):
        answers = {"a": "10.0.0.1\n", "b": "not an address", "c": "34.200.1.7\n"}
        self.assertIsNone(netaddr.public_ipv4(["a", "b"], fetch=answers.__getitem__))
        self.assertEqual(netaddr.public_ipv4(["a", "b", "c"], fetch=answers.__getitem__), "34.200.1.7")

    def test_network_errors_are_skipped(self):
        def fetch(url):
            raise OSError("offline")
        self.assertIsNone(netaddr.public_ipv4(["x"], fetch=fetch))

    def test_falls_back_to_curl_when_python_cannot_verify_https(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("CERTIFICATE_VERIFY_FAILED")), \
             mock.patch("shutil.which", return_value="/usr/bin/curl"), \
             mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "34.200.1.7\n", "")) as run:
            self.assertEqual(netaddr.public_ipv4(["https://checkip.amazonaws.com"]), "34.200.1.7")
        self.assertEqual(run.call_args[0][0][0], "curl")

    def test_normalise(self):
        self.assertEqual(netaddr.normalise_cidr("198.51.100.7"), "198.51.100.7/32")
        self.assertEqual(netaddr.normalise_cidr("192.0.2.9/28"), "192.0.2.0/28")
        with self.assertRaises(ValueError):
            netaddr.normalise_cidr("0.0.0.0/0")


class SshTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_include_goes_first_is_idempotent_and_keeps_the_rest(self):
        user = self.tmp / "ssh" / "config"
        user.parent.mkdir()
        user.write_text("Host *\n  AddKeysToAgent yes\n")
        target = self.tmp / "cf" / "config"
        self.assertTrue(sshmod.ensure_included(user, target))
        first = user.read_text()
        self.assertTrue(first.startswith(sshmod.MARK_BEGIN))
        self.assertIn("Host *\n  AddKeysToAgent yes", first)
        self.assertFalse(sshmod.ensure_included(user, target))
        self.assertEqual(user.read_text(), first)
        self.assertEqual(oct(user.stat().st_mode & 0o777), "0o600")

    def test_include_moves_when_the_target_changes(self):
        user = self.tmp / "config"
        sshmod.ensure_included(user, self.tmp / "a")
        sshmod.ensure_included(user, self.tmp / "b")
        text = user.read_text()
        self.assertEqual(text.count(sshmod.MARK_BEGIN), 1)
        self.assertIn(str(self.tmp / "b"), text)

    def test_host_entry(self):
        entry = sshmod.HostEntry("caseforge", "203.0.113.10", "caseforge", Path("/k/id"), Path("/k/kh"), "caseforge-i-1",
                                 {"keycloak": 8080, "app": 8000})
        text = entry.render()
        self.assertIn('IdentityFile "/k/id"', text)
        self.assertLess(text.index("LocalForward 8000"), text.index("LocalForward 8080"))
        self.assertIn("StrictHostKeyChecking accept-new", text)

    def test_commands_never_forward_and_interactive_does(self):
        calls = []
        s = sshmod.Ssh(Path("/c"), "caseforge", runner=lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))
        s.run(["caseforge", "status"])
        self.assertIn("ClearAllForwardings=yes", calls[0])
        self.assertEqual(calls[0][-3:], ["--", "caseforge", "status"])
        self.assertNotIn("ClearAllForwardings=yes", s.interactive())

    def test_real_keygen(self):
        if not subprocess.run(["which", "ssh-keygen"], capture_output=True).returncode == 0:
            self.skipTest("no ssh-keygen")
        key = sshmod.ensure_key(self.tmp / "m", "caseforge-test")
        self.assertTrue(sshmod.public_key(key).startswith("ssh-ed25519 "))
        self.assertEqual(oct((self.tmp / "m").stat().st_mode & 0o777), "0o700")
        self.assertEqual(sshmod.ensure_key(self.tmp / "m", "again"), key)


class AwsTest(unittest.TestCase):
    def test_operator_profile_replaced_in_place(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config"
            path.write_text("[default]\nregion = eu-west-2\n")
            write_operator_profile(path, operator_profile_block("arn:1", "us-east-1", "default"))
            write_operator_profile(path, operator_profile_block("arn:2", "us-east-1", "default"))
            text = path.read_text()
            self.assertEqual(text.count("[profile caseforge-operator]"), 1)
            self.assertIn("role_arn = arn:2", text)
            self.assertTrue(text.startswith("[default]\nregion = eu-west-2\n"))

    def test_backend_and_vars(self):
        aws = Aws()
        setup = aws.setup_summary(fakes.SETUP_OUTPUTS)
        backend = aws.backend_settings(setup, "caseforge-demo")
        self.assertEqual(backend["key"], "machine/caseforge-demo.tfstate")
        self.assertTrue(backend["use_lockfile"])
        self.assertEqual(aws.machine_vars(setup)["instance_profile_name"], "caseforge-machine")

    def test_pending_clouds_explain(self):
        with self.assertRaises(NotSupported) as ctx:
            get_cloud("azure")
        self.assertIn("infra/modules/azure/machine", str(ctx.exception))

    def test_cost_lines(self):
        text = costs.describe(Aws(), "large", 150)
        self.assertIn("stopped:  about $12 a month", text)
        self.assertIn("down:     nothing", text)


class TofuTest(unittest.TestCase):
    def test_version(self):
        self.assertEqual(parse_version("OpenTofu v1.11.5\non linux_amd64"), (1, 11, 5))
        old = Tofu(Path("."), Path(tempfile.mkdtemp()), runner=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "OpenTofu v1.8.0\n", ""))
        with self.assertRaises(ToolError):
            old.check_version()

    def test_plan_exit_codes(self):
        for code, changed in ((0, False), (2, True)):
            t = Tofu(Path("."), Path(tempfile.mkdtemp()), runner=lambda cmd, code=code, **kw: subprocess.CompletedProcess(cmd, code))
            self.assertEqual(t.plan(Path("p")), changed)
        t = Tofu(Path("."), Path(tempfile.mkdtemp()), runner=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1))
        with self.assertRaises(ToolError):
            t.plan(Path("p"))

    def test_data_dir_is_per_root(self):
        seen = {}
        t = Tofu(Path("/r"), Path(tempfile.mkdtemp()) / "d", env={"AWS_PROFILE": "x"},
                 runner=lambda cmd, env, **kw: seen.update(env) or subprocess.CompletedProcess(cmd, 0, "{}", ""))
        t.outputs()
        self.assertEqual(seen["TF_DATA_DIR"], str(t.data_dir))
        self.assertEqual(seen["AWS_PROFILE"], "x")

    def test_backend_file(self):
        with tempfile.TemporaryDirectory() as d:
            text = write_backend_config(Path(d) / "b.hcl", {"bucket": "b", "use_lockfile": True, "n": 3}).read_text()
            self.assertEqual(text, 'bucket = "b"\nn = 3\nuse_lockfile = true\n')


class CliTest(unittest.TestCase):
    def test_refuses_to_change_anything_without_a_terminal_or_yes(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=False):
            with self.assertRaises(UserError):
                cli.run(["down"], life=mock.Mock(assume_yes=False))

    def test_main_prints_errors_without_traceback(self):
        with mock.patch("sys.stderr") as err:
            self.assertEqual(cli.main(["up", "--cloud", "gcp", "--yes"]), 1)
        self.assertTrue(any("not implemented" in str(c) for c in err.write.call_args_list))

    def test_up_passes_options(self):
        life = mock.Mock(assume_yes=True)
        cli.run(["up", "--size", "xlarge", "--stack", "sso", "--ref", "v1", "--allow", "192.0.2.1", "--no-wait"], life=life)
        opts = life.up.call_args[0][0]
        self.assertEqual((opts.size, opts.stack, opts.git_ref, opts.allowed_cidrs), ("xlarge", "sso", "v1", ["192.0.2.1"]))
        self.assertEqual(life.up.call_args[1], {"wait": False})

    def test_env_value_goes_on_stdin_not_argv(self):
        life = mock.Mock(assume_yes=True)
        ssh = life.ssh_factory.return_value
        ssh.run.return_value = subprocess.CompletedProcess([], 0)
        with mock.patch.object(sys, "stdin") as stdin:
            stdin.readline.return_value = "sk-secret\n"
            cli.run(["env", "ANTHROPIC_API_KEY"], life=life)
        args, kwargs = ssh.run.call_args
        self.assertNotIn("sk-secret", " ".join(args[0]))
        self.assertEqual(kwargs["stdin"], "sk-secret\n")


if __name__ == "__main__":
    unittest.main()
