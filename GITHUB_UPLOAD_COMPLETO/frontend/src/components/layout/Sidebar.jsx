import { motion } from 'framer-motion'
import {
  LayoutDashboard, FileText, Thermometer, Volume2, FlaskConical,
  ClipboardList, Building2, Users, Package, BarChart3, Activity,
  ShieldCheck
} from 'lucide-react'

const sections = [
  {
    label: 'DOCUMENTOS',
    items: [
      { id: 'pgr',     icon: FileText,      label: 'Gerador de PGR' },
      { id: 'calor',   icon: Thermometer,   label: 'Laudo de Calor' },
      { id: 'ruido',   icon: Volume2,       label: 'Laudo de Ruído' },
      { id: 'quimico', icon: FlaskConical,  label: 'Análise Química' },
    ],
  },
  {
    label: 'MEDIÇÕES',
    items: [
      { id: 'medicoes',  icon: ClipboardList, label: 'Medições e Amostradores' },
      { id: 'demandas',  icon: Activity,      label: 'Demandas / OS' },
    ],
  },
  {
    label: 'CADASTROS',
    items: [
      { id: 'empresas',    icon: Building2, label: 'Empresas' },
      { id: 'agentes',     icon: Users,     label: 'Agentes' },
      { id: 'amostradores',icon: Package,   label: 'Amostradores' },
    ],
  },
  {
    label: 'ANÁLISE',
    items: [
      { id: 'relatorios',  icon: BarChart3,     label: 'Relatórios' },
      { id: 'indicadores', icon: LayoutDashboard, label: 'Indicadores' },
    ],
  },
]

export default function Sidebar({ active, onNavigate }) {
  return (
    <aside className="w-56 bg-surface border-r border-border flex flex-col shrink-0">
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-4 py-4 border-b border-border">
        <div className="w-7 h-7 rounded-lg bg-green/20 border border-green/30 flex items-center justify-center">
          <ShieldCheck size={15} className="text-green" />
        </div>
        <div>
          <div className="text-text1 font-semibold text-sm leading-none">Ocupacional</div>
          <div className="text-text3 text-[10px] mt-0.5">Plataforma SST</div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto py-3 px-2 space-y-4">
        {/* Dashboard link */}
        <NavItem
          id="dashboard"
          icon={LayoutDashboard}
          label="Início"
          active={active === 'dashboard'}
          onClick={() => onNavigate('dashboard')}
        />

        {sections.map(section => (
          <div key={section.label}>
            <div className="px-3 mb-1 text-[10px] font-semibold text-text3 tracking-wider">
              {section.label}
            </div>
            {section.items.map(item => (
              <NavItem
                key={item.id}
                {...item}
                active={active === item.id}
                onClick={() => onNavigate(item.id)}
              />
            ))}
          </div>
        ))}
      </nav>

      {/* Footer */}
      <div className="px-4 py-3 border-t border-border">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-full bg-green/20 border border-green/30 flex items-center justify-center text-green text-xs font-bold">
            MC
          </div>
          <div>
            <div className="text-text1 text-xs font-medium">Matheus Costa</div>
            <div className="text-text3 text-[10px]">Técnico SST</div>
          </div>
          <div className="ml-auto w-1.5 h-1.5 rounded-full bg-green" />
        </div>
      </div>
    </aside>
  )
}

function NavItem({ id, icon: Icon, label, active, onClick }) {
  return (
    <motion.button
      onClick={onClick}
      whileHover={{ x: 2 }}
      whileTap={{ scale: 0.97 }}
      className={`nav-item w-full ${active ? 'active' : ''}`}
    >
      <Icon size={15} className={active ? 'text-green' : ''} />
      <span>{label}</span>
    </motion.button>
  )
}
