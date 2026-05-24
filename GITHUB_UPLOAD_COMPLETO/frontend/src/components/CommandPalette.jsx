import { useEffect, useState, useCallback } from 'react'
import { Command } from 'cmdk'
import { motion, AnimatePresence } from 'framer-motion'
import {
  LayoutDashboard, FileText, Thermometer, Volume2, FlaskConical,
  ClipboardList, Activity, Package, Building2, Users, BarChart3,
  Settings, Search, ArrowRight,
} from 'lucide-react'

const ITEMS = [
  { group: 'Navegação', id: 'dashboard',    label: 'Início — Painel Operacional', icon: LayoutDashboard },
  { group: 'Navegação', id: 'demandas',     label: 'Demandas / OS',               icon: Activity },
  { group: 'Navegação', id: 'amostradores', label: 'Amostradores',                icon: Package },
  { group: 'Navegação', id: 'empresas',     label: 'Empresas',                    icon: Building2 },
  { group: 'Navegação', id: 'bi',           label: 'BI / Indicadores',            icon: BarChart3 },
  { group: 'Documentos', id: 'pgr',         label: 'Gerador de PGR',              icon: FileText },
  { group: 'Documentos', id: 'calor',       label: 'Laudo de Calor',              icon: Thermometer },
  { group: 'Documentos', id: 'ruido',       label: 'Laudo de Ruído',              icon: Volume2 },
  { group: 'Documentos', id: 'quimico',     label: 'Análise Química',             icon: FlaskConical },
  { group: 'Operacional', id: 'coleta',     label: 'Coleta de Campo',             icon: ClipboardList },
  { group: 'Cadastros',  id: 'agentes',     label: 'Agentes',                     icon: Users },
  { group: 'Sistema',    id: 'configuracoes', label: 'Configurações',             icon: Settings },
]

const groups = [...new Set(ITEMS.map(i => i.group))]

export default function CommandPalette({ open, onOpenChange, onNavigate }) {
  const [value, setValue] = useState('')

  // Reset search on open
  useEffect(() => { if (open) setValue('') }, [open])

  // ⌘K / Ctrl+K global shortcut
  useEffect(() => {
    const handler = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        onOpenChange(o => !o)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onOpenChange])

  const run = useCallback((id) => {
    onNavigate(id)
    onOpenChange(false)
  }, [onNavigate, onOpenChange])

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            onClick={() => onOpenChange(false)}
          />

          {/* Dialog */}
          <motion.div
            className="fixed left-1/2 top-[20%] z-50 w-full max-w-lg -translate-x-1/2"
            initial={{ opacity: 0, scale: 0.96, y: -8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: -8 }}
            transition={{ duration: 0.15, ease: 'easeOut' }}
          >
            <Command
              className="rounded-xl border border-border bg-card shadow-2xl overflow-hidden"
              value={value}
              onValueChange={setValue}
              shouldFilter={true}
            >
              {/* Input */}
              <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
                <Search size={14} className="text-muted-foreground shrink-0" />
                <Command.Input
                  placeholder="Buscar páginas, ações..."
                  className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground outline-none"
                  autoFocus
                />
                <kbd className="text-[10px] text-muted-foreground border border-border rounded px-1.5 py-0.5 shrink-0">
                  ESC
                </kbd>
              </div>

              {/* Results */}
              <Command.List className="max-h-72 overflow-y-auto p-2">
                <Command.Empty className="py-8 text-center text-sm text-muted-foreground">
                  Nenhum resultado encontrado
                </Command.Empty>

                {groups.map(group => {
                  const items = ITEMS.filter(i => i.group === group)
                  return (
                    <Command.Group key={group} heading={group}
                      className="[&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-muted-foreground/50 [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:font-semibold">
                      {items.map(({ id, label, icon: Icon }) => (
                        <Command.Item
                          key={id}
                          value={label}
                          onSelect={() => run(id)}
                          className="flex items-center gap-3 px-2 py-2 rounded-md text-sm text-muted-foreground cursor-pointer
                            data-[selected=true]:bg-secondary data-[selected=true]:text-foreground
                            hover:bg-secondary hover:text-foreground transition-colors"
                        >
                          <Icon size={14} className="shrink-0" />
                          <span className="flex-1">{label}</span>
                          <ArrowRight size={12} className="opacity-0 group-data-[selected=true]:opacity-100 transition-opacity" />
                        </Command.Item>
                      ))}
                    </Command.Group>
                  )
                })}
              </Command.List>

              {/* Footer */}
              <div className="border-t border-border px-4 py-2 flex gap-4 text-[10px] text-muted-foreground/50">
                <span><kbd className="border border-border rounded px-1">↑↓</kbd> navegar</span>
                <span><kbd className="border border-border rounded px-1">↵</kbd> abrir</span>
                <span><kbd className="border border-border rounded px-1">ESC</kbd> fechar</span>
              </div>
            </Command>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
