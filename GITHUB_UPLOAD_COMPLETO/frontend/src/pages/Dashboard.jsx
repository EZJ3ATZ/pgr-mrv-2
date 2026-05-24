import { motion } from 'framer-motion'
import { useStats } from '../hooks/useStats'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Separator } from '@/components/ui/separator'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts'
import { Package, FlaskConical, ClipboardList, Building2, TrendingUp, AlertTriangle, CheckCircle2, Clock, ArrowRight, Activity } from 'lucide-react'
import { BadgeDelta } from '@tremor/react'

const stagger = {
  animate: { transition: { staggerChildren: 0.07 } }
}
const fadeUp = {
  initial: { opacity: 0, y: 16 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.22, ease: 'easeOut' } },
}

const prodData = [
  { mes: 'Jan', medicoes: 18, laudos: 5 },
  { mes: 'Fev', medicoes: 24, laudos: 8 },
  { mes: 'Mar', medicoes: 31, laudos: 11 },
  { mes: 'Abr', medicoes: 27, laudos: 9 },
  { mes: 'Mai', medicoes: 38, laudos: 14 },
]

function KpiRow({ label, value, sub, icon: Icon, loading }) {
  return (
    <div className="flex items-center justify-between py-2.5">
      <div className="flex items-center gap-2.5">
        <div className="w-6 h-6 rounded bg-secondary flex items-center justify-center shrink-0">
          <Icon size={12} className="text-muted-foreground" />
        </div>
        <div>
          <p className="text-sm text-foreground">{label}</p>
          <p className="text-[11px] text-muted-foreground">{sub}</p>
        </div>
      </div>
      {loading ? <Skeleton className="h-4 w-8" /> : (
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold tabular-nums">{value ?? '—'}</span>
          {value > 0 && <BadgeDelta deltaType="increase" size="xs" />}
        </div>
      )}
    </div>
  )
}

export default function Dashboard() {
  const { data: stats, loading } = useStats()

  const kpis = [
    { label: 'Medições realizadas',     value: stats?.medicoes_realizadas, sub: 'total histórico',      icon: CheckCircle2 },
    { label: 'Em estoque',              value: stats?.estoque,             sub: 'disponíveis',          icon: Package },
    { label: 'Demandas pendentes',      value: stats?.demandas_pendentes,  sub: 'aguardando coleta',    icon: ClipboardList },
    { label: 'No laboratório',          value: stats?.laboratorio,         sub: 'aguardando resultado', icon: FlaskConical },
    { label: 'Empresas ativas',         value: stats?.empresas_ativas,     sub: 'com OS registradas',   icon: Building2 },
    { label: 'Medições pendentes',      value: stats?.medicoes_pendentes,  sub: 'a executar',           icon: Clock },
  ]

  const pieData = stats ? [
    { name: 'Estoque',     value: stats.estoque,     color: '#3b82f6' },
    { name: 'Laboratório', value: stats.laboratorio, color: '#8b5cf6' },
    { name: 'Reservados',  value: stats.reservados,  color: '#f59e0b' },
    { name: 'Devolvidos',  value: stats.devolvidos,  color: '#22c55e' },
  ].filter(d => d.value > 0) : []

  return (
    <motion.div className="space-y-4" variants={stagger} initial="initial" animate="animate">
      <motion.div variants={fadeUp} className="flex items-center justify-between">
        <div>
          <h1 className="text-foreground text-lg font-semibold">Painel Operacional</h1>
          <p className="text-muted-foreground text-xs mt-0.5">Visão geral em tempo real</p>
        </div>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <TrendingUp size={12} className="text-green-500" />
          Atualizado agora
        </div>
      </motion.div>

      <div className="grid grid-cols-3 gap-4">
        <motion.div variants={fadeUp}><Card>
          <CardHeader className="pb-0 pt-4 px-4">
            <CardTitle className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
              <Activity size={11} /> Indicadores
            </CardTitle>
          </CardHeader>
          <CardContent className="px-4 pb-3">
            {kpis.map((kpi, i) => (
              <div key={kpi.label}>
                <KpiRow {...kpi} loading={loading} />
                {i < kpis.length - 1 && <Separator />}
              </div>
            ))}
          </CardContent>
        </Card></motion.div>

        <motion.div variants={fadeUp} className="col-span-2"><Card>
          <CardHeader className="pb-0 pt-4 px-4">
            <CardTitle className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Evolução mensal</CardTitle>
          </CardHeader>
          <CardContent className="px-4 pb-4">
            <ResponsiveContainer width="100%" height={210}>
              <AreaChart data={prodData} margin={{ top: 8, right: 4, left: -24, bottom: 0 }}>
                <defs>
                  <linearGradient id="gMed" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#22c55e" stopOpacity={0.12} />
                    <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="gLaud" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#3b82f6" stopOpacity={0.12} />
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="mes" tick={{ fill: 'hsl(215 12% 54%)', fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: 'hsl(215 12% 54%)', fontSize: 11 }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={{ background: 'hsl(222 12% 11%)', border: '1px solid hsl(222 10% 20%)', borderRadius: 6, fontSize: 12, color: 'hsl(210 20% 92%)' }} />
                <Area type="monotone" dataKey="medicoes" name="Medições" stroke="#22c55e" fill="url(#gMed)" strokeWidth={1.5} dot={false} />
                <Area type="monotone" dataKey="laudos"   name="Laudos"   stroke="#3b82f6" fill="url(#gLaud)" strokeWidth={1.5} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
            <div className="flex gap-4 justify-end mt-1">
              {[['#22c55e','Medições'],['#3b82f6','Laudos']].map(([c,l]) => (
                <div key={l} className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <div className="w-2 h-2 rounded-full" style={{ background: c }} />{l}
                </div>
              ))}
            </div>
          </CardContent>
        </Card></motion.div>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <motion.div variants={fadeUp}><Card>
          <CardHeader className="pb-0 pt-4 px-4">
            <CardTitle className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Amostradores</CardTitle>
          </CardHeader>
          <CardContent className="px-4 pb-4 mt-1">
            {pieData.length > 0 ? (
              <div className="flex items-center gap-4">
                <ResponsiveContainer width={90} height={90}>
                  <PieChart>
                    <Pie data={pieData} cx="50%" cy="50%" innerRadius={25} outerRadius={40} paddingAngle={3} dataKey="value">
                      {pieData.map(e => <Cell key={e.name} fill={e.color} />)}
                    </Pie>
                    <Tooltip contentStyle={{ background: 'hsl(222 12% 11%)', border: '1px solid hsl(222 10% 20%)', borderRadius: 6, fontSize: 11 }} />
                  </PieChart>
                </ResponsiveContainer>
                <div className="space-y-2">
                  {pieData.map(e => (
                    <div key={e.name} className="flex items-center gap-2 text-xs">
                      <div className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: e.color }} />
                      <span className="text-muted-foreground">{e.name}</span>
                      <span className="font-semibold ml-2">{e.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : <Skeleton className="h-20 w-20 rounded-full mx-auto mt-2" />}
          </CardContent>
        </Card></motion.div>

        <motion.div variants={fadeUp} className="col-span-2"><Card>
          <CardHeader className="pb-0 pt-4 px-4">
            <CardTitle className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Alertas</CardTitle>
          </CardHeader>
          <CardContent className="px-4 pb-4 space-y-3 mt-2">
            {stats?.venc_urgente > 0 ? (
              <div className="flex items-center gap-2.5 p-2.5 rounded-md border border-red-800/40 bg-red-950/20">
                <AlertTriangle size={13} className="text-red-400 shrink-0" />
                <p className="text-sm"><span className="font-semibold text-red-400">{stats.venc_urgente}</span> amostrador(es) com vencimento urgente</p>
                <button className="ml-auto text-xs text-red-400 hover:underline flex items-center gap-1 shrink-0">Ver <ArrowRight size={11} /></button>
              </div>
            ) : (
              <div className="flex items-center gap-2.5 p-2.5 rounded-md border border-border bg-secondary/20">
                <CheckCircle2 size={13} className="text-green-500 shrink-0" />
                <p className="text-xs text-muted-foreground">Nenhum alerta crítico</p>
              </div>
            )}
            <div>
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground/50 mb-2">Acesso rápido</p>
              <div className="grid grid-cols-3 gap-2">
                {[['Nova coleta', ClipboardList],['Dar baixa', Package],['Gerar PGR', CheckCircle2]].map(([label, Icon]) => (
                  <button key={label} className="flex items-center gap-2 p-2.5 rounded-md border border-border hover:bg-secondary transition-colors text-xs text-muted-foreground hover:text-foreground">
                    <Icon size={12} />{label}
                  </button>
                ))}
              </div>
            </div>
          </CardContent>
        </Card></motion.div>
      </div>
    </motion.div>
  )
}
