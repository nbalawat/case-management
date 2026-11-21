import { useState } from 'react'
import { api } from '../../api'
import { useApi } from '../../useApi'
import { Card, Loading } from '../ui'
import { utc, type FeatureSet } from './types'

type Values = { set: string; version: string; computed: string; features: { name: string; label: string; value: number | null }[] }

const how = (f: FeatureSet['features'][number]) => {
  const what = f.source === 'party' ? `customer record: ${f.field}` : `${f.agg?.replace('_', ' ')}${f.field ? ` of ${f.field}` : ''} over ${f.source}`
  const where = f.where ? Object.entries(f.where).map(([k, v]) => `${k.replace(/_/g, ' ')} ${v}`).join(', ') : ''
  return [what, f.window_days ? `last ${f.window_days} days` : '', where].filter(Boolean).join(' · ')
}

/** The feature store: each set's definitions, and the online values for one customer, computed as of now. */
export default function FeaturesTab() {
  const { data, error } = useApi<{ sets: FeatureSet[] }>('/ml/features')
  const [party, setParty] = useState('')
  const [got, setGot] = useState<Values | null>(null)
  const [err, setErr] = useState<string | null>(null)
  if (!data) return <Loading error={error} />
  const look = (setId: string) => {
    setErr(null)
    api.get<Values>(`/ml/features/${setId}/${encodeURIComponent(party.trim())}`).then(setGot).catch(e => { setGot(null); setErr(e.message) })
  }
  return (<>
    {data.sets.map(fs => (
      <Card key={fs.id} title={`${fs.title} (version ${fs.version})`}>
        <p className="muted">{fs.description} Every window ends just before the moment asked about, so a model trained on these never sees what happened after an alert was raised.</p>
        <div className="inline">
          <input aria-label="Customer id" placeholder="Customer id, e.g. P10001" value={party} onChange={e => setParty(e.target.value)} onKeyDown={e => e.key === 'Enter' && party.trim() && look(fs.id)} />
          <button disabled={!party.trim()} onClick={() => look(fs.id)}>Show this customer's values</button>
          {err && <span className="msg" role="alert">{err}</span>}
        </div>
        <table>
          <thead><tr><th>Feature</th><th>How it is worked out</th>{got?.set === fs.id && <th className="num">Value now</th>}</tr></thead>
          <tbody>{fs.features.map(f => (
            <tr key={f.name}><td>{f.label}<div className="muted small lin-mono">{f.name}</div></td><td className="small">{how(f)}</td>
              {got?.set === fs.id && <td className="num">{got.features.find(x => x.name === f.name)?.value?.toLocaleString() ?? 'n/a'}</td>}</tr>))}
          </tbody>
        </table>
        {got?.set === fs.id && <p className="muted small">Computed {utc(got.computed).toLocaleString()} and kept in the online store.</p>}
      </Card>
    ))}
  </>)
}
