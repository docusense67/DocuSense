import { useQuery } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { api, ApiError } from './api'
import { Layout } from './components/Layout'
import { AdminHome } from './pages/admin/AdminHome'
import { IntakeSettingsPage } from './pages/admin/IntakeSettings'
import { AuditTrail } from './pages/AuditTrail'
import { ChangePassword, Login } from './pages/Auth'
import { IntakeStationPage } from './pages/operator/IntakeStation'
import { OperatorHome } from './pages/operator/OperatorHome'
import { ScanDetailPage } from './pages/operator/ScanDetail'
import { IntakeQueue } from './pages/registrar/IntakeQueue'
import { IntakeReview } from './pages/registrar/IntakeReview'
import { Records } from './pages/registrar/Records'
import { Setup } from './pages/registrar/Setup'
import { StudentRecord } from './pages/registrar/StudentRecord'
import { RegistrarHome } from './pages/registrar/RegistrarHome'
import { RequestList } from './pages/registrar/RequestList'
import { RequestReview } from './pages/registrar/RequestReview'
import { Templates } from './pages/registrar/Templates'
import { StudentHome } from './pages/student/StudentHome'
import { TeacherHome } from './pages/teacher/TeacherHome'

export default function App() {
  const me = useQuery({
    queryKey: ['me'],
    queryFn: async () => {
      try {
        return await api.me()
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null
        throw e
      }
    },
    retry: false,
  })

  if (me.isPending) return null
  if (!me.data) return <Login />
  if (me.data.must_change_password) return <ChangePassword />
  const user = me.data

  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout user={user} />}>
          {user.role === 'student' && <Route index element={<StudentHome />} />}
          {user.role === 'teacher' && <Route index element={<TeacherHome />} />}
          {user.role === 'admin' && (
            <>
              <Route index element={<AdminHome />} />
              <Route path="overview" element={<RegistrarHome readOnly />} />
              <Route path="audit" element={<AuditTrail />} />
              <Route path="intake-settings" element={<IntakeSettingsPage />} />
            </>
          )}
          {user.role === 'registrar' && (
            <>
              <Route index element={user.can_validate ? <Navigate to="/scan" replace /> : <RegistrarHome />} />
              <Route path="requests" element={<RequestList />} />
              <Route path="requests/:id" element={<RequestReview />} />
              <Route path="intake" element={<IntakeQueue />} />
              <Route path="intake/:id" element={<IntakeReview />} />
              <Route path="records" element={<Records />} />
              <Route path="records/students/:id" element={<StudentRecord />} />
              <Route path="setup" element={<Setup />} />
              <Route path="audit" element={<AuditTrail />} />
              <Route path="templates" element={<Templates />} />
              {user.can_validate && (
                <>
                  <Route path="scan" element={<OperatorHome />} />
                  <Route path="scan/:id" element={<ScanDetailPage />} />
                  <Route path="intake-station" element={<IntakeStationPage />} />
                </>
              )}
            </>
          )}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
