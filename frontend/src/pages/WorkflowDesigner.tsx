import { useLocation, useParams } from 'react-router-dom'
import Designer from '../features/composer/Designer'

export default function WorkflowDesigner() {
  const { id = '' } = useParams()
  const notes = (useLocation().state as { notes?: string[] } | null)?.notes ?? []
  return <Designer key={id} draftId={id} notes={notes} />
}
