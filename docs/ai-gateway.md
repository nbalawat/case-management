# The model gateway

Every model call in CaseForge (Ask anything, the specialist agents) goes through one gateway in `backend/app/ai/`.
Callers ask for a model by purpose (`ask` or `agents`); the gateway reads the client's model policy, picks the
endpoint, checks residency and spend limits, meters the call, and falls back if the endpoint cannot be reached.

## Endpoints

`backend/ai/endpoints.yaml` lists the models this deployment can reach. Secrets never live in it: each endpoint names
the environment variables it needs, and is offered only once they are set.

| Endpoint | Kind | Needs | Runs in |
| --- | --- | --- | --- |
| `claude` | Claude Opus 5.5 through the Anthropic API | `ANTHROPIC_API_KEY` | `us` (inference pinned with `inference_geo`) |
| `claude-sonnet` | Claude Sonnet 5.5 through the Anthropic API | `ANTHROPIC_API_KEY` | `us` |
| `open-weight` | Any OpenAI-compatible server: vLLM, Ollama, LiteLLM, llama.cpp, TGI | `OPEN_MODEL_URL` (and `OPEN_MODEL`) | `local` |
| `offline` | Built in: the deterministic planner, no model | nothing | `local` |

Put keys in the repository's `.env` file (git-ignored; Docker Compose and the app both read it):

```
ANTHROPIC_API_KEY=...
# or an open-weight model, for example Ollama on the host:
OPEN_MODEL_URL=http://host.docker.internal:11434/v1
OPEN_MODEL=qwen3:14b
AGENT_MODE=model   # let the specialist agents use the model too
```

## A client's model policy

Platform staff set it with `PUT /api/platform/tenants/{client}/model`. It is stored in the client's settings:

| Setting | Meaning |
| --- | --- |
| `ask`, `agents` | Endpoint id for each purpose, or `auto` (the deployment default); `agents` may also be `rules` |
| `fallback` | Endpoint ids tried in order when the chosen one is not configured, or fails on the first call |
| `regions` | Where inference may run. An endpoint outside the list is never used, not even as a fallback |
| `monthly_tokens` | Input plus output tokens per calendar month (UTC). Over it, calls are refused before they are made |
| `question_tokens` | Tokens one question or one agent run may use |

When nothing allowed is configured, questions go to the offline planner and agents to the rules, and the answer says why.
A fallback never happens silently in the middle of an answer: once tools have run, an outage ends the answer honestly.

## Usage

Every model reply writes a `model_usage` row (client, purpose, endpoint, model that served it, tokens, cost at the
endpoint's configured price). `GET /api/ai` shows a client's set-up and this month's usage to people with the team
view or the agent manager permission; the platform route shows the same per client to platform staff.
