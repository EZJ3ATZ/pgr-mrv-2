import { Search, Bell } from 'lucide-react'
import { Button } from '@/components/ui/button'

export default function Header({ onOpenCmd }) {
  return (
    <header className="h-12 border-b border-border bg-card flex items-center px-5 gap-4 shrink-0">
      <button
        onClick={onOpenCmd}
        className="relative flex items-center gap-2 flex-1 max-w-xs h-7 px-3 rounded-md bg-secondary border-0 text-xs text-muted-foreground/60 hover:text-muted-foreground transition-colors cursor-pointer"
      >
        <Search size={13} className="shrink-0" />
        <span className="flex-1 text-left">Buscar empresa, OS, agente...</span>
        <kbd className="text-[10px] text-muted-foreground bg-background border border-border rounded px-1 shrink-0">
          ⌘K
        </kbd>
      </button>
      <div className="flex items-center gap-3 ml-auto">
        <Button variant="ghost" size="icon" className="h-7 w-7">
          <Bell size={14} />
        </Button>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <div className="w-1.5 h-1.5 rounded-full bg-green-500" />
          Online
        </div>
      </div>
    </header>
  )
}
