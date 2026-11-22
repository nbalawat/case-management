"""The OpenTofu wrapper against the real `tofu` binary, on a local root with enforced state and plan encryption like
the machine stack's: init, plan to a file, apply that file, read outputs, plan a destroy. Skipped without tofu."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import fakes  # noqa: F401  (puts the package on the path)

from caseforge_cloud.tofu import Tofu

ROOT = """
variable "passphrase" { type = string }
variable "value" { type = string }
terraform {
  encryption {
    key_provider "pbkdf2" "k" { passphrase = var.passphrase }
    method "aes_gcm" "m" { keys = key_provider.pbkdf2.k }
    state {
      method   = method.aes_gcm.m
      enforced = true
    }
    plan {
      method   = method.aes_gcm.m
      enforced = true
    }
  }
}
resource "terraform_data" "this" { input = var.value }
output "value" { value = terraform_data.this.output }
"""


@unittest.skipIf(shutil.which("tofu") is None, "OpenTofu is not installed")
class RealTofuTest(unittest.TestCase):
    def test_encrypted_lifecycle(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "root"
            root.mkdir()
            (root / "main.tf").write_text(ROOT)
            var_file = Path(d) / "vars.json"
            var_file.write_text(json.dumps({"passphrase": "a-test-passphrase-of-length", "value": "one"}))
            tofu = Tofu(root=root, data_dir=Path(d) / "data")
            self.assertGreaterEqual(tuple(int(x) for x in tofu.check_version().split(".")), (1, 11, 0))
            tofu.init(var_file=var_file)
            plan = Path(d) / "p.tfplan"
            self.assertTrue(tofu.plan(out=plan, var_file=var_file))
            tofu.apply(plan, var_file=var_file)
            self.assertEqual(tofu.outputs(var_file=var_file), {"value": "one"})
            self.assertFalse(tofu.plan(out=plan, var_file=var_file))
            self.assertTrue(tofu.plan(out=plan, var_file=var_file, destroy=True))
            tofu.apply(plan, var_file=var_file)
            self.assertEqual(tofu.outputs(var_file=var_file), {})


if __name__ == "__main__":
    unittest.main()
