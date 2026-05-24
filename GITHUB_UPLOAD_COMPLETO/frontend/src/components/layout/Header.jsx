import { Search, Bell } from 'lucide-react'

export default function Header() {
  return (
    <header className="h-12 bg-surface border-b border-border flex items-center px-6 gap-4 shrink-0">
      {/* Search */}
      <div className="flex items-center gap-2 bg-surface2 border border-border rounded-btn px-3 py-1.5 flex-1 max-w-sm">
        <Search size={13} className="text-text3" />
        <input
          placeholder="Buscar empresa, OS, agente..."
          className="bg-transparent text-text1 text-sm outline-none placeholder:text-text3 w-full"
        />
        <kbd className="text-[10px] text-text3 bg-bg border border-border rounded px-1.5 py-0.5 font-mono">
          Ctrl+K
        </kbd>
      </div>

      <div className="flex-1" />

      {/* Status */}
      <div className="flex items-center gap-1.5 text-xs text-green">
        <span className="w-1.5 h-1.5 rounded-full bg-green animate-pulse" />
        Online
      </div>

      {/* Notificações */}
      <button className="w-8 h-8 rounded-btn hover:bg-surface2 border border-transparent hover:border-border transition-all flex items-center justify-center text-text2 hover:text-text1">
        <Bell size={15} />
      </button>
    </header>
  )
}
