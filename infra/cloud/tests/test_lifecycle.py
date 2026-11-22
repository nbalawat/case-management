import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fakes import World

from caseforge_cloud.lifecycle import Options, UserError


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.world = World(self.tmp)
        env = {"AWS_CONFIG_FILE": str(self.tmp / "aws-config"), "AWS_PROFILE": "admin"}
        self._env = mock.patch.dict(os.environ, env)
        self._env.start()
        os.environ.pop("AWS_ACCESS_KEY_ID", None)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def setup_done(self, **kw):
        life = self.world.lifecycle(**kw)
        life.setup("us-east-1")
        return life

    # ---- setup
    def test_setup_saves_outputs_writes_operator_profile_and_ssh_include(self):
        (self.tmp / "dot-ssh").mkdir()
        (self.tmp / "dot-ssh" / "config").write_text("Host other\n  HostName example.com\n")
        life = self.setup_done()
        setup = life.home.setup_outputs("aws")
        self.assertEqual(setup["operator_role_arn"], "arn:aws:iam::123456789012:role/caseforge-machine-operator")
        self.assertTrue(setup["operator_profile_written"])
        profile = (self.tmp / "aws-config").read_text()
        self.assertIn("[profile caseforge-operator]", profile)
        self.assertIn("source_profile = admin", profile)
        ssh_config = (self.tmp / "dot-ssh" / "config").read_text()
        self.assertTrue(ssh_config.startswith("# >>> caseforge"), "the Include must come first to apply globally")
        self.assertIn("Host other", ssh_config)
        self.assertTrue((self.tmp / "dot-ssh" / "config.caseforge-backup").exists())

    def test_setup_survives_an_unwritable_ssh_config(self):
        life = self.world.lifecycle()
        with mock.patch("caseforge_cloud.ssh.ensure_included", side_effect=PermissionError(13, "Permission denied")):
            life.setup("us-east-1")
        self.assertTrue(life.home.setup_outputs("aws"))
        self.assertTrue(any("sudo chown $(whoami) ~/.ssh" in line for line in self.world.output))

    def test_setup_twice_changes_nothing_the_second_time(self):
        self.setup_done()
        before = (self.tmp / "aws-config").read_text()
        self.setup_done()
        self.assertEqual((self.tmp / "aws-config").read_text(), before)
        applies = [c for c in self.world.calls if c[0] == "apply"]
        self.assertEqual(len(applies), 1)

    def test_setup_with_environment_keys_does_not_write_a_profile(self):
        with mock.patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "AKIAEXAMPLE"}):
            life = self.setup_done()
        self.assertFalse((self.tmp / "aws-config").exists())
        self.assertFalse(life.home.setup_outputs("aws")["operator_profile_written"])
        self.assertTrue(any("environment variables" in line for line in self.world.output))

    def test_up_needs_setup(self):
        with self.assertRaises(UserError):
            self.world.lifecycle().up(Options(), wait=False)

    # ---- up
    def test_up_creates_the_machine_as_the_operator_and_writes_the_ssh_host(self):
        life = self.setup_done()
        record = life.up(Options(), wait=False)
        plan = [c for c in self.world.calls if c[0] == "plan" and c[1] == "machine"][-1]
        values, env = plan[3], plan[4]
        self.assertEqual(values["allowed_cidrs"], ["198.51.100.7/32"])
        self.assertTrue(values["running"])
        self.assertEqual(values["instance_profile_name"], "caseforge-machine")
        self.assertTrue(values["ssh_public_key"].startswith("ssh-ed25519 "))
        self.assertEqual(env, {"AWS_PROFILE": "caseforge-operator"})
        backend = [c for c in self.world.calls if c[0] == "init" and c[1] == "machine"][-1][2]
        self.assertIn('key = "machine/caseforge.tfstate"', backend)
        self.assertIn("use_lockfile = true", backend)
        self.assertEqual(record["machine"]["public_ip"], "203.0.113.10")
        config = life.home.ssh_config.read_text()
        self.assertIn("Host caseforge\n  HostName 203.0.113.10\n  User caseforge", config)
        self.assertIn("HostKeyAlias caseforge-i-0abc", config)
        self.assertIn("LocalForward 8000 localhost:8000", config)
        self.assertIn("LocalForward 8080 localhost:8080", config)
        self.assertTrue(any("ssh caseforge" in line for line in self.world.output))

    def test_up_as_caller_uses_the_callers_credentials(self):
        life = self.setup_done(as_caller=True)
        life.up(Options(), wait=False)
        plan = [c for c in self.world.calls if c[0] == "plan" and c[1] == "machine"][-1]
        self.assertEqual(plan[4], {})

    def test_up_asks_first_and_stops_on_no(self):
        self.setup_done()
        life = self.world.lifecycle(assume_yes=False)
        self.world.answers = ["no"]
        with self.assertRaises(UserError):
            life.up(Options(), wait=False)
        self.assertIsNone(self.world.machine)

    def test_up_refuses_the_whole_internet(self):
        life = self.setup_done()
        with self.assertRaises(UserError):
            life.up(Options(allowed_cidrs=["0.0.0.0/0"]), wait=False)

    def test_up_without_a_detectable_address_asks_for_one(self):
        life = self.setup_done()
        self.world.my_ip = None
        with self.assertRaises(UserError):
            life.up(Options(), wait=False)

    def test_up_rejects_bad_names_and_sizes(self):
        life = self.setup_done()
        for bad in (Options(name="prod"), Options(size="huge"), Options(stack="nope"), Options(disk_gb=10)):
            with self.assertRaises(UserError):
                life.up(bad, wait=False)

    def test_up_waits_for_the_stack(self):
        life = self.setup_done()
        life.up(Options(), wait=True)
        commands = [c[1] for c in self.world.remote]
        self.assertIn(["cloud-init", "status", "--wait"], commands)
        self.assertIn(["caseforge", "wait", "45"], commands)

    def test_up_again_with_a_new_ref_updates_the_machine_over_ssh(self):
        life = self.setup_done()
        life.up(Options(), wait=False)
        self.world.remote.clear()
        life.up(Options(git_ref="feature/x", stack="sso"), wait=False)
        commands = [c[1] for c in self.world.remote]
        self.assertIn(["caseforge", "update", "feature/x"], commands)
        self.assertIn(["caseforge", "stack", "sso"], commands)

    # ---- stop, start
    def test_stop_keeps_the_machine_and_start_readmits_the_current_address(self):
        life = self.setup_done()
        life.up(Options(), wait=False)
        life.set_running("caseforge", False)
        self.assertFalse(self.world.machine["running"])
        self.assertIn("# caseforge: stopped", life.home.ssh_config.read_text())
        self.assertNotIn("Host caseforge", life.home.ssh_config.read_text())
        self.world.my_ip, self.world.next_ip = "192.0.2.44", "203.0.113.99"
        life.set_running("caseforge", True, wait=False)
        self.assertEqual(self.world.cidrs, ["192.0.2.44/32"])
        self.assertIn("HostName 203.0.113.99", life.home.ssh_config.read_text())

    def test_start_unknown_machine_explains(self):
        life = self.setup_done()
        with self.assertRaises(UserError):
            life.set_running("caseforge", True)

    # ---- down
    def test_down_needs_the_name_typed_and_deletes_the_key(self):
        self.setup_done()
        life = self.world.lifecycle(assume_yes=True)
        life.up(Options(), wait=False)
        life = self.world.lifecycle(assume_yes=False)
        self.world.answers = ["yes"]
        with self.assertRaises(UserError):
            life.down("caseforge")
        self.assertIsNotNone(self.world.machine)
        self.world.answers = ["caseforge"]
        life.down("caseforge")
        self.assertIsNone(self.world.machine)
        mdir = life.home.machine_dir("aws", "caseforge")
        self.assertFalse((mdir / "id_ed25519").exists())
        self.assertFalse((mdir / "machine.json").exists())
        self.assertNotIn("Host caseforge", life.home.ssh_config.read_text())

    def test_down_from_another_computer_uses_placeholders(self):
        life = self.setup_done()
        self.world.machine = {"running": True}
        life.down("caseforge")
        plan = [c for c in self.world.calls if c[0] == "plan" and c[1] == "machine"][-1]
        self.assertTrue(plan[2])
        self.assertEqual(plan[3]["allowed_cidrs"], ["192.0.2.1/32"])
        self.assertIsNone(self.world.machine)

    # ---- status
    def test_status_runs_the_machines_status_or_explains_why_not(self):
        life = self.setup_done()
        life.up(Options(), wait=False)
        self.assertEqual(life.status("caseforge"), 0)
        self.assertEqual(self.world.remote[-1][1], ["caseforge", "status"])
        self.world.reachable = False
        self.assertEqual(life.status("caseforge"), 1)
        self.assertTrue(any("stopped itself" in line for line in self.world.output))


if __name__ == "__main__":
    unittest.main()
