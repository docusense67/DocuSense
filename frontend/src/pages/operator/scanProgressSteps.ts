import type { MechanismStatus } from '../../api'

type Workflow = NonNullable<MechanismStatus['workflow']>
type ProgressTone = 'paused' | 'valid' | 'invalid' | 'review' | 'active'

/** Milestones reported by the station, rather than elapsed-time estimates. */
export function scanProgress(workflow?: Workflow | null, pending = false, unavailable = false) {
  const stage = pending ? 'preflight' : workflow?.stage === 'idle' && workflow.held ? 'held' : workflow?.stage ?? 'idle'
  const result = pending || stage === 'idle' ? null : workflow?.result?.result
  const phases: Record<string, { value: number; step: number; title: string }> = {
    idle: { value: 0, step: -1, title: 'Ready to feed paper' },
    preflight: { value: 5, step: 0, title: 'Preparing scan…' },
    feeding: { value: 20, step: 0, title: 'Feeding paper…' },
    capturing: { value: 40, step: 1, title: 'Scanning paper…' },
    ocr: { value: 60, step: 1, title: 'Reading document text…' },
    validating: { value: 75, step: 2, title: 'Checking document…' },
    sorting: { value: 90, step: 3, title: 'Sending paper to its tray…' },
    complete: { value: 100, step: 3, title: 'Scan complete' },
  }
  // Older station services reported every motor action as "moving".
  const phase = phases[stage] ?? (stage === 'moving' ? phases[result ? 'sorting' : 'feeding'] : phases.idle)
  const interrupted = ['held', 'stopping', 'error'].includes(stage)
  const reported = !pending && workflow?.progress !== undefined && Number.isFinite(workflow.progress)
    ? Math.max(0, Math.min(100, workflow.progress)) : undefined
  const value = reported ?? (interrupted ? (result === 'NEEDS_REVIEW' ? 100 : 0) : phase.value)
  const step = interrupted ? (value >= 85 ? 3 : value >= 75 ? 2 : value >= 40 ? 1 : value > 0 ? 0 : -1) : phase.step
  const resultLabel = result === 'VALID' ? 'VALID' : result === 'INVALID' ? 'NOT VALID' : result === 'NEEDS_REVIEW' ? 'NEEDS REVIEW' : null
  const title = unavailable ? 'Station unavailable — progress paused'
    : stage === 'stopping' ? 'Stopping scan…'
    : stage === 'error' ? 'Scan could not start'
    : interrupted && workflow?.error ? 'Scan stopped'
    : interrupted && !(stage === 'held' && result === 'NEEDS_REVIEW') ? 'Scan paused — check the paper'
    : resultLabel ? resultLabel : phase.title
  const detail = unavailable ? 'Reconnect the station to receive its current status.'
    : stage === 'stopping' ? 'Waiting for the camera/OCR operation to stop. Then clear the paper path.'
    : interrupted && workflow?.error ? workflow.error
    : stage === 'error' ? workflow?.message ?? 'The station could not start the scan. Check the camera and station connection.'
    : interrupted ? (stage === 'held' && result === 'NEEDS_REVIEW'
      ? 'Remove the held sheet and review the document before continuing.'
      : workflow?.message || 'The cycle did not finish. Clear the paper path before trying again.')
    : stage === 'sorting' ? 'Validation finished. The mechanism is sorting the sheet.'
    : stage === 'complete' ? 'The cycle finished. Check the output tray.'
    : stage === 'idle' ? 'Place one sheet in the feeder, then start validation.'
    : 'Progress follows the station’s reported steps.'
  const tone: ProgressTone = unavailable || (interrupted && result !== 'NEEDS_REVIEW') ? 'paused'
    : result === 'VALID' ? 'valid' : result === 'INVALID' ? 'invalid'
    : result === 'NEEDS_REVIEW' ? 'review' : 'active'
  return { value, step, title, detail, tone, resultLabel, paused: unavailable || interrupted }
}
