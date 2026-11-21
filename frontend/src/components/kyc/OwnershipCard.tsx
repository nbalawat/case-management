import { useMemo, useState } from 'react'
import { api, fmtDate, words } from '../../api'
import { useNameOf } from '../../shell/people'
import { Card, Pill } from '../ui'
import type { Analysis, OwnLink, OwnNode, Ownership } from './types'

const CUSTOMER = 'customer'
const W = 150, H = 46, GAP_X = 26, GAP_Y = 64
const pct = (n: number) => `${Number(n.toFixed(2))}%`

/** Rows of the chart: the customer at the bottom, each owner one row above the nearest thing it owns. */
function layout(links: OwnLink[]) {
  const depth = new Map<string, number>([[CUSTOMER, 0]])
  let frontier = [CUSTOMER]
  while (frontier.length) {
    const next: string[] = []
    for (const owned of frontier) for (const l of links) if (l.owned === owned && !depth.has(l.owner)) { depth.set(l.owner, depth.get(owned)! + 1); next.push(l.owner) }
    frontier = next
  }
  const rows: string[][] = []
  for (const [key, d] of depth) (rows[d] ??= []).push(key)
  const widest = Math.max(...rows.map(r => r.length))
  const width = widest * W + (widest - 1) * GAP_X + 24
  const height = rows.length * H + (rows.length - 1) * GAP_Y + 24
  const at = new Map<string, { x: number; y: number }>()
  rows.forEach((row, d) => {
    const span = row.length * W + (row.length - 1) * GAP_X
    row.forEach((key, i) => at.set(key, { x: (width - span) / 2 + i * (W + GAP_X), y: height - 12 - H - d * (H + GAP_Y) }))
  })
  return { at, width, height }
}

/** The structure as a chart: who holds what, with the people who count as beneficial owners marked. */
function Chart({ name, nodes, links, analysis }: { name: string; nodes: OwnNode[]; links: OwnLink[]; analysis: Analysis }) {
  const { at, width, height } = useMemo(() => layout(links), [links])
  const owners = new Set(analysis.beneficial_owners.map(o => o.key))
  const all = [{ key: CUSTOMER, name, kind: 'customer', country: '', pep: false, verified: true }, ...nodes]
  return (
    <div className="scroll"><svg className="own" viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img"
      aria-label={`Ownership of ${name}: ${analysis.beneficial_owners.map(o => `${o.name} ${pct(o.effective)}`).join(', ') || 'no beneficial owner identified'}`}>
      {links.map(l => {
        const a = at.get(l.owner), b = at.get(l.owned)
        if (!a || !b) return null
        const x1 = a.x + W / 2, y1 = a.y + H, x2 = b.x + W / 2, y2 = b.y, up = y2 < y1 // an upward line is a loop back
        const mid = (y1 + y2) / 2
        return <g key={`${l.owner}>${l.owned}`} className={`own-link${l.control ? ' control' : ''}${up ? ' loop' : ''}`}>
          <path d={`M${x1},${y1} C${x1},${mid} ${x2},${mid} ${x2},${y2}`} />
          <text x={(x1 + x2) / 2} y={mid} textAnchor="middle" dy="4">{l.percent ? pct(l.percent) : ''}{l.control ? `${l.percent ? ' + ' : ''}control` : ''}</text>
        </g>
      })}
      {all.map(n => {
        const p = at.get(n.key)
        if (!p) return null
        return <g key={n.key} className={`own-node ${n.kind}${owners.has(n.key) ? ' bo' : ''}${n.pep ? ' pep' : ''}`} transform={`translate(${p.x},${p.y})`}>
          <title>{`${n.name} · ${words(n.kind)}${n.country ? ` · ${n.country}` : ''}${n.pep ? ' · politically exposed' : ''}${n.kind !== 'customer' && !n.verified ? ' · identity not verified' : ''}`}</title>
          <rect width={W} height={H} rx={n.kind === 'person' ? 23 : 6} />
          <text x={W / 2} y={19} textAnchor="middle" className="nm">{n.name.length > 22 ? `${n.name.slice(0, 21)}…` : n.name}</text>
          <text x={W / 2} y={35} textAnchor="middle" className="sub">{n.kind === 'customer' ? 'The customer' : `${words(n.kind)}${n.country ? ` · ${n.country}` : ''}${n.pep ? ' · PEP' : ''}`}</text>
        </g>
      })}
    </svg></div>
  )
}

type Draft = { nodes: OwnNode[]; links: OwnLink[]; source: string; note: string }
const blankNode = (i: number): OwnNode => ({ key: `party_${i}`, name: '', kind: 'person', country: 'US', pep: false, verified: false })

/** Beneficial ownership: the recorded structure, what it works out to, and a way to record a new version. */
export default function OwnershipCard({ partyId, name, ownership, onChange }: { partyId: string; name: string; ownership: Ownership; onChange: () => void }) {
  const nameOf = useNameOf()
  const [draft, setDraft] = useState<Draft | null>(null)
  const [preview, setPreview] = useState<Analysis | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const s = ownership.structure, a = ownership.analysis
  const edit = (d: Draft) => { setDraft(d); setPreview(null) }
  const start = () => edit(s ? { nodes: s.nodes.map(n => ({ ...n })), links: s.links.map(l => ({ ...l })), source: '', note: '' }
    : { nodes: [blankNode(1)], links: [{ owner: 'party_1', owned: CUSTOMER, percent: 100, control: false }], source: '', note: '' })
  const node = (i: number, patch: Partial<OwnNode>) => draft && edit({ ...draft, nodes: draft.nodes.map((n, j) => (j === i ? { ...n, ...patch } : n)) })
  const link = (i: number, patch: Partial<OwnLink>) => draft && edit({ ...draft, links: draft.links.map((l, j) => (j === i ? { ...l, ...patch } : l)) })
  const check = () => draft && api.post<Analysis>(`/customers/${partyId}/ownership/check`, draft).then(r => { setPreview(r); setMsg(null) }).catch(e => { setPreview(null); setMsg(e.message) })
  const save = () => draft && api.put(`/customers/${partyId}/ownership`, draft)
    .then(() => { setDraft(null); setPreview(null); setMsg('Recorded as a new version. The earlier one is kept.'); onChange() }).catch(e => setMsg(e.message))
  const shown = preview ?? a
  return (
    <Card title="Ownership and control" action={<span className="inline tight">
      {s && <span className="muted small">Version {s.version} · {fmtDate(s.recorded_at)} · {nameOf(s.recorded_by)}{s.source ? ` · ${s.source}` : ''}</span>}
      {ownership.can_record && !draft && <button onClick={start}>{s ? 'Record a change…' : 'Record the structure…'}</button>}</span>}>
      {!s && !draft && <p className="empty">No ownership structure is on record for this customer.</p>}
      {s && a && !draft && <Chart name={name} nodes={s.nodes} links={s.links} analysis={a} />}
      {draft && <div className="own-edit">
        <h3>Parties</h3>
        <table><thead><tr><th>Key</th><th>Name</th><th>Kind</th><th>Country</th><th>PEP</th><th>Verified</th><th /></tr></thead>
          <tbody>{draft.nodes.map((n, i) => <tr key={i}>
            <td><input aria-label="Key" value={n.key} size={10} onChange={e => node(i, { key: e.target.value })} /></td>
            <td><input aria-label="Name" value={n.name} size={24} onChange={e => node(i, { name: e.target.value })} /></td>
            <td><select aria-label="Kind" value={n.kind} onChange={e => node(i, { kind: e.target.value })}>{ownership.kinds.map(k => <option key={k} value={k}>{words(k)}</option>)}</select></td>
            <td><input aria-label="Country" value={n.country} size={3} maxLength={2} onChange={e => node(i, { country: e.target.value.toUpperCase() })} /></td>
            <td><input type="checkbox" aria-label="Politically exposed" checked={n.pep} onChange={e => node(i, { pep: e.target.checked })} /></td>
            <td><input type="checkbox" aria-label="Identity verified" checked={n.verified} onChange={e => node(i, { verified: e.target.checked })} /></td>
            <td><button className="ghost" aria-label={`Remove ${n.name || n.key}`} onClick={() => edit({ ...draft, nodes: draft.nodes.filter((_, j) => j !== i), links: draft.links.filter(l => l.owner !== n.key && l.owned !== n.key) })}>Remove</button></td>
          </tr>)}</tbody></table>
        <button onClick={() => edit({ ...draft, nodes: [...draft.nodes, blankNode(draft.nodes.length + 1)] })}>Add a party</button>
        <h3>Holdings</h3>
        <table><thead><tr><th>Owner</th><th>Holds</th><th>Share %</th><th>Control</th><th /></tr></thead>
          <tbody>{draft.links.map((l, i) => <tr key={i}>
            <td><select aria-label="Owner" value={l.owner} onChange={e => link(i, { owner: e.target.value })}>{draft.nodes.map(n => <option key={n.key} value={n.key}>{n.name || n.key}</option>)}</select></td>
            <td><select aria-label="Holds" value={l.owned} onChange={e => link(i, { owned: e.target.value })}><option value={CUSTOMER}>{name} (the customer)</option>
              {draft.nodes.filter(n => n.key !== l.owner).map(n => <option key={n.key} value={n.key}>{n.name || n.key}</option>)}</select></td>
            <td><input type="number" aria-label="Share" min={0} max={100} step="any" value={l.percent} onChange={e => link(i, { percent: Number(e.target.value) })} /></td>
            <td><input type="checkbox" aria-label="Control without shares, or with them" checked={l.control} onChange={e => link(i, { control: e.target.checked })} /></td>
            <td><button className="ghost" onClick={() => edit({ ...draft, links: draft.links.filter((_, j) => j !== i) })}>Remove</button></td>
          </tr>)}</tbody></table>
        <button disabled={!draft.nodes.length} onClick={() => edit({ ...draft, links: [...draft.links, { owner: draft.nodes[0].key, owned: CUSTOMER, percent: 0, control: false }] })}>Add a holding</button>
        <div className="inline">
          <input type="search" aria-label="Source" placeholder="Source, such as the company registry" value={draft.source} onChange={e => edit({ ...draft, source: e.target.value })} />
          <input type="search" aria-label="Note" placeholder="What changed and how you know" value={draft.note} onChange={e => edit({ ...draft, note: e.target.value })} />
        </div>
        <div className="inline"><button onClick={check}>Work it out</button>
          <button className="primary" disabled={!preview} onClick={save}>Record as version {(s?.version ?? 0) + 1}</button>
          <button onClick={() => { setDraft(null); setPreview(null); setMsg(null) }}>Cancel</button>
          <span className="muted small">{preview ? 'This is what the draft works out to. Nothing is saved yet.' : 'Work it out first to see the beneficial owners before recording.'}</span></div>
        {preview && <Chart name={name} nodes={draft.nodes} links={draft.links} analysis={preview} />}
      </div>}
      {shown && (!draft || preview) && <>
        {shown.flags.length > 0 && <ul className="own-flags">{shown.flags.map(f => <li key={f.code}><Pill tone="serious">{words(f.code)}</Pill> {f.text}</li>)}</ul>}
        <table className="factors">
          <thead><tr><th>Owner</th><th>Kind</th><th className="num">Effective share</th><th className="num">Layers</th><th>Standing</th></tr></thead>
          <tbody>{shown.owners.map(o => <tr key={o.key}>
            <td><b>{o.name}</b>{o.pep && <Pill tone="serious">PEP</Pill>}</td><td>{words(o.kind)}{o.country ? ` · ${o.country}` : ''}</td>
            <td className="num">{pct(o.effective)}</td><td className="num">{o.layers}</td>
            <td>{o.beneficial_owner ? <Pill tone="human">Beneficial owner{o.control && o.effective < shown.threshold ? ' by control' : ''}</Pill> : <span className="muted">{o.kind === 'person' ? `Below ${pct(shown.threshold)}` : 'Not a person, so not an owner in itself'}</span>}
              {!o.verified && <Pill tone="warning">Not verified</Pill>}</td>
          </tr>)}
            {shown.unidentified > 0.005 && <tr><td colSpan={2}><b>Not traced to anyone</b></td><td className="num">{pct(shown.unidentified)}</td><td /><td><Pill tone="serious">Unidentified</Pill></td></tr>}</tbody>
        </table>
        <p className="small muted">A person holding {pct(shown.threshold)} or more through any combination of chains, or holding control, is a beneficial owner under this client's rules. {shown.layers} layer{shown.layers === 1 ? '' : 's'} deep.</p>
      </>}
      {!draft && ownership.history.length > 1 && <details><summary className="small">{ownership.history.length} versions on record</summary>
        <ul className="small">{ownership.history.map(h => <li key={h.version}>Version {h.version}, {fmtDate(h.recorded_at)}, {nameOf(h.recorded_by)}: {h.beneficial_owners.join(', ') || 'no beneficial owner identified'}{h.note ? `. “${h.note}”` : ''}</li>)}</ul></details>}
      {msg && <p className="msg" role="status">{msg}</p>}
    </Card>
  )
}
