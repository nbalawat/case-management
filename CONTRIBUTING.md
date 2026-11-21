# Working in parallel

Several branches are built at once. These conventions keep them from editing the same lines, and CI fails when
one is broken, so a bad merge is caught on the pull request rather than on `main`.

## Backend endpoints: one file in `backend/app/api/`

Any module in `backend/app/api/` with a module-level `router = APIRouter(...)` is included automatically, behind
the signed-in check. Do not edit `backend/app/main.py` to add a router.

```python
# backend/app/api/model_gateway.py
router = APIRouter(prefix="/api/ai")
```

- Give each feature its own path prefix; modules load in name order, so paths must not depend on order.
- `PUBLIC = True` in the module includes it without the sign-in check. Only `auth` does this; a test pins it.
- Code that lives elsewhere (for example `app/public/`) exposes its router through a thin module here.
- Mounting a separate ASGI app or starting a background worker is still an explicit line in `main.py`.
- `tests/test_registration.py` fails if two features claim the same method and path.

## Frontend pages: one file in `frontend/src/modules/`

Each `*.tsx` file there default-exports a `Module` (or an array) and is picked up at build time. Do not add new
entries to `MODULES` in `frontend/src/shell/registry.tsx`.

```tsx
// frontend/src/modules/workflow-designer.tsx
import { PencilRuler } from 'lucide-react'
import { can } from '../shell/access'
import { page } from '../shell/page'
import type { Module } from '../shell/registry'

const workflowDesigner: Module = {
  id: 'workflow-designer', path: '/workflows/designer', label: 'Workflow designer', group: 'admin',
  description: 'Compose your own workflow from blocks', icon: PencilRuler, visible: can('publish_workflow'),
  page: page(() => import('../pages/WorkflowDesignerHome')), after: 'forms',
}
export default workflowDesigner
```

Name the object and export it (as above): `export default { ... }` fails `npm run lint`, which forbids anonymous
default exports.

`after` places it in its group after another module's id; without it, it goes at the end of its group. Import
`page` from `shell/page`, not from the registry (the registry imports these files). `npm test` fails on a
repeated id or path.

## Migrations: named by time, one head

- Write one with `make migration m="what changed"` (or `uv run python -m app.migrate revision "..."`). New
  revisions are named by the time they were written (`20261121174700_what_changed.py`), so two branches never
  claim the same id. Older migrations keep their numbers (`0016` to `0019`); nothing renumbers them.
- Two branches that each add a migration leave two heads once both land. `python -m app.migrate check` (a CI step)
  and `tests/test_registration.py` fail on that, and on a repeated revision id, and name the files.
- Revision ids only need to be unique; Alembic follows `down_revision`, not the number, so numbered ids (including
  ones reserved for open pull requests) and time ids live side by side and may land in any order. An id is
  four digits or a 14-digit time; `test_schema.py` checks the format and uniqueness, not that ids ascend.
- Immediately before merging, after merging `main`: your first new migration's `down_revision` must be what
  `python -m app.migrate heads` prints for `main`. `check` also fails when a `down_revision` names a revision
  that is not there.
- The fix after merging `main`: in the migration your branch added, set `down_revision` to the other head (the
  one `main` brought in). If your file used a number `main` already has, rename it and its `revision` to a time
  id first. Then run `python -m app.migrate check` again.

## Before you merge a pull request

1. Merge the latest `main` into your branch and push, so CI runs on what `main` will actually become.
2. Wait for every check to pass on that push, then merge.

When `main` moves and an open pull request now conflicts with it, the "Flag pull requests that conflict with
main" workflow comments on it and adds the `conflicts-with-main` label, because GitHub runs no checks on a
conflicted pull request. Merge `main` in and push; the label comes off on the next run.

## Still shared, so keep edits small

`backend/app/domain/models.py`, `backend/app/config.py` and `FACTORY` in `backend/tests/test_tenancy.py` are
still single files every feature appends to. Add your lines next to related ones rather than at the end, so two
branches rarely touch the same spot.
