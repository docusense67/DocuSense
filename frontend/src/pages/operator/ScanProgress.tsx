import type { MechanismStatus } from '../../api'
import { scanProgress } from './scanProgressSteps'

const steps = ['Paper feeding', 'Scanning', 'Checking', 'Result']
const colors = {
  active: 'bg-accent', valid: 'bg-good', invalid: 'bg-critical',
  review: 'bg-warning', paused: 'bg-muted',
}

export function ScanProgress({ workflow, pending, unavailable }: {
  workflow?: MechanismStatus['workflow']; pending: boolean; unavailable: boolean
}) {
  const progress = scanProgress(workflow, pending, unavailable)
  const complete = progress.value === 100 && !!progress.resultLabel && progress.tone !== 'paused'
  return (
    <section aria-label="Paper validation progress" className="mb-4 rounded-xl border border-line bg-page p-4">
      <div role="status" aria-live="polite" aria-atomic="true" className="mb-3">
        <p className={`text-2xl font-semibold ${progress.tone === 'valid' ? 'text-good-ink' : progress.tone === 'invalid' ? 'text-critical' : 'text-ink'}`}>
          {progress.tone === 'valid' ? '✓ ' : progress.tone === 'invalid' ? '✕ ' : progress.tone === 'review' ? '! ' : ''}{progress.title}
        </p>
        <p className="mt-1 text-sm text-ink-2">{progress.detail}</p>
      </div>
      <div role="progressbar" aria-label="Paper validation steps" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={progress.value} aria-valuetext={progress.title}
        className="h-4 overflow-hidden rounded-full bg-surface-2">
        <div className={`h-full rounded-full transition-[width] duration-300 motion-reduce:transition-none ${colors[progress.tone]}`}
          style={{ width: `${progress.value}%` }} />
      </div>
      <ol className="mt-3 grid grid-cols-4 gap-2 text-center text-xs sm:text-sm">
        {steps.map((label, index) => {
          const done = index < progress.step || (index === 3 && complete)
          const current = index === progress.step && !done
          return <li key={label} aria-current={current ? 'step' : undefined} className={done || current ? 'font-medium text-ink' : 'text-muted'}>
            <span aria-hidden="true" className={`mx-auto mb-1 flex h-7 w-7 items-center justify-center rounded-full ${done ? 'bg-accent text-accent-ink' : current ? 'border-2 border-accent' : 'border border-line'}`}>
              {done ? (index === 3 && progress.tone === 'invalid' ? '✕' : index === 3 && progress.tone === 'review' ? '!' : '✓') : index + 1}
            </span>{label}
          </li>
        })}
      </ol>
    </section>
  )
}
