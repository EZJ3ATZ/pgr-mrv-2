import { useState } from 'react'
import { Toaster } from 'sonner'
import Sidebar from './components/layout/Sidebar'
import Header from './components/layout/Header'
import Dashboard from './pages/Dashboard'
import Demandas from './pages/Demandas'
import Amostradores from './pages/Amostradores'
import './index.css'

const PAGES = {
  dashboard:    Dashboard,
  demandas:     Demandas,
  amostradores: Amostradores,
}

export default function App() {
  const [page, setPage] = useState('dashboard')
  const Page = PAGES[page] || Dashboard

  return (
    <div className="flex h-screen bg-bg overflow-hidden">
      <Sidebar active={page} onNavigate={setPage} />
      <div className="flex flex-col flex-1 overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <Page />
        </main>
      </div>
      <Toaster theme="dark" position="bottom-right" richColors />
    </div>
  )
}
