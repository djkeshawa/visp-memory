const DASHBOARD_PATH = "/dashboard"
const LOGIN_PATH = `${DASHBOARD_PATH}/auth`

export function redirectToLogin(): void {
  const next = window.location.pathname + window.location.search + window.location.hash
  window.location.replace(`${LOGIN_PATH}?${new URLSearchParams({ next })}`)
}

export function dashboardReturnPath(): string {
  const next = new URLSearchParams(window.location.search).get("next")
  if (!next) return DASHBOARD_PATH
  try {
    const destination = new URL(next, window.location.origin)
    const path = decodeURIComponent(destination.pathname).replace(/\/+$/, "")
    if (
      destination.origin !== window.location.origin ||
      (path !== DASHBOARD_PATH && !path.startsWith(`${DASHBOARD_PATH}/`)) ||
      path === LOGIN_PATH || path.startsWith(`${LOGIN_PATH}/`) || path.includes("\\")
    ) return DASHBOARD_PATH
    return destination.pathname + destination.search + destination.hash
  } catch {
    return DASHBOARD_PATH
  }
}
