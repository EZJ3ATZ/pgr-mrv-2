import { ShieldCheck, LayoutDashboard, FileText, Thermometer, Volume2,
         FlaskConical, ClipboardList, Activity, Building2, Users,
         Package, BarChart3, Settings, ChevronRight } from 'lucide-react'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'

const NAV = [
  {
    section: 'Documentos',
    items: [
      { id: 'pgr',      label: 'Gerador de PGR',  icon: FileText },
      { id: 'calor',    label: 'Laudo de Calor',   icon: Thermometer },
      { id: 'ruido',    label: 'Laudo de Ruído',   icon: Volume2 },
      { id: 'quimico',  label: 'Análise Química',  icon: FlaskConical },
    ],
  },
  {
    section: 'Operacional',
    items: [
      { id: 'coleta',       label: 'Coleta de Campo',  icon: ClipboardList },
      { id: 'demandas',     label: 'Demandas / OS',    icon: Activity },
      { id: 'amostradores', label: 'Amostradores',     icon: Package },
    ],
  },
  {
    section: 'Cadastros',
    items: [
      { id: 'empresas', label: 'Empresas', icon: Building2 },
      { id: 'agentes',  label: 'Agentes',  icon: Users },
    ],
  },
  {
    section: 'Análise',
    items: [
      { id: 'bi', label: 'BI / Indicadores', icon: BarChart3 },
    ],
  },
]

export default function Sidebar({ active, onNavigate }) {
  return (
    <aside className="w-56 flex flex-col h-screen bg-card border-r border-border shrink-0">
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-4 h-12 border-b border-border">
        <div className="w-6 h-6 rounded bg-blue-600 flex items-center justify-center shrink-0">
          <ShieldCheck size={13} className="text-white" />
        </div>
        <div className="leading-tight">
          <div className="text-foreground text-sm font-semibold tracking-tight">Ocupacional</div>
          <div className="text-muted-foreground text-[10px]">Plataforma SST</div>
        </div>
      </div>

      {/* Dashboard */}
      <div className="px-3 pt-3 pb-1">
        <button
          onClick={() => onNavigate('dashboard')}
          className={cn('nav-item w-full', active === 'dashboard' && 'active')}
        >
          <LayoutDashboard size={14} />
          <span>Início</span>
          {active === 'dashboard' && <ChevronRight size={12} className="ml-auto opacity-40" />}
        </button>
      </div>

      <Separator className="mx-3 w-auto" />

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-3 pb-3 space-y-3 pt-2">
        {NAV.map(({ section, items }) => (
          <div key={section}>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/50 px-2 mb-1">
              {section}
            </p>
            <div className="space-y-0.5">
              {items.map(({ id, label, icon: Icon }) => (
                <button
                  key={id}
                  onClick={() => onNavigate(id)}
                  className={cn('nav-item w-full', active === id && 'active')}
                >
                  <Icon size={14} />
                  <span>{label}</span>
                  {active === id && <ChevronRight size={12} className="ml-auto opacity-40" />}
                </button>
              ))}
            </div>
          </div>
        ))}
      </nav>

      {/* Footer */}
      <div className="border-t border-border px-3 py-2">
        <button onClick={() => onNavigate('configuracoes')} className="nav-item w-full mb-1">
          <Settings size={14} />
          <span>Configurações</span>
        </button>
        <div className="flex items-center gap-2 px-2 py-1">
          <div className="w-6 h-6 rounded-full bg-blue-700 flex items-center justify-center text-white text-[10px] font-bold shrink-0">
            MC
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-foreground text-xs font-medium truncate">Matheus Costa</p>
            <p className="text-muted-foreground text-[10px]">Técnico SST</p>
          </div>
          <div className="w-1.5 h-1.5 rounded-full bg-green-500 shrink-0" />
        </div>
      </div>
    </aside>
  )
}
