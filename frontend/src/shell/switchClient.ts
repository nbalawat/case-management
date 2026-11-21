import { setTenantId } from '../api'

/** Switching client is a clean break: nothing from one client may linger on screen for the next. */
export function switchClient(id: string) {
  setTenantId(id)
  window.location.assign('/')
}
