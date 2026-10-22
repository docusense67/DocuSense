// Convenience setting for this station/browser; permission checks remain server-side.
function key(stationId: number) {
  return `adgvs.tray-setup.${stationId}.up180-down90.v1`
}

export function hasTraySetup(stationId: number | null): boolean {
  if (!stationId) return false
  try { return localStorage.getItem(key(stationId)) === 'confirmed' } catch { return false }
}

export function saveTraySetup(stationId: number): boolean {
  try { localStorage.setItem(key(stationId), 'confirmed'); return true } catch { return false }
}
