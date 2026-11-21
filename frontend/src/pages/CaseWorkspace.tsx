import { useState } from 'react'
import { BreakGlassPanel } from '../components/access/BreakGlass'
import { useParams } from 'react-router-dom'
import { usePersona } from '../shell/people'
import { useApi } from '../useApi'
import { Loading } from '../components/ui'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/kit/tabs'
import { TabCount } from '../components/layout/PageTabs'
import { useTabParam } from '../components/layout/useTabParam'
import FlowOfFunds from '../components/FlowOfFunds'
import EvidenceCard from '../components/kyc/EvidenceCard'
import type { Kyc } from '../components/kyc/types'
import CaseExtras from '../components/CaseExtras'
import HoldsCard from '../components/HoldsCard'
import DisputesCard from '../components/DisputesCard'
import ScreeningCard from '../components/ScreeningCard'
import CaseHeader from '../components/case/workspace/CaseHeader'
import DecisionRail from '../components/case/workspace/DecisionRail'
import AgentBriefing from '../components/case/workspace/AgentBriefing'
import Copilot from '../components/case/workspace/Copilot'
import AlertsCard from '../components/case/workspace/AlertsCard'
import TransactionExplorer from '../components/case/workspace/TransactionExplorer'
import CustomerPanel from '../components/case/workspace/CustomerPanel'
import NarrativeCard from '../components/case/workspace/NarrativeCard'
import RequestsCard from '../components/case/workspace/RequestsCard'
import FormsCard from '../components/case/workspace/FormsCard'
import Timeline from '../components/case/workspace/Timeline'
import { NEW_REQUEST, type Act, type CaseDetail, type RequestDraft } from '../components/case/workspace/types'

const TABS = ['overview', 'transactions', 'customer', 'narrative', 'forms', 'requests', 'timeline'] as const
type Tab = (typeof TABS)[number]

/**
 * One case: a header strip with who, how urgent and who owns it; the evidence in tabs (kept in the URL so a link
 * opens the same view); and the decision rail, which stays in view whichever tab is open.
 */
export default function CaseWorkspace() {
  const { id } = useParams()
  const persona = usePersona()
  const { data, error, reload } = useApi<CaseDetail>(`/cases/${id}`, [persona?.id])
  const kyc = useApi<Kyc>(data ? `/customers/${data.party.id}/kyc` : null, [persona?.id])
  const [note, setNote] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const [focus, setFocus] = useState<string[]>([])
  const [request, setRequest] = useState<RequestDraft>(NEW_REQUEST)
  const [tick, setTick] = useState(0) // bumps after every action so the side panels reload too
  const [tab, goTo] = useTabParam(TABS) as [Tab, (t: Tab) => void]
  if (!data) return error ? <BreakGlassPanel kind="case" target={id!} error={error} onOpened={reload} /> : <Loading />
  const { case: c, party } = data

  const act: Act = async (fn, ok) => {
    try { await fn(); setMsg(ok); setNote(''); setTick(n => n + 1); reload() } catch (e) { setMsg((e as Error).message) }
  }
  const changed = (m: string) => { setMsg(m); setTick(n => n + 1); reload() }
  const cite = (ids: string[]) => { setFocus(ids); goTo('transactions') }
  const openRequests = data.requests.filter(r => r.status !== 'answered').length

  return (
    <>
      <CaseHeader d={data} act={act} />
      <div className="case-ws">
        <Tabs value={tab} onValueChange={v => goTo(v as Tab)} className="case-main">
          <TabsList aria-label="Case sections">
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="transactions">Transactions<TabCount n={data.transactions.length} /></TabsTrigger>
            <TabsTrigger value="customer">Customer{party.pep && <span className="tab-count warn">PEP</span>}</TabsTrigger>
            <TabsTrigger value="narrative">Narrative{!c.drafted_by && c.narrative && <span className="tab-count">draft</span>}</TabsTrigger>
            <TabsTrigger value="forms">Forms</TabsTrigger>
            <TabsTrigger value="requests">Requests<TabCount n={openRequests} /></TabsTrigger>
            <TabsTrigger value="timeline">Timeline</TabsTrigger>
          </TabsList>

          <TabsContent value="overview" className="ws-overview">
            <div className="col">
              <AgentBriefing d={data} act={act} onCite={cite} />
              <Copilot caseId={c.id} canWork={data.access.can_work} onCite={cite} onNote={setNote} onError={setMsg}
                onRequest={q => { setRequest(r => ({ ...r, question: q })); goTo('requests') }} />
            </div>
            <div className="col">
              <AlertsCard alerts={data.alerts} onCite={cite} />
              {c.case_type === 'sanctions_hit' && <ScreeningCard caseId={c.id} tick={tick} onChange={changed} />}
              {c.case_type !== 'qa_review' && <HoldsCard caseId={c.id} tick={tick} onChange={changed} />}
              {c.case_type === 'fraud_claim' && <DisputesCard caseId={c.id} tick={tick} onChange={changed} />}
              <CaseExtras caseId={c.id} tick={tick} onChange={changed} />
            </div>
          </TabsContent>

          <TabsContent value="transactions" className="ws-stack">
            <TransactionExplorer txns={data.transactions} focus={focus} onClearFocus={() => setFocus([])} />
            <FlowOfFunds partyId={party.id} caseId={c.id} />
          </TabsContent>

          <TabsContent value="customer" className="ws-stack">
            <CustomerPanel party={party} others={data.other_cases} />
            {kyc.data && <EvidenceCard partyId={party.id} caseId={c.id} view={kyc.data.evidence} onChange={kyc.reload} />}
          </TabsContent>

          <TabsContent value="narrative" className="ws-stack"><NarrativeCard key={`${c.id}:${c.narrative}`} d={data} act={act} /></TabsContent>
          <TabsContent value="forms" className="ws-stack"><FormsCard caseId={c.id} /></TabsContent>
          <TabsContent value="requests" className="ws-stack"><RequestsCard d={data} act={act} draft={request} setDraft={setRequest} /></TabsContent>
          <TabsContent value="timeline" className="ws-stack"><Timeline events={data.events} /></TabsContent>
        </Tabs>
        <aside className="case-rail" aria-label="Decision">
          <DecisionRail d={data} act={act} note={note} setNote={setNote} msg={msg} />
        </aside>
      </div>
    </>
  )
}
