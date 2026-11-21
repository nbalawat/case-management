# The stamp contract: outputs

Every cloud's stamp module has one output, `contract`, of exactly this shape. `modules/kubernetes/stamp-release`
consumes it and never needs to know the cloud. `check.py` verifies each module's `outputs.tf` declares these keys.

| Key | Meaning |
|---|---|
| `name`, `environment`, `tenancy`, `namespace`, `service_account` | Echo of the inputs, with defaults applied |
| `cloud` | `aws`, `azure` or `gcp`, for display and labels only |
| `service_account_annotations` | Binds the service account to the stamp's cloud identity (IRSA, Azure workload identity, GKE workload identity) |
| `pod_labels` | Labels the pods need for that identity (Azure needs `azure.workload.identity/use`) |
| `storage` | `landing_url`, `evidence_url`, `quarantine_url` in the application's URL schemes (`s3://`, `abfss://`, `gs://`) |
| `database` | `host`, `port`, `name`, `sslmode`, and `credentials` (a secret reference) |
| `event_bus` | `mode`, `url` (null unless managed), `sasl_mechanism`, `credentials` (null unless managed) |
| `search` | `mode`, `url` (null unless managed), `credentials` (null unless managed) |
| `secret_store` | The External Secrets Operator provider block that reads this stamp's secrets as its own identity |
| `extra_env` | Cloud-specific environment the application needs (for example the Azure storage account name) |

A secret reference is `{ key, username_property, password_property }`: the name of the secret in the cloud's
secret manager and the JSON properties that hold the username and password. Every secret is JSON of that form,
so External Secrets builds `DATABASE_URL` and `KAFKA_CONFIG` the same way on every cloud.
