# Models that rank alerts

A model reads each new alert's customer behaviour and history and estimates how likely the alert is to matter
(to end in a filing or a confirmed concern). The estimate ranks the queue; it never closes or routes an alert on its
own. The triage agent and the client's agent policy still decide that.

Everything here is open source: [scikit-learn](https://scikit-learn.org) for the models, the platform's own database
for the feature store, and any object store (local folder, S3, ADLS, GCS through fsspec) for the model files.

## The parts

| Part | Where | What it does |
|---|---|---|
| Feature store | `backend/app/features/` | Feature sets defined in YAML (`features/library/*.yaml`). `compute` works out values **as of a moment**: every window ends just before it, so training never sees what happened after an alert. `online` keeps the latest values per customer for reading on screen |
| Training data | `backend/app/ml/dataset.py` | Alerts whose case has closed, labelled by outcome, plus outcomes loaded as `TrainingLabel` rows (for example a client's history from its old system) |
| Training | `backend/app/ml/train.py` | Time-ordered split: the oldest 75% to learn from, the newest 25% to test on. A boosted-tree model and a logistic baseline are compared on a validation slice of the training part, and the better one is refitted. The test alerts play no part in the choice |
| Registry | `backend/app/ml/registry.py` | Versions move candidate → approved → champion or challenger → retired. Every step is kept on the model and shows in the governance change register |
| Scoring | `backend/app/ml/scoring.py` | After the triage gate, the champion scores every incoming alert and the challenger (if any) scores alongside, for comparison only. Each reading keeps its three main reasons |
| Monitoring | `backend/app/ml/monitoring.py` | Population stability index for each feature, recent alerts against the training distribution |
| Model files | `backend/app/ml/artifacts.py` | Pickled to `MODEL_STORE_URL`. The SHA-256 recorded at training is checked before a file is loaded, so a swapped file is refused |

## Controls

- **Training** needs the `tune_detection` permission. **Approving, promoting and retiring** need `approve_tuning`.
- **Four eyes**: the person who trained a version cannot approve it. Approval asks for a note on what was checked.
- Only an approved version can be put to work. Making a new champion retires the old one, so exactly one version
  ranks alerts at any time.
- A model that cannot score (a missing file, a digest mismatch, scikit-learn not installed) leaves the alerts as they
  were and logs why. Alert handling never stops because of a model.
- Training refuses with too little to learn from: at least 100 decided alerts, with 15 of each outcome, and at least
  5 that mattered on each side of the split.

## What a model card shows

- **AUC on unseen alerts**, the other algorithm's AUC on the same alerts, precision-recall AUC against the base
  rate, and the Brier score for calibration.
- **Work it saves**: alerts reviewed highest score first against the share of the ones that mattered reached. "Could
  wait, reaching 95%" is the share of alerts that could go to a lighter review while still reaching 95% of the ones
  that mattered.
- **Drift** for each feature over the last 30 days: under 0.1 stable, 0.1 to 0.25 worth a look, over 0.25 moved.
  With fewer than 200 alerts in the period the figures are shown but not judged, because with ten bins chance alone
  gives an index of about 9 divided by the number of alerts.
- The features it reads, the history of who did what, and the model file's digest.

## Reasons on an alert

For each feature, the reason is how much the probability falls when that feature is set to its typical (training
median) value. The three that matter most are kept with the score, so the case shows "cash deposited in 30 days:
46,200 (typical 0)" rather than a bare number.

## In the demo

The seed gives each client a synthetic history of decided alerts at real transaction times (marked
`synthetic history (demo)`), trains version 1, has a second person approve it, makes it champion and scores the open
alerts. At Meridian, Omar (administrator) trained it and Maya, who holds the approval permission, approved it.

Open **Models** under Oversight to see the registry, a model card and the feature store. A case's **Alerts** card
shows the champion's reading on each alert.

## Adding a feature

Add a line to a feature set's YAML and bump its `version`. Sources are `transactions`, `alerts`, `cases` and
`party`; aggregations are `count`, `sum`, `max` and `count_distinct`, over `window_days`, filtered with `where`.
Online values computed under an older version are worked out again on the next read. A model keeps reading the features it was trained
on, so remove a feature only once no approved version uses it.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MODEL_STORE_URL` | `data/models` | Where model files are kept. Helm: `storage.modelStoreUrl` |

scikit-learn is the optional `ml` dependency group (`uv sync --group ml`); the container image includes it. Without
it the registry and feature store still work, and training says what is missing.
