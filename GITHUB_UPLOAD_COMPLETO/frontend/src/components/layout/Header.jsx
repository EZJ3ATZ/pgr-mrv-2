import { Search, Bell } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'

export default function Header() {
  return (
    <header className="h-12 border-b border-border bg-card flex items-center px-5 gap-4 shrink-0">
      <div className="relative flex-1 max-w-xs">
        <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
        <Input
          className="pl-8 h-7 text-xs bg-secondary border-0 focus-visible:ring-1 placeholder:text-muted-foreground/60"
          placeholder="Buscar empresa, OS, agente..."
        />
        <kbd className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-muted-foreground bg-background border border-border rounded px-1">
          ⌘K
        </kbd>
      </div>
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
