import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from './Sidebar'
import Topbar from './Topbar'
import AlertPanel from '../alerts/AlertPanel'
import ErrorBoundary from '@/components/ui/ErrorBoundary'
import { useUIStore } from '@/store/ui'

interface AppLayoutProps {
  title?: string
  subtitle?: string
  actions?: React.ReactNode
}

export default function AppLayout({ title = 'CrimeAI', subtitle, actions }: AppLayoutProps) {
  const location = useLocation()
  const alertPanelOpen = useUIStore((s) => s.alertPanelOpen)

  return (
    <div className="flex h-screen overflow-hidden bg-bg-primary">
      <Sidebar />

      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        <Topbar title={title} subtitle={subtitle} actions={actions} />

        <main className="flex-1 overflow-auto">
          <ErrorBoundary resetKey={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>

      {/* Slide-in alert panel */}
      <AlertPanel open={alertPanelOpen} />
    </div>
  )
}
