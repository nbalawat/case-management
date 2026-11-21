import { Card } from '../ui'
import type { Overview } from './types'

const origin = () => window.location.origin

/** The first five minutes of an integrator: a token, a list of cases, an alert, the event feed and a webhook check. */
export default function QuickStart({ data }: { data: Overview }) {
  const base = `${origin()}${data.api.base_url}`
  const steps: [string, string][] = [
    ['1. Get a token', `curl -s -u "$CLIENT_ID:$CLIENT_SECRET" \\\n  -d grant_type=client_credentials -d scope="cases:read alerts:write" \\\n  ${base}/oauth/token`],
    ['2. Read cases', `curl -s -H "Authorization: Bearer $TOKEN" "${base}/cases?status=open&priority=high&limit=50"\n# next page: add &cursor=<next_cursor>`],
    ['3. Send an alert, safely retried', `curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: $(uuidgen)" \\\n  -H "Content-Type: application/json" ${base}/alerts \\\n  -d '{"source":"my-tm","external_id":"A-1001","scenario":"rapid_movement","party_id":"P10005","score":0.82,"explanation":"Rule 12"}'`],
    ['4. Follow changes', `curl -s -H "Authorization: Bearer $TOKEN" "${base}/events?after=0&types=case.*,alert.created"`],
    ['5. Check a webhook signature (Python)', `from app.public.webhooks.signing import verify   # or any Standard Webhooks library\nok = verify(secret, headers["webhook-id"], headers["webhook-timestamp"],\n            headers["webhook-signature"], raw_body)`],
  ]
  return (
    <div className="dev-grid">
      <Card title="Quick start">
        {steps.map(([t, code]) => <div key={t} style={{ marginBottom: 14 }}><b className="small">{t}</b><div className="dev-code">{code}</div></div>)}
      </Card>
      <div className="ws-stack">
        <Card title="Reference">
          <p>Version {data.api.version}. Every operation, its scope, its schemas and its errors.</p>
          <div className="inline"><a className="btn primary" href={data.api.docs} target="_blank" rel="noreferrer">Open the interactive reference</a>
            <a href={data.api.openapi} target="_blank" rel="noreferrer">OpenAPI document</a></div>
          <p className="muted small">Generate a client in any language from the OpenAPI document (openapi-generator, openapi-typescript, Kiota).</p>
        </Card>
        <Card title="How it keeps clients safe">
          <ul className="dev-rules">{data.rules.map(r => <li key={r}>{r}</li>)}
            <li>Every call is authenticated, rate limited, and recorded with its request id.</li>
            <li>Webhooks go only to public https addresses, are signed, and see only what their application may see.</li></ul>
        </Card>
        <Card title="Scopes">
          <table><tbody>{data.scopes.map(s => <tr key={s.name}><td><code>{s.name}</code></td><td className="small">{s.description}
            {s.people_only && <> · <b>people only</b></>}{s.needs_permission && <> · needs <i>{s.needs_permission.replaceAll('_', ' ')}</i></>}</td></tr>)}</tbody></table>
        </Card>
      </div>
    </div>
  )
}
