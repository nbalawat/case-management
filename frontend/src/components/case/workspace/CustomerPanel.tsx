import { Link } from 'react-router-dom'
import { Card, Pill } from '../../ui'
import type { CaseDetail } from './types'

/** Who the case is about: the facts an investigator checks first, and the customer's other cases. */
export default function CustomerPanel({ party, others }: { party: CaseDetail['party']; others: CaseDetail['other_cases'] }) {
  return (
    <Card title="Customer" action={<Link to={`/customers/${party.id}`}>Profile and network</Link>}>
      <dl className="facts">
        <dt>Customer ID</dt><dd>{party.id}</dd><dt>Occupation</dt><dd>{party.occupation || '–'}</dd>
        <dt>Risk rating</dt><dd><Pill tone={party.risk_rating === 'high' ? 'serious' : party.risk_rating === 'medium' ? 'warning' : 'neutral'}>{party.risk_rating}</Pill>{party.pep && <Pill tone="serious">PEP</Pill>}</dd>
        <dt>Country</dt><dd>{party.country || '–'}</dd>
        <dt>Customer since</dt><dd>{party.onboarded}</dd><dt>Accounts</dt><dd>{party.accounts.join(', ')}</dd>
        <dt>Other cases</dt><dd>{others.length === 0 ? 'None' : others.map(o => <span key={o.id}><Link to={`/cases/${o.id}`}>{o.id}</Link> <span className="muted small">{o.type_title}, {o.state_label}</span><br /></span>)}</dd>
      </dl>
    </Card>
  )
}
