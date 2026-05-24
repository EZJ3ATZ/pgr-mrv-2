import { Player } from '@remotion/player'
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate, spring } from 'remotion'

// ── Composição Remotion — Relatório animado SST ───────────────────────────
function TitleSlide({ empresa, periodo, total, urgentes }) {
  const frame = useCurrentFrame()
  const { fps } = useVideoConfig()

  const titleOpacity = interpolate(frame, [0, 20], [0, 1])
  const subtitleY    = interpolate(frame, [10, 35], [20, 0], { extrapolateRight: 'clamp' })
  const subtitleOp   = interpolate(frame, [10, 35], [0, 1], { extrapolateRight: 'clamp' })

  const kpi1Scale = spring({ frame: frame - 40, fps, config: { damping: 12 } })
  const kpi2Scale = spring({ frame: frame - 55, fps, config: { damping: 12 } })
  const kpi3Scale = spring({ frame: frame - 70, fps, config: { damping: 12 } })

  const barWidth1 = interpolate(frame, [50, 90], [0, 73], { extrapolateRight: 'clamp' })
  const barWidth2 = interpolate(frame, [55, 95], [0, 22], { extrapolateRight: 'clamp' })
  const barWidth3 = interpolate(frame, [60, 100], [0, 5],  { extrapolateRight: 'clamp' })

  return (
    <AbsoluteFill style={{
      background: 'linear-gradient(135deg, #09090b 0%, #18181b 100%)',
      fontFamily: 'system-ui, -apple-system, sans-serif',
      padding: 48,
      display: 'flex',
      flexDirection: 'column',
      gap: 24,
    }}>
      {/* Logo bar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, opacity: titleOpacity }}>
        <div style={{
          width: 32, height: 32, borderRadius: 8,
          background: '#2563eb',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 16, color: 'white', fontWeight: 700,
        }}>O</div>
        <span style={{ color: '#e4e4e7', fontSize: 16, fontWeight: 600, letterSpacing: '-0.3px' }}>
          Ocupacional — Plataforma SST
        </span>
        <div style={{ marginLeft: 'auto', color: '#52525b', fontSize: 12 }}>{periodo}</div>
      </div>

      {/* Título */}
      <div style={{ opacity: titleOpacity }}>
        <h1 style={{
          color: '#ffffff', fontSize: 36, fontWeight: 700,
          margin: 0, letterSpacing: '-1px', lineHeight: 1.1,
        }}>
          Relatório de Segurança
        </h1>
        <p style={{
          color: '#71717a', fontSize: 16, marginTop: 6,
          transform: `translateY(${subtitleY}px)`,
          opacity: subtitleOp,
        }}>
          {empresa} · Análise de Ordens de Serviço
        </p>
      </div>

      {/* KPIs */}
      <div style={{ display: 'flex', gap: 16, marginTop: 8 }}>
        {[
          { label: 'Total OS', value: total, color: '#3b82f6', scale: kpi1Scale },
          { label: 'Urgentes', value: urgentes, color: '#f87171', scale: kpi2Scale },
          { label: 'Em andamento', value: Math.round(total * 0.09), color: '#fbbf24', scale: kpi3Scale },
        ].map(({ label, value, color, scale }) => (
          <div key={label} style={{
            flex: 1, background: '#18181b', borderRadius: 12,
            border: '1px solid #27272a', padding: '16px 20px',
            transform: `scale(${scale})`,
            transformOrigin: 'center bottom',
          }}>
            <div style={{ color: '#71717a', fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>
              {label}
            </div>
            <div style={{ color, fontSize: 32, fontWeight: 700, lineHeight: 1 }}>{value}</div>
          </div>
        ))}
      </div>

      {/* Barra de status */}
      <div>
        <div style={{ color: '#52525b', fontSize: 11, marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          Distribuição por Status
        </div>
        {[
          { label: 'Concluídas', pct: barWidth1, color: '#4ade80' },
          { label: 'Em andamento', pct: barWidth2, color: '#fbbf24' },
          { label: 'Urgentes', pct: barWidth3, color: '#f87171' },
        ].map(({ label, pct, color }) => (
          <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
            <span style={{ color: '#71717a', fontSize: 11, width: 100, flexShrink: 0 }}>{label}</span>
            <div style={{ flex: 1, height: 6, background: '#27272a', borderRadius: 3 }}>
              <div style={{
                width: `${pct}%`, height: '100%',
                background: color, borderRadius: 3,
                transition: 'width 0.1s',
              }} />
            </div>
            <span style={{ color, fontSize: 11, width: 32, textAlign: 'right' }}>{Math.round(pct)}%</span>
          </div>
        ))}
      </div>

      {/* Footer */}
      <div style={{
        marginTop: 'auto', color: '#3f3f46', fontSize: 10,
        opacity: interpolate(frame, [80, 100], [0, 1], { extrapolateRight: 'clamp' }),
        display: 'flex', justifyContent: 'space-between',
      }}>
        <span>Gerado automaticamente pela Plataforma SST</span>
        <span>Ocupacional Engenharia · {new Date().getFullYear()}</span>
      </div>
    </AbsoluteFill>
  )
}

// ── Player wrapper ────────────────────────────────────────────────────────
export default function RelatorioPlayer({ stats }) {
  const total    = stats?.medicoes_realizadas ?? 270
  const urgentes = stats?.venc_urgente ?? 5

  return (
    <div className="bg-card border border-border rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <div>
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Preview — Relatório Animado</p>
          <p className="text-[10px] text-muted-foreground/50 mt-0.5">Powered by Remotion</p>
        </div>
        <span className="text-[10px] bg-secondary border border-border px-2 py-0.5 rounded text-muted-foreground">
          🎬 Remotion Player
        </span>
      </div>
      <div className="rounded-lg overflow-hidden border border-border">
        <Player
          component={TitleSlide}
          inputProps={{
            empresa: 'MRV Engenharia',
            periodo: `Mai/${new Date().getFullYear()}`,
            total,
            urgentes,
          }}
          durationInFrames={120}
          compositionWidth={800}
          compositionHeight={450}
          fps={30}
          style={{ width: '100%', borderRadius: 8 }}
          controls
          loop
        />
      </div>
    </div>
  )
}
