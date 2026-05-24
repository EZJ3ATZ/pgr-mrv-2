import { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Toaster } from 'sonner'
import Sidebar from './components/layout/Sidebar'
import Header from './components/layout/Header'
import Dashboard from './pages/Dashboard'
import Demandas from './pages/Demandas'
import Amostradores from './pages/Amostradores'
import Empresas from './pages/Empresas'
import BI from './pages/BI'
import './index.css'

const PAGES = {
  dashboard:    Dashboard,
  demandas:     Demandas,
  amostradores: Amostradores,
  empresas:     Empresas,
  bi:           BI,
}

const pageVariants = {
  initial: { opacity: 0, y: 10 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.18, ease: 'easeOut' } },
  exit:    { opacity: 0, y: -6, transition: { duration: 0.12, ease: 'easeIn' } },
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
          <AnimatePresence mode="wait">
            <motion.div key={page} variants={pageVariants} initial="initial" animate="animate" exit="exit" className="h-full">
              <Page />
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
      <Toaster theme="dark" position="bottom-right" richColors />
    </div>
  )
}
