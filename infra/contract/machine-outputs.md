# The cloud machine's `machine` output

Every `modules/<cloud>/machine` returns one output, `machine`, an object with exactly these keys
(`contract/check.py` compares them across clouds):

| Key | Meaning |
|---|---|
| `cloud` | `aws`, `azure` or `gcp` |
| `region` | Region or location the machine runs in |
| `id` | The cloud's id for the machine |
| `machine_type` | The cloud's machine type that `size` mapped to |
| `public_ip` | Address to SSH to; empty while the machine is stopped, and it changes on every start |
| `running` | Whether the machine was asked to run |
| `console_url` | The machine's page in the cloud's console |
