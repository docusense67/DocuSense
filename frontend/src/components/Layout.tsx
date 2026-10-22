import { useMutation, useQueryClient } from '@tanstack/react-query'
import { NavLink, Outlet } from 'react-router-dom'
import { api, type User } from '../api'
import { Notifications } from './Notifications'

const ROLE_LABEL: Record<User['role'], string> = {
  student: 'Student', teacher: 'Teacher', registrar: 'Registrar', admin: 'System Admin',
}

function navFor(user: User): { to: string; label: string }[] {
  switch (user.role) {
    case 'student':
      return [{ to: '/', label: 'My documents' }]
    case 'teacher':
      return [{ to: '/', label: 'My classes' }]
    case 'admin':
      return [{ to: '/', label: 'Accounts & stations' }, { to: '/overview', label: 'Overview' },
        { to: '/intake-settings', label: 'Screening thresholds' }, { to: '/audit', label: 'Audit trail' }]
    case 'registrar': {
      const items = [
        { to: '/', label: 'Dashboard' },
        { to: '/requests', label: 'Requests' },
        { to: '/intake', label: 'Incoming documents' },
        { to: '/records', label: 'Records' },
        { to: '/setup', label: 'Academic setup' },
        { to: '/templates', label: 'Templates' },
      ]
      if (user.can_validate) items.push({ to: '/scan', label: 'Scan station' })
      if (user.can_validate) items.push({ to: '/intake-station', label: 'Intake station' })
      items.push({ to: '/audit', label: 'Audit trail' })
      return items
    }
  }
}

export function Layout({ user }: { user: User }) {
  const qc = useQueryClient()
  const logout = useMutation({ mutationFn: api.logout, onSettled: () => { qc.clear(); qc.setQueryData(['me'], null) } })
  return (
    <div className="min-h-screen bg-page">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <span className="font-semibold text-ink">Academic Document System</span>
          <nav className="flex flex-wrap gap-1">
            {navFor(user).map((item) => (
              <NavLink key={item.to} to={item.to} end={item.to === '/'}
                className={({ isActive }) => `rounded-md px-2.5 py-1 text-sm ${isActive ? 'bg-surface-2 font-medium text-ink' : 'text-ink-2 hover:text-ink'}`}>
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm text-ink-2">
            <Notifications key={user.id} user={user} />
            <span className="hidden sm:inline">{user.email} · {ROLE_LABEL[user.role]}</span>
            <button onClick={() => logout.mutate()} className="text-accent hover:underline">Sign out</button>
          </div>
        </div>
      </header>
      <Outlet />
    </div>
  )
}
