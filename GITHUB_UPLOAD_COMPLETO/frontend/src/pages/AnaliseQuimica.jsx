import { useState, useRef } from 'react'
import { Plus, X, FileDown, AlertTriangle, CheckCircle2, Loader2, Image, ChevronDown, ChevronUp } from 'lucide-react'

const BOMBAS = [
  { value: 'airlite', label: 'AIRLITE – SKC' },
  { value: 'bdx',     label: 'BDX II – GILLIAN' },
  { value: 'turam',   label: 'FORMIS – TURAM' },
  { value: 'inlite',  label: 'INLITE VENTUSPRO' },
]
const CALIBRADORES = [
  { value: 'defender510m', label: 'DEFENDER 510M' },
  { value: 'tsi4143f',     label: 'TSI 4143F' },
]
const UFS = ['MG','SP','RJ','ES','GO','BA','PR','SC','RS','DF','MT','MS','PA']

function newAval() {
  return {
    cargo: '', trabalhador: '', setor: '', jornada: '8h',
    dataColeta: '', dataAnalise: '',
    agente: '', fonte: '', filtroNumero: '',
    vazaoInicial: '', vazaoFinal: '', tempoColeta: '', volume: '',
    tempoExposicao: '', acessorios: '',
    ltNR15: '', naNR15: '', ltTWA: '', naTWA: '', ltSTEL: '',
    concentracao: '', conclusao: '',
    expanded: true,
  }
}

function imgToB64(file) {
  return new Promise((res, rej) => {
    const r = new FileReader()
    r.onload = e => res(e.target.result)
    r.onerror = rej
    r.readAsDataURL(file)
  })
}

export default function AnaliseQuimica() {
  const [empresa, setEmpresa] = useState({
    razaoSocial: '', titulo: '', logo: '',
    cnpj: '', endereco: '', numero: '', cep: '',
    bairro: '', cidade: 'Belo Horizonte', uf: 'MG',
    cnae: '', descricaoCnae: '',
  })
  const [config, setConfig] = useState({
    bomba: 'airlite', bombaSN: '', calibrador: 'defender510m',
    fotosVIII: [], laudoImgs: [],
  })
  const [avaliacoes, setAvaliacoes] = useState([newAval()])
  const [gerando, setGerando] = useState(false)
  const [status, setStatus] = useState(null)
  const logoRef = useRef()

  function setEmp(k, v) { setEmpresa(e => ({ ...e, [k]: v })) }
  function setConf(k, v) { setConfig(c => ({ ...c, [k]: v })) }

  function setAval(i, k, v) {
    setAvaliacoes(a => a.map((x, idx) => idx === i ? { ...x, [k]: v } : x))
  }
  function addAval() { setAvaliacoes(a => [...a, newAval()]) }
  function removeAval(i) { setAvaliacoes(a => a.filter((_, idx) => idx !== i)) }
  function toggleAval(i) { setAvaliacoes(a => a.map((x, idx) => idx === i ? { ...x, expanded: !x.expanded } : x)) }

  async function handleLogo(file) {
    if (!file) return
    setEmp('logo', await imgToB64(file))
  }

  async function handleFotoVIII(file) {
    if (!file) return
    const b64 = await imgToB64(file)
    setConfig(c => ({ ...c, fotosVIII: [...c.fotosVIII, { img: b64, desc: '' }] }))
  }

  function setFotoDesc(i, desc) {
    setConfig(c => ({ ...c, fotosVIII: c.fotosVIII.map((f, idx) => idx === i ? { ...f, desc } : f) }))
  }
  function removeFoto(i) {
    setConfig(c => ({ ...c, fotosVIII: c.fotosVIII.filter((_, idx) => idx !== i) }))
  }

  async function handleLaudoImg(file) {
    if (!file) return
    const b64 = await imgToB64(file)
    setConfig(c => ({ ...c, laudoImgs: [...c.laudoImgs, b64] }))
  }
  function removeLaudoImg(i) {
    setConfig(c => ({ ...c, laudoImgs: c.laudoImgs.filter((_, idx) => idx !== i) }))
  }

  async function gerar() {
    setStatus(null)
    if (!empresa.razaoSocial.trim()) { setStatus({ tipo: 'erro', msg: 'Informe a Razão Social.' }); return }
    if (avaliacoes.length === 0) { setStatus({ tipo: 'erro', msg: 'Adicione pelo menos uma avaliação.' }); return }
    setGerando(true)
    try {
      const payload = {
        empresa: { ...empresa },
        config: { ...config },
        avaliacoes: avaliacoes.map(({ expanded, ...av }) => av),
      }
      const r = await fetch('/gerar_quimico', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setStatus({ tipo: 'erro', msg: d.erro || `Erro ${r.status}` })
        return
      }
      const blob = await r.blob()
      const cd = r.headers.get('Content-Disposition') || ''
      const fn = cd.match(/filename="?([^"]+)"?/)?.[1] || 'Analise_Quimica.docx'
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a'); a.href = url; a.download = fn; a.click()
      URL.revokeObjectURL(url)
      setStatus({ tipo: 'ok', msg: `${fn} gerado com sucesso!` })
    } catch {
      setStatus({ tipo: 'erro', msg: 'Erro de conexão.' })
    } finally {
      setGerando(false)
    }
  }

  return (
    <div className="space-y-4 max-w-4xl">
      <div>
        <h1 className="text-foreground text-lg font-semibold">Análise Química — Agentes Químicos</h1>
        <p className="text-muted-foreground text-xs mt-0.5">Preencha os dados, configure o equipamento, adicione as avaliações e gere o laudo.</p>
      </div>

      {/* Dados da Empresa */}
      <div className="bg-card border border-border rounded-lg p-4 space-y-3">
        <p className="text-sm font-semibold text-foreground">Dados da Empresa</p>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-muted-foreground">Razão Social</label>
            <input className="form-input mt-1" placeholder="Nome completo da empresa"
              value={empresa.razaoSocial} onChange={e => setEmp('razaoSocial', e.target.value)} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">Título (cabeçalho do laudo)</label>
            <input className="form-input mt-1" placeholder="Deixe em branco para usar a Razão Social"
              value={empresa.titulo} onChange={e => setEmp('titulo', e.target.value)} />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-muted-foreground">CNPJ</label>
            <input className="form-input mt-1" placeholder="00.000.000/0001-00"
              value={empresa.cnpj} onChange={e => setEmp('cnpj', e.target.value)} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">CEP</label>
            <input className="form-input mt-1" placeholder="00000-000"
              value={empresa.cep} onChange={e => setEmp('cep', e.target.value)} />
          </div>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div className="col-span-2">
            <label className="text-xs text-muted-foreground">Endereço (Rua / Avenida)</label>
            <input className="form-input mt-1" placeholder="Rua / Avenida"
              value={empresa.endereco} onChange={e => setEmp('endereco', e.target.value)} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">Número</label>
            <input className="form-input mt-1" placeholder="Nº"
              value={empresa.numero} onChange={e => setEmp('numero', e.target.value)} />
          </div>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="text-xs text-muted-foreground">Bairro</label>
            <input className="form-input mt-1" placeholder="Bairro"
              value={empresa.bairro} onChange={e => setEmp('bairro', e.target.value)} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">Cidade</label>
            <input className="form-input mt-1" placeholder="Cidade"
              value={empresa.cidade} onChange={e => setEmp('cidade', e.target.value)} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">UF</label>
            <select className="form-input mt-1" value={empresa.uf} onChange={e => setEmp('uf', e.target.value)}>
              {UFS.map(u => <option key={u} value={u}>{u}</option>)}
            </select>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-muted-foreground">CNAE</label>
            <input className="form-input mt-1" placeholder="00.00-0/00"
              value={empresa.cnae} onChange={e => setEmp('cnae', e.target.value)} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">Descrição CNAE</label>
            <input className="form-input mt-1" placeholder="Atividade econômica"
              value={empresa.descricaoCnae} onChange={e => setEmp('descricaoCnae', e.target.value)} />
          </div>
        </div>
        <div>
          <label className="text-xs text-muted-foreground">Logo da Empresa <span className="text-muted-foreground/60">(opcional)</span></label>
          <button className="btn-secondary mt-1" onClick={() => logoRef.current?.click()}>
            <Image size={13} />
            {empresa.logo ? 'Logo carregado ✓' : 'Selecionar logo (PNG/JPG)'}
          </button>
          <input ref={logoRef} type="file" accept="image/*" className="hidden"
            onChange={e => handleLogo(e.target.files[0])} />
        </div>
      </div>

      {/* Config Equipamento */}
      <div className="bg-card border border-border rounded-lg p-4 space-y-3">
        <p className="text-sm font-semibold text-foreground">Equipamentos de Coleta</p>
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="text-xs text-muted-foreground">Bomba de Amostragem</label>
            <select className="form-input mt-1" value={config.bomba} onChange={e => setConf('bomba', e.target.value)}>
              {BOMBAS.map(b => <option key={b.value} value={b.value}>{b.label}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-muted-foreground">Nº de Série da Bomba</label>
            <input className="form-input mt-1" placeholder="Ex: A060502"
              value={config.bombaSN} onChange={e => setConf('bombaSN', e.target.value)} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">Calibrador</label>
            <select className="form-input mt-1" value={config.calibrador} onChange={e => setConf('calibrador', e.target.value)}>
              {CALIBRADORES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </div>
        </div>

        {/* Fotos Memorial Fotográfico */}
        <div>
          <label className="text-xs text-muted-foreground block mb-1">Memorial Fotográfico (Seção VIII) <span className="text-muted-foreground/60">— opcional</span></label>
          <label className="btn-secondary cursor-pointer inline-flex">
            <Plus size={13} /> Adicionar foto
            <input type="file" accept="image/*" className="hidden"
              onChange={e => handleFotoVIII(e.target.files[0])} />
          </label>
          {config.fotosVIII.length > 0 && (
            <div className="space-y-2 mt-2">
              {config.fotosVIII.map((f, i) => (
                <div key={i} className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground w-16 shrink-0">Foto {i+1}</span>
                  <input className="form-input flex-1" placeholder="Descrição da foto"
                    value={f.desc} onChange={e => setFotoDesc(i, e.target.value)} />
                  <button onClick={() => removeFoto(i)} className="text-muted-foreground hover:text-red-400">
                    <X size={13} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Laudos do laboratório */}
        <div>
          <label className="text-xs text-muted-foreground block mb-1">Resultados do Laboratório (Seção X) <span className="text-muted-foreground/60">— opcional</span></label>
          <label className="btn-secondary cursor-pointer inline-flex">
            <Plus size={13} /> Adicionar imagem do laudo
            <input type="file" accept="image/*" className="hidden"
              onChange={e => handleLaudoImg(e.target.files[0])} />
          </label>
          {config.laudoImgs.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {config.laudoImgs.map((_, i) => (
                <div key={i} className="flex items-center gap-1 px-2 py-1 bg-secondary rounded text-xs text-muted-foreground">
                  Laudo pg. {i+1}
                  <button onClick={() => removeLaudoImg(i)} className="hover:text-red-400"><X size={11} /></button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Avaliações */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <p className="text-sm font-semibold text-foreground">Avaliações de Agentes Químicos</p>
          <button className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded-md transition-colors"
            onClick={addAval}>
            <Plus size={14} /> Adicionar Avaliação
          </button>
        </div>

        {avaliacoes.map((av, i) => (
          <div key={i} className="bg-card border border-border rounded-lg overflow-hidden">
            <div className="flex items-center justify-between px-4 py-2.5 bg-secondary/30 border-b border-border">
              <button className="flex items-center gap-2 text-sm font-medium text-foreground" onClick={() => toggleAval(i)}>
                {av.expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                Avaliação {String(i+1).padStart(2,'0')} {av.cargo ? `— ${av.cargo}` : ''} {av.agente ? `| ${av.agente}` : ''}
              </button>
              {avaliacoes.length > 1 && (
                <button onClick={() => removeAval(i)} className="text-muted-foreground hover:text-red-400 transition-colors">
                  <X size={14} />
                </button>
              )}
            </div>

            {av.expanded && (
              <div className="p-4 space-y-3">
                {/* Identificação */}
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="text-xs text-muted-foreground">Cargo</label>
                    <input className="form-input mt-1" placeholder="Ex: Pintor"
                      value={av.cargo} onChange={e => setAval(i, 'cargo', e.target.value)} />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">Trabalhador</label>
                    <input className="form-input mt-1" placeholder="Nome"
                      value={av.trabalhador} onChange={e => setAval(i, 'trabalhador', e.target.value)} />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">Setor</label>
                    <input className="form-input mt-1" placeholder="Ex: Pintura"
                      value={av.setor} onChange={e => setAval(i, 'setor', e.target.value)} />
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="text-xs text-muted-foreground">Jornada</label>
                    <input className="form-input mt-1" placeholder="8h"
                      value={av.jornada} onChange={e => setAval(i, 'jornada', e.target.value)} />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">Data da Coleta</label>
                    <input className="form-input mt-1" placeholder="dd/mm/aaaa"
                      value={av.dataColeta} onChange={e => setAval(i, 'dataColeta', e.target.value)} />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">Data da Análise</label>
                    <input className="form-input mt-1" placeholder="dd/mm/aaaa"
                      value={av.dataAnalise} onChange={e => setAval(i, 'dataAnalise', e.target.value)} />
                  </div>
                </div>

                {/* Agente e fonte */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs text-muted-foreground">Agentes Analisados (CAS)</label>
                    <input className="form-input mt-1" placeholder="Ex: Tolueno (108-88-3)"
                      value={av.agente} onChange={e => setAval(i, 'agente', e.target.value)} />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">Fonte Geradora</label>
                    <input className="form-input mt-1" placeholder="Ex: Tintas e solventes"
                      value={av.fonte} onChange={e => setAval(i, 'fonte', e.target.value)} />
                  </div>
                </div>

                {/* Amostragem */}
                <div className="border border-border/50 rounded-md p-3 space-y-3">
                  <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Dados da Amostragem</p>
                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <label className="text-xs text-muted-foreground">Filtro Número</label>
                      <input className="form-input mt-1" placeholder="Ex: F-001"
                        value={av.filtroNumero} onChange={e => setAval(i, 'filtroNumero', e.target.value)} />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground">Vazão Inicial (L/min)</label>
                      <input className="form-input mt-1" placeholder="0.0"
                        value={av.vazaoInicial} onChange={e => setAval(i, 'vazaoInicial', e.target.value)} />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground">Vazão Final (L/min)</label>
                      <input className="form-input mt-1" placeholder="0.0"
                        value={av.vazaoFinal} onChange={e => setAval(i, 'vazaoFinal', e.target.value)} />
                    </div>
                  </div>
                  <div className="grid grid-cols-4 gap-3">
                    <div>
                      <label className="text-xs text-muted-foreground">Tempo Coleta (min)</label>
                      <input className="form-input mt-1" placeholder="0"
                        value={av.tempoColeta} onChange={e => setAval(i, 'tempoColeta', e.target.value)} />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground">Volume Amostrado (L)</label>
                      <input className="form-input mt-1" placeholder="0.0"
                        value={av.volume} onChange={e => setAval(i, 'volume', e.target.value)} />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground">Tempo Exposição (h)</label>
                      <input className="form-input mt-1" placeholder="0"
                        value={av.tempoExposicao} onChange={e => setAval(i, 'tempoExposicao', e.target.value)} />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground">Acessórios</label>
                      <input className="form-input mt-1" placeholder="Ex: Impinger"
                        value={av.acessorios} onChange={e => setAval(i, 'acessorios', e.target.value)} />
                    </div>
                  </div>
                </div>

                {/* Limites e resultado */}
                <div className="border border-border/50 rounded-md p-3 space-y-3">
                  <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Limites de Tolerância e Resultado</p>
                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <label className="text-xs text-muted-foreground">LT NR-15 (ppm)</label>
                      <input className="form-input mt-1" placeholder="0"
                        value={av.ltNR15} onChange={e => setAval(i, 'ltNR15', e.target.value)} />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground">NA NR-15 (ppm)</label>
                      <input className="form-input mt-1" placeholder="0"
                        value={av.naNR15} onChange={e => setAval(i, 'naNR15', e.target.value)} />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground">Concentração (ppm)</label>
                      <input className="form-input mt-1" placeholder="0.0"
                        value={av.concentracao} onChange={e => setAval(i, 'concentracao', e.target.value)} />
                    </div>
                  </div>
                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <label className="text-xs text-muted-foreground">LT TWA (ppm)</label>
                      <input className="form-input mt-1" placeholder="0"
                        value={av.ltTWA} onChange={e => setAval(i, 'ltTWA', e.target.value)} />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground">NA TWA (ppm)</label>
                      <input className="form-input mt-1" placeholder="0"
                        value={av.naTWA} onChange={e => setAval(i, 'naTWA', e.target.value)} />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground">LT STEL (ppm)</label>
                      <input className="form-input mt-1" placeholder="0"
                        value={av.ltSTEL} onChange={e => setAval(i, 'ltSTEL', e.target.value)} />
                    </div>
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">Conclusão <span className="text-muted-foreground/60">(deixe em branco para conclusão automática)</span></label>
                    <textarea className="form-input mt-1" rows={2}
                      placeholder="Automático: regular se C < LT"
                      value={av.conclusao} onChange={e => setAval(i, 'conclusao', e.target.value)} />
                  </div>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Status */}
      {status && (
        <div className={`flex gap-2 p-3 rounded-lg border text-sm ${
          status.tipo === 'ok' ? 'bg-green-950/30 border-green-800/40 text-green-400' :
          'bg-red-950/30 border-red-800/40 text-red-400'
        }`}>
          {status.tipo === 'ok' ? <CheckCircle2 size={16} className="shrink-0 mt-0.5" /> : <AlertTriangle size={16} className="shrink-0 mt-0.5" />}
          <p>{status.msg}</p>
        </div>
      )}

      <button
        className="flex items-center gap-2 px-6 py-3 bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white font-semibold rounded-lg transition-colors w-full justify-center"
        onClick={gerar}
        disabled={gerando}
      >
        {gerando ? <Loader2 size={17} className="animate-spin" /> : <FileDown size={17} />}
        {gerando ? 'Gerando...' : 'Gerar Análise Química'}
      </button>
    </div>
  )
}
