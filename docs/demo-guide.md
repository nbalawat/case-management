# CaseForge demo guide

A guided tour of the platform with the seeded demo data, one persona at a time. Start the app first (see
[Quick start](../README.md#quick-start)) and run `make seed` (or `make reset` under Docker) so every step below
starts from the same data.

## Getting around

The account menu at the top right is the whole login: "View as" picks the person, "Client" the client (or type
a name into the command palette). Steps 1 to 9 are at Meridian Retail Bank; each says who to be. Steps 10 to 12
show the platform running several clients.

**Finding your way.** The menu on the left is built for the person signed in: their work, the desks they work
(KYC, Sanctions, Fraud), investigation tools, oversight pages if they manage, and an Administration area only if
they configure something. An analyst sees six items; the MLRO about twenty; the auditor lands on the dashboard.
Press `/` or `Ctrl K` (`⌘K` on a Mac) anywhere for the command palette: find any case, customer, counterparty or
transaction, jump to any page you have, or type a question and ask it. Light, dark or system theme is in the
account menu. Every page still opens by its address for anyone, and the server still decides what each person
may see and do; the menu only decides what is in front of them.

**Lists.** Cases, Alert intake and Regulatory reports share one grid. Cases are paged, sorted and filtered in the
database (`GET /api/case-list`), so the list stays quick however many cases there are; each filter shows how many
cases every choice would leave. Filters, sort and page live in the address, so a view can be bookmarked or sent.
Pick or save named views, choose columns and row density, export everything that matches to CSV, and move with the
keyboard: `J`/`K` or the arrows, `Enter` to open, `Space` for a quick view beside the list, `X` to select for bulk
steps. Saved views and column choices are kept in the browser, per client and person; storing them on the server
so they follow a person between devices and can be shared with a team is still to do.

**A case.** The strip under the name shows priority, the regulatory deadline, the stage clock, the agent's view and
who owns the case, with claim, release, reassign and restrict beside them. The evidence sits in tabs (Overview,
Transactions, Customer, Narrative, Requests, Timeline), and the tab is in the address, so `?tab=transactions` opens
straight on the money. The decision stays on the right whichever tab is open: the stage's tasks with progress and
the next one marked, the note, the closing reason, and each step with a short reason when it is not available yet.
Every source chip in the agent briefing, the copilot or an alert opens Transactions with just those records. There,
a daily chart of money in and out marks days with alerted activity; click a day (shift-click for a range) to
filter, or group by counterparty, channel or country to see where the value went.

**Dashboards.** Every figure opens the cases behind it. On the overview, a headline tile, a day's bar of cases
opened or closed, a stage, or an alert scenario opens the case list filtered to exactly those cases (the counts
match: `due=week`, `opened=2026-11-21`, `closed=2026-11`, `scenario=structuring` and the usual filters are list
filters the API understands). The filters a dashboard set show as chips on the list, each removable. The
management report does the same for months. Charts are drawn with Apache ECharts, coloured from the page's own
tokens so they follow light and dark, and each has its figures as a table underneath.

**Desks.** KYC, Sanctions, Fraud, Entities and Detection tuning are split into tabs, each with a count and a red
mark when something there is overdue or waiting on a second person; the tab is in the address (`/kyc?tab=approvals`)
and the headline tiles open the matching tab. Loading sanctions list versions and editing the client's own list
moved from the Sanctions desk to Administration, under Screening lists; the desk keeps a read-only view of the
lists in force.

## The walkthrough

1. **My work, as Ivan Petrov (L2 investigator).** His own cases ordered by urgency, then unassigned work in
   his queue with a Claim button. Stage clocks and the regulatory deadline are separate columns.
2. **An investigation, still as Ivan.** Open a case in *Investigation*. The agent briefing is on the left with
   its recommendation and cited findings; click a "sources" chip and the transactions filter to the rows
   behind it. On the right, the steps are blocked until the stage tasks are ticked. Raise an information
   request to the customer and park the case: it moves to a waiting stage with its own clock.
3. **Approval, as Maya Chen (MLRO).** Open a case in *MLRO review*. An ordinary filing shows "Approve and file
   SAR". A case over $100,000 or on a politically exposed customer shows "Approve and send to Head of FIU"
   instead: the workflow itself routes it to a second approver (Hugo Laurent). Four-eyes rules stop anyone
   approving work they drafted or sent.
4. **Fraud, as Fran Dubois.** A different workflow on the same engine: a two-hour triage clock, customer
   contact as a waiting stage, and refunds above $5,000 routed to a lead (Tara Singh) for approval.
5. **Team, as Tara Singh (team lead).** Workload by person, queue backlogs, every case past its stage clock,
   and reassignment in one click. QA results sit alongside: one sampled closure failed QA and was reopened.
6. **Workflows, as Omar Haddad (admin).** Pick a case type and read it as a diagram with live case counts.
   Edit the definition (say, change the triage clock from 48 to 12 hours and raise the version), check the
   draft to see exactly what would change, and publish. New cases use the new version; open cases finish
   on the one they started with. A broken draft (unknown role, dead-end stage, bad condition) is refused
   with the reason.
7. **Access, as anyone.** The role and permission matrix, every workflow step against every role, people and
   queues. All of it is the client's own policy; nothing about roles is in code.
8. **Read-only, as Audra Klein (auditor).** Every case and audit trail opens; nothing can be changed.
9. **Data pipelines, as Omar.** Open Data. Three sources read Meridian's core-banking files from its
   landing zone: customers, accounts and daily transaction partitions in CSV, JSON lines and Parquet. Press
   "Run all sources". They run in dependency order; counts and amounts reconcile; one customer record is
   rejected and one transaction file is held whole because too many of its rows are unusable, with the
   rejected rows written to quarantine. Open the run's details to see each file. Run again: nothing loads
   twice. "Add a source" looks at a location, samples the newest file and proposes the mapping. The sample
   file loader and the throughput check are still on their own pages.

10. **A second client.** Switch the client to Kestrel Payments. Different people, different roles and queue
    names, three case types instead of six, no QA. Joy Tanaka holds two roles (MLRO and operations lead) and
    still cannot approve her own work. Customer `P10005` exists here too and is a different person.
11. **Changing access, as Pat Novak at Kestrel.** On Access, add a person or move a permission in the policy,
    raise the version and check the draft: the page lists exactly what would change. Try removing the only
    chief risk officer: refused, because a workflow Kestrel runs would have nobody for a step. Publish, and the
    change applies from the next click. Meridian's policy is untouched.
12. **Operator console.** Pick "All clients" in the client picker. Every client with its volumes, backlog and
    configuration, in counts only. Onboard a new client: choose a template, name it, and it is live with its
    own copy of the policy and a small set of sample data. Suspend it and its sign-ins stop at once.

13. **Ask anything, as Tara Singh at Meridian.** Open Ask and click "Which scenario produces the most SARs?".
    The answer leads with the figure, charts the breakdown, and links the cases behind it; "How this was
    answered" shows each lookup and its filters. Ask "Who has the most open work?", then switch to Ana Ruiz
    (an analyst) and ask it again: her role does not hold the team view, so the answer says so, and "What
    should I work on first?" lists only her own cases. Switch client and the same question gets that
    client's answer. Every question lands in the question record.


14. **Agents, as Omar Haddad at Meridian.** Open Agents. Every agent prepares and recommends; none acts alone. Set the
    sanctions adjudicator to L3 on screening hits and try to publish: refused until those settings are evaluated.
    Set the discount limit to 0.95 and evaluate the draft: it fails, because it would have discounted a true match.
    At 0.9 it passes; publish. Run the lookback and the tuning review. Switch an agent off with a reason and re-run
    a briefing: the step is gone and says why. Download the documentation pack. Then open a case as Ivan and ask
    the copilot why the recommendation is what it is, and to draft a request to the customer.


15. **Who sees what, at Meridian.** As Ana Ruiz, open Cases: only her two queues. As Ivan Petrov: every monitoring
    case and nothing else. Ask "how many cases are there in total" as each and the answers differ, because the
    filter sits under everything. As Maya Chen, open one of Ivan's investigations and press Restrict with a
    reason. Switch to Ivan: the case is gone from his lists, his numbers and his answers, and its address says
    "not found". Lena Fischer, who is cleared for restricted matters, can claim it and carry on. Open Access as
    Omar to see what each role sees, then change a role's `sees` rule and check the draft.

16. **Who is who, as Ivan at Meridian.** Press `/` and type "volkof": the misspelling still finds
    Volkov Maritime Trading, which payments spell two ways. Open Entities: spellings merged without a person,
    look-alikes waiting for one, and "is this payee one of our own customers?" questions. Press Same on one and it
    moves to the record. Open a customer from a case ("Customer profile and network"): money in and out, every
    counterparty under one name, the customers connected through the uncommon ones, and the network drawn two
    hops out. Click a flagged firm to see every customer who deals with it and why its spellings were matched.

17. **Reporting to the regulator, as Maya Chen at Meridian.** Open Reports. One cash day over $10,000 has no CTR:
    press Prepare CTR, read the document, send it. Open an accepted SAR: the FinCEN XML exactly as sent, its
    fingerprint, the regulator's reference and the date to look again. Open the report still with the regulator and
    record a rejection, then amend it with a corrected narrative; the original stays as it was. Try to edit the
    narrative on a case whose SAR was sent: it is locked. Switch to Alder Private Bank: the same work produces goAML
    STRs, because that client reports to a different regulator.

18. **Casework at volume, at Meridian.** As Ana Ruiz, open Cases, tick several and press Claim: each case answers for
    itself, and the ones outside her queues say why. Pick cases at the same stage and close them together with one
    reason. Open the unassigned case for a customer who has two: Related cases offers the merge, and the alerts
    move across. Follow "weak alerts are waiting" to Alert intake to see what a hibernation rule is holding, and
    open a case for one. As Quinn Adeyemi, open the QA review, score it, and try to pass it below 80%. As Tara
    Singh, read Quality assurance findings on Team. As Omar Haddad, open Lists and retire a closing reason.
19. **Knowing the customer, at Meridian.** As Maya Chen, open KYC: a lower rating for Halcyon Imports waits for
    her, because Kai Nakamura proposed it and cannot approve it himself. Open Northwind Holdings: the second
    version of its ownership put a Jersey trust on top, so no beneficial owner is identified and a review opened.
    Press "Record a change", add a person behind the trust and press "Work it out" to see the answer before
    saving. On Sable and Finch, the passport has expired: open the document to see its fingerprint and everyone
    who touched it. On any monitoring case, Flow of funds shows only the payments behind its alerts.
20. **Sanctions and fraud, at Meridian.** As Maya Chen, open Sanctions: a $48,200 payment is held on a 90% name
    match, and Sofia Marin's proposal to release it waits for a second person. Approve it, or open the case to
    see the two names side by side. Prepare the report owed for the rejected payment and follow it to Reports.
    Under Lists screened against, add a name to the internal list and watch everyone get screened again. Open
    the law enforcement request and decide a possible match; switch to Ivan Petrov and the section is gone. As
    Fran Dubois, open Fraud: a wire dispute is in recovery, and the closed ones show what came back and what was
    lost. Open its case to record money coming back and resolve it on the figures.
21. **Tuning detection, at Meridian.** Open Detection tuning (under Administration). As Maya Chen, a change to cash structuring
    waits for her: Omar Haddad proposed four deposits instead of three, and the backtest shows which alerts
    would go and that none of them mattered. As Omar, press "Try different settings" on Rapid movement of
    funds, set the hours to 24 and run it: three alerts that led to filings would be lost, so approving it
    would take a written reason. As Hugo Laurent, run the lookback "Cash deposits from $5,000": its alerts land
    in cases for people, and it can be signed off by someone else once those cases are decided.
22. **Reporting and governance, at Meridian.** As Tara Singh, open Management reports (or Dashboard, then "Month by month"): the
    month as a committee reads it, with last month one click away and two downloads. As Maya Chen, open
    Governance. The change register lists every change to the setup with its second person. Start an
    access review: Maya and Hugo are flagged for holding both sides of the detection control, nobody can
    certify their own access, and last month's review still shows one change that was asked for and not made.
    In Audit search, look for the word "jurisdiction", then switch to Tara and see that events on the
    restricted case are not there.


## Appendix: models, overlay mode and the API

### Connecting a model to Ask

With no key the Ask page uses an offline planner: a phrase matcher that understands common questions (the
suggested ones and their variations), answers one question at a time, and says so when it cannot place a
question. To answer free-form and follow-up questions with a model:

```bash
export ANTHROPIC_API_KEY=...        # then start the app as usual
export ASK_MODEL=claude-opus-5-5    # optional; this is the default
```

The model never sees the database. It is handed five read-only lookups (breakdown, case list, case detail,
customer, team workload) that run as the person asking, inside their client, and it gets only the lookups that
person's role may use. Case ids in an answer are checked against what the lookups returned before they become
links. `ASK_PROVIDER=offline` forces the planner even when a key is set. Other providers (a model in the
client's own cloud, a local model) plug in behind the same interface in `backend/app/ask/gateway.py`.

To show overlay mode (sitting on top of an incumbent monitoring engine), push an alert from a terminal and
watch a case appear:

```bash
curl -X POST localhost:8000/api/alerts -H 'content-type: application/json' -d '{
  "source": "legacy-tm", "external_id": "LT-88213", "scenario": "rapid_movement",
  "party_id": "P10005", "score": 0.82, "explanation": "Vendor rule 12: funds in and out within 24h"}'
```

API calls are for one client. In demo mode it is read from an `X-Tenant` header (Meridian if absent) and the
person from `X-Persona`. With `AUTH_MODE=oidc` both come from the sign-in token and those headers are ignored.
`POST /api/alerts` is a system calling in, so it has no person's view: it sees every case of the client.

The full API reference is at <http://localhost:8000/docs>.
