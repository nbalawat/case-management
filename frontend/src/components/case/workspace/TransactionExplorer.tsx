import { useMemo, useState } from 'react'
import { fmtDateTime, fmtMoney } from '../../../api'
import { Card, Pill } from '../../ui'
import DataGrid from '../../grid/DataGrid'
import { Segmented } from '../../grid/controls'
import type { GridColumn, SortState } from '../../grid/types'
import DailyHistogram from './DailyHistogram'
import { daily, dayOf, grouped, pick, totals, visible, type GroupKey } from './txns'
import type { Txn } from './types'

const COLUMNS: GridColumn<Txn>[] = [
  { id: 'ts', accessorFn: t => t.ts, header: 'When', meta: { label: 'When', pinned: true, className: 'nowrap' }, cell: ({ row }) => fmtDateTime(row.original.ts) },
  { id: 'channel', accessorFn: t => t.channel, header: 'Channel', meta: { label: 'Channel' } },
  { id: 'counterparty', accessorFn: t => t.counterparty, header: 'Counterparty', meta: { label: 'Counterparty' },
    cell: ({ row: { original: t } }) => <>{t.counterparty || <span className="muted">Cash</span>}{t.country !== 'US' && <Pill>{t.country}</Pill>}</> },
  { id: 'in', accessorFn: t => (t.direction === 'in' ? t.amount : 0), header: 'In', meta: { label: 'In', align: 'right' },
    cell: ({ row: { original: t } }) => (t.direction === 'in' ? fmtMoney(t.amount) : '') },
  { id: 'out', accessorFn: t => (t.direction === 'out' ? t.amount : 0), header: 'Out', meta: { label: 'Out', align: 'right' },
    cell: ({ row: { original: t } }) => (t.direction === 'out' ? fmtMoney(t.amount) : '') },
  { id: 'id', accessorFn: t => t.id, header: 'Reference', meta: { label: 'Reference', hidden: true, className: 'small muted' } },
]

const GROUPS: [string, string][] = [['none', 'List'], ['counterparty', 'By counterparty'], ['channel', 'By channel'], ['country', 'By country']]

/**
 * The case's transactions as a picture first (daily in and out), then grouped totals or the list. Citations from
 * the agents or the copilot narrow it to exactly the cited records.
 */
export default function TransactionExplorer({ txns, focus, onClearFocus }: { txns: Txn[]; focus: string[]; onClearFocus: () => void }) {
  const [onlyAlerted, setOnlyAlerted] = useState(false)
  const [range, setRange] = useState<[string, string] | null>(null)
  const [by, setBy] = useState<'none' | GroupKey>('none')
  const [group, setGroup] = useState<{ by: GroupKey; key: string } | null>(null)
  const [sorting, setSorting] = useState<SortState>([{ id: 'ts', desc: true }])
  const days = useMemo(() => daily(txns), [txns])
  const shown = useMemo(() => visible(txns, { focus, onlyAlerted, range, group }), [txns, focus, onlyAlerted, range, group])
  const sum = totals(shown)
  const shownDays = useMemo(() => new Set(shown.map(t => dayOf(t.ts))), [shown])
  const groups = useMemo(() => (by === 'none' ? [] : grouped(visible(txns, { focus, onlyAlerted, range, group: null }), by)), [txns, focus, onlyAlerted, range, by])
  const filtered = focus.length > 0 || onlyAlerted || !!range || !!group
  const clear = () => { onClearFocus(); setOnlyAlerted(false); setRange(null); setGroup(null) }
  return (
    <Card title={`Transactions (${txns.length})`} className="txn-explorer"
      action={filtered ? <button className="ghost" onClick={clear}>Show all</button> : undefined}>
      <DailyHistogram days={days} active={d => !filtered || shownDays.has(d)} onPick={(d, ext) => setRange(r => pick(r, d, ext))} />
      <div className="txn-bar">
        <span className="txn-sum"><b>{sum.count}</b> shown · <span className="in">{fmtMoney(sum.in)} in</span> · <span className="out">{fmtMoney(sum.out)} out</span></span>
        {focus.length > 0 && <Pill tone="agent">{focus.length} cited record{focus.length > 1 ? 's' : ''}</Pill>}
        {range && <Pill tone="human">{range[0] === range[1] ? range[0] : `${range[0]} to ${range[1]}`}</Pill>}
        {group && <Pill tone="human">{group.key}</Pill>}
        <span className="grow" />
        <label className="check"><input type="checkbox" checked={onlyAlerted} onChange={e => setOnlyAlerted(e.target.checked)} /> Alerted only</label>
        <Segmented label="Group transactions" options={GROUPS} value={by} onChange={v => { setBy(v as typeof by); setGroup(null) }} />
      </div>
      {by !== 'none' && !group ? (
        <table className="txn-groups">
          <thead><tr><th>{GROUPS.find(g => g[0] === by)![1].replace('By ', '')}</th><th className="num">Count</th><th className="num">In</th><th className="num">Out</th><th>Share of value</th></tr></thead>
          <tbody>{groups.map(g => {
            const all = groups.reduce((a, x) => a + x.in + x.out, 0) || 1
            return (
              <tr key={g.key} className={g.alerted ? 'hit' : ''} tabIndex={0} onClick={() => setGroup({ by, key: g.key })} onKeyDown={e => e.key === 'Enter' && setGroup({ by, key: g.key })}>
                <td><b>{g.key}</b>{g.alerted > 0 && <span className="muted small"> · {g.alerted} alerted</span>}</td>
                <td className="num">{g.count}</td><td className="num">{g.in ? fmtMoney(g.in) : ''}</td><td className="num">{g.out ? fmtMoney(g.out) : ''}</td>
                <td><div className="share"><div style={{ width: `${((g.in + g.out) / all) * 100}%` }} /></div></td>
              </tr>)
          })}</tbody>
        </table>
      ) : (
        <DataGrid id="case-transactions" label="Transactions" columns={COLUMNS} rows={shown} getRowId={t => t.id}
          sorting={sorting} onSortingChange={setSorting} rowClassName={t => (t.alerted ? 'hit' : '')}
          empty={<p className="empty">No transactions match.</p>} />
      )}
    </Card>
  )
}
