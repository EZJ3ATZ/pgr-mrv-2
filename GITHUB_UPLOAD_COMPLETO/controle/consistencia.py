# -*- coding: utf-8 -*-
"""
Motor de Consistência Operacional SST — v1.0
=============================================
NÃO usa IA / LLM.
Opera por regras de negócio puras:
  - Comparação planejamento vs campo
  - Validação de métodos analíticos (normas FUNDACENTRO/NIOSH/NR-15)
  - Detecção de divergências operacionais
  - Motor de alertas estruturado
  - Rastreabilidade completa
"""
import json
import re
from datetime import datetime, timedelta
from .db import get_db, USE_PG

# ════════════════════════════════════════════════════════════════════════
# CONSTANTES OPERACIONAIS
# ════════════════════════════════════════════════════════════════════════

MOTIVOS_DIVERGENCIA = {
    'culpa_cliente':         'Culpa do cliente (acesso negado, área interditada)',
    'impossibilidade_op':    'Impossibilidade operacional no campo',
    'ausencia_funcionario':  'Ausência do funcionário para monitorar',
    'chuva':                 'Condições climáticas adversas',
    'equipamento':           'Falha ou indisponibilidade de equipamento',
    'erro_planejamento':     'Erro no planejamento original',
    'revisita_necessaria':   'Medição incompleta — revisita necessária',
    'outros':                'Outros (ver observação)',
}

SEVERIDADES = ('critico', 'alto', 'medio', 'baixo')

# Tabela de métodos analíticos (normas FUNDACENTRO / NIOSH / NR-15)
# vazao_min / vazao_max em L/min. None = sem requisito de vazão (ex: ruído)
METODOS_ANALITICOS = {
    'ruido':         {'vazao_min': None, 'vazao_max': None, 'norma': 'NHO-01 / NR-15 A1',    'equip': 'Dosímetro de ruído'},
    'calor':         {'vazao_min': None, 'vazao_max': None, 'norma': 'NR-15 A3',              'equip': 'Termômetro IBUTG'},
    'vibracao_vci':  {'vazao_min': None, 'vazao_max': None, 'norma': 'NR-9 / ISO 2631',       'equip': 'Acelerômetro VCI'},
    'vibracao_vmb':  {'vazao_min': None, 'vazao_max': None, 'norma': 'NR-9 / ISO 5349',       'equip': 'Acelerômetro VMB'},
    'poeira_total':  {'vazao_min': 1.5,  'vazao_max': 3.0,  'norma': 'NIOSH 0500',            'equip': 'Bomba + Cassete PVC 37mm'},
    'poeira_resp':   {'vazao_min': 1.7,  'vazao_max': 1.7,  'norma': 'NIOSH 0600',            'equip': 'Bomba + Ciclone 10mm'},
    'silica':        {'vazao_min': 1.7,  'vazao_max': 1.7,  'norma': 'NIOSH 7500 / 7602',     'equip': 'Bomba + Ciclone 10mm'},
    'benzeno':       {'vazao_min': 0.05, 'vazao_max': 0.20, 'norma': 'NIOSH 1501',            'equip': 'Bomba + Tubo Carvão Ativado'},
    'tolueno':       {'vazao_min': 0.05, 'vazao_max': 0.20, 'norma': 'NIOSH 1501',            'equip': 'Bomba + Tubo Carvão Ativado'},
    'btx':           {'vazao_min': 0.05, 'vazao_max': 0.20, 'norma': 'NIOSH 1501',            'equip': 'Bomba + Tubo Carvão Ativado'},
    'xileno':        {'vazao_min': 0.05, 'vazao_max': 0.20, 'norma': 'NIOSH 1501',            'equip': 'Bomba + Tubo Carvão Ativado'},
    'hexano':        {'vazao_min': 0.05, 'vazao_max': 0.20, 'norma': 'NIOSH 1500',            'equip': 'Bomba + Tubo Carvão Ativado'},
    'metais':        {'vazao_min': 1.0,  'vazao_max': 2.0,  'norma': 'NIOSH 7300',            'equip': 'Bomba + Filtro MCE 0.8µm'},
    'manganes':      {'vazao_min': 1.0,  'vazao_max': 2.0,  'norma': 'NIOSH 7300',            'equip': 'Bomba + Filtro MCE 0.8µm'},
    'chumbo':        {'vazao_min': 1.0,  'vazao_max': 2.0,  'norma': 'NIOSH 7082 / 7300',     'equip': 'Bomba + Filtro MCE 0.8µm'},
    'cromio':        {'vazao_min': 1.0,  'vazao_max': 2.0,  'norma': 'NIOSH 7300',            'equip': 'Bomba + Filtro MCE 0.8µm'},
    'gases_vapores': {'vazao_min': 0.05, 'vazao_max': 0.20, 'norma': 'NIOSH 1500 / 2000',     'equip': 'Bomba + Tubo adsorvente'},
    'acidos':        {'vazao_min': 0.20, 'vazao_max': 1.0,  'norma': 'NIOSH 7903',            'equip': 'Bomba + Filtro PVC / Impinger'},
    'formaldeid':    {'vazao_min': 0.05, 'vazao_max': 1.0,  'norma': 'NIOSH 2016',            'equip': 'Bomba + Tubo DNPH'},
    'co':            {'vazao_min': 0.05, 'vazao_max': 0.20, 'norma': 'NIOSH 6604',            'equip': 'Bomba + Tubo Hopcalite'},
}

# Aliases: nome livre → chave canônica
_ALIAS = {
    'ruído': 'ruido', 'ruido': 'ruido', 'dosimetria': 'ruido', 'nho-01': 'ruido', 'noise': 'ruido',
    'calor': 'calor', 'ibutg': 'calor', 'temperatura': 'calor', 'heat': 'calor',
    'vibração': 'vibracao_vci', 'vibracao': 'vibracao_vci', 'vci': 'vibracao_vci',
    'vibração vci': 'vibracao_vci', 'vibração vmb': 'vibracao_vmb', 'vmb': 'vibracao_vmb',
    'poeira total': 'poeira_total', 'poeira resp': 'poeira_resp',
    'poeira respirável': 'poeira_resp', 'poeira respiravel': 'poeira_resp',
    'poeira': 'poeira_total', 'dust': 'poeira_total',
    'sílica': 'silica', 'silica': 'silica', 'sio2': 'silica', 'quartzo': 'silica',
    'benzeno': 'benzeno', 'benzene': 'benzeno',
    'btx': 'btx', 'btex': 'btx',
    'tolueno': 'tolueno', 'toluene': 'tolueno',
    'xileno': 'xileno', 'xylene': 'xileno',
    'hexano': 'hexano',
    'metais': 'metais', 'metal': 'metais', 'metals': 'metais',
    'manganês': 'manganes', 'manganes': 'manganes', 'mn': 'manganes',
    'chumbo': 'chumbo', 'pb': 'chumbo', 'lead': 'chumbo',
    'crômio': 'cromio', 'cromio': 'cromio', 'cr': 'cromio',
    'gases e vapores': 'gases_vapores', 'gases': 'gases_vapores', 'vapores': 'gases_vapores',
    'ácidos': 'acidos', 'acidos': 'acidos', 'acido sulfurico': 'acidos', 'ácido sulfúrico': 'acidos',
    'formaldeído': 'formaldeid', 'formaldeid': 'formaldeid', 'formol': 'formaldeid',
    'monóxido': 'co', 'co': 'co', 'monoxide': 'co',
}


def _chave_metodo(nome: str) -> str | None:
    return _ALIAS.get(nome.lower().strip())


def _parse_lista(v) -> list:
    if not v:
        return []
    if isinstance(v, list):
        return v
    try:
        parsed = json.loads(v)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, str):
            return [s.strip() for s in parsed.split(',') if s.strip()]
    except Exception:
        pass
    return [s.strip() for s in str(v).split(',') if s.strip()]


def _ph():
    return '%s' if USE_PG else '?'


# ════════════════════════════════════════════════════════════════════════
# 1. VALIDAÇÃO DE MÉTODO / VAZÃO
# ════════════════════════════════════════════════════════════════════════

def validar_vazao(agente_nome: str, vazao_lpm: float) -> dict:
    """
    Valida se a vazão informada é compatível com o método do agente.
    Retorna: {ok, alerta, norma, equip, vazao_min, vazao_max}
    """
    chave = _chave_metodo(agente_nome)
    if not chave or chave not in METODOS_ANALITICOS:
        return {'ok': True, 'alerta': None, 'norma': 'N/A', 'equip': None,
                'vazao_min': None, 'vazao_max': None}
    m = METODOS_ANALITICOS[chave]
    if m['vazao_min'] is None:
        return {'ok': True, 'alerta': None, 'norma': m['norma'], 'equip': m['equip'],
                'vazao_min': None, 'vazao_max': None}
    ok = m['vazao_min'] <= vazao_lpm <= m['vazao_max']
    alerta = None
    if not ok:
        alerta = (
            f"Vazão {vazao_lpm} L/min incompatível com {agente_nome} "
            f"(esperado {m['vazao_min']}–{m['vazao_max']} L/min, norma {m['norma']})"
        )
    return {'ok': ok, 'alerta': alerta, 'norma': m['norma'], 'equip': m['equip'],
            'vazao_min': m['vazao_min'], 'vazao_max': m['vazao_max']}


def info_metodo(agente_nome: str) -> dict | None:
    """Retorna especificações do método para um agente."""
    chave = _chave_metodo(agente_nome)
    if not chave:
        return None
    return METODOS_ANALITICOS.get(chave)


# ════════════════════════════════════════════════════════════════════════
# 2. CRUZAMENTO PLANEJAMENTO vs CAMPO
# ════════════════════════════════════════════════════════════════════════

def cruzar_plano_campo(plan_id: int, visita_id: int) -> list:
    """
    Compara agentes planejados vs executados.
    Retorna lista de dicts de divergência.
    """
    divs = []
    with get_db() as conn:
        plan = conn.execute(
            'SELECT * FROM planejamentos WHERE id=?', (plan_id,)
        ).fetchone()
        row = conn.execute('''
            SELECT vt.resultado, vt.justificativa,
                   ec.agentes_executados, ec.agentes_nao_executados,
                   ec.agentes_adicionados, ec.dosimetros_usados, ec.bombas_usadas
            FROM visitas_tecnicas vt
            LEFT JOIN execucao_campo ec ON ec.visita_id = vt.id
            WHERE vt.id=?
        ''', (visita_id,)).fetchone()

    if not plan or not row:
        return []

    p = dict(plan)
    v = dict(row)

    ag_plan = _parse_lista(p.get('agentes_previstos'))
    ag_exec = _parse_lista(v.get('agentes_executados'))
    ag_nao  = _parse_lista(v.get('agentes_nao_executados'))
    ag_add  = _parse_lista(v.get('agentes_adicionados'))

    qtd_plan = len(ag_plan)
    qtd_exec = len(ag_exec)
    qtd_nao  = len(ag_nao)

    # ── Agentes faltando sem justificativa ──
    faltando = qtd_plan - qtd_exec - qtd_nao
    if faltando > 0:
        divs.append({
            'tipo': 'agentes_faltando',
            'severidade': 'alto',
            'descricao': (
                f'Planejamento previa {qtd_plan} agente(s), '
                f'{qtd_exec} executado(s), {qtd_nao} justificado(s). '
                f'{faltando} sem justificativa.'
            ),
            'entidade_tipo': 'visita',
            'entidade_id': visita_id,
        })

    # ── Agentes executados sem previsão ──
    if ag_add:
        divs.append({
            'tipo': 'agente_nao_previsto',
            'severidade': 'medio',
            'descricao': f'Agente(s) sem previsão executado(s) no campo: {", ".join(str(a) for a in ag_add)}.',
            'entidade_tipo': 'visita',
            'entidade_id': visita_id,
        })

    # ── Dosímetros ──
    qtd_dosim = int(p.get('qtd_dosim_prevista') or 0)
    if qtd_dosim > 0 and not v.get('dosimetros_usados'):
        divs.append({
            'tipo': 'equipamento_nao_informado',
            'severidade': 'medio',
            'descricao': f'Planejamento previa {qtd_dosim} dosímetro(s) — nenhum registrado na visita.',
            'entidade_tipo': 'visita',
            'entidade_id': visita_id,
        })

    # ── Bombas ──
    qtd_bombas = int(p.get('qtd_bombas_previstas') or 0)
    if qtd_bombas > 0 and not v.get('bombas_usadas'):
        divs.append({
            'tipo': 'equipamento_nao_informado',
            'severidade': 'medio',
            'descricao': f'Planejamento previa {qtd_bombas} bomba(s) — nenhuma registrada na visita.',
            'entidade_tipo': 'visita',
            'entidade_id': visita_id,
        })

    # ── Resultado reagendar/parcial sem justificativa ──
    resultado = v.get('resultado', '')
    justif = v.get('justificativa', '')
    if resultado in ('reagendar', 'parcial', 'cancelado') and not justif:
        divs.append({
            'tipo': 'resultado_sem_justificativa',
            'severidade': 'alto',
            'descricao': f'Visita marcada como "{resultado}" sem justificativa registrada.',
            'entidade_tipo': 'visita',
            'entidade_id': visita_id,
        })

    return divs


# ════════════════════════════════════════════════════════════════════════
# 3. CICLO COMPLETO DA OS
# ════════════════════════════════════════════════════════════════════════

def validar_os_ciclo(demanda_id: int) -> dict:
    """
    Valida o ciclo completo: Demanda → Planejamento → Visita → Coleta → Resultado.
    Retorna: {etapas, divergencias, score 0-100}
    """
    resultado = {
        'demanda_id': demanda_id,
        'etapas': {},
        'divergencias': [],
        'score': 0,
    }
    with get_db() as conn:
        dem = conn.execute('SELECT * FROM demandas WHERE id=?', (demanda_id,)).fetchone()
        if not dem:
            return resultado
        dem = dict(dem)

        # Etapa 1: Planejamento
        plan = conn.execute(
            'SELECT * FROM planejamentos WHERE demanda_id=? ORDER BY criado_em DESC LIMIT 1',
            (demanda_id,)
        ).fetchone()
        resultado['etapas']['planejamento'] = 'ok' if plan else 'pendente'
        if not plan:
            resultado['divergencias'].append({
                'tipo': 'sem_planejamento', 'severidade': 'alto',
                'descricao': 'OS sem planejamento criado.',
                'entidade_tipo': 'demanda', 'entidade_id': demanda_id,
            })

        # Etapa 2: Visita
        visita = None
        if plan:
            visita = conn.execute(
                'SELECT * FROM visitas_tecnicas WHERE planejamento_id=? ORDER BY criado_em DESC LIMIT 1',
                (plan['id'],)
            ).fetchone()
        resultado['etapas']['visita'] = 'ok' if visita else 'pendente'

        # Etapa 3: Execução de campo
        exec_ok = False
        if visita:
            ec = conn.execute(
                'SELECT id FROM execucao_campo WHERE visita_id=? LIMIT 1', (visita['id'],)
            ).fetchone()
            exec_ok = bool(ec)
        resultado['etapas']['execucao'] = 'ok' if exec_ok else 'pendente'

        # Etapa 4: Coleta técnica (ruído ou químico)
        coleta_ok = False
        if visita:
            cr = conn.execute(
                'SELECT id FROM coletas_ruido WHERE visita_id=? LIMIT 1', (visita['id'],)
            ).fetchone()
            if not cr:
                cr = conn.execute(
                    'SELECT id FROM coletas_quimico WHERE visita_id=? LIMIT 1', (visita['id'],)
                ).fetchone()
            coleta_ok = bool(cr)
        resultado['etapas']['coleta'] = 'ok' if coleta_ok else 'pendente'

        # Prazo vencido
        prazo = dem.get('prazo')
        if prazo:
            try:
                prazo_dt = datetime.strptime(str(prazo)[:10], '%Y-%m-%d')
                if prazo_dt < datetime.now() and resultado['etapas'].get('coleta') != 'ok':
                    resultado['divergencias'].append({
                        'tipo': 'prazo_vencido', 'severidade': 'critico',
                        'descricao': f'OS com prazo vencido em {prazo[:10]} sem coleta concluída.',
                        'entidade_tipo': 'demanda', 'entidade_id': demanda_id,
                    })
            except Exception:
                pass

        # Score: proporção de etapas concluídas
        etapas_ok = sum(1 for v in resultado['etapas'].values() if v == 'ok')
        resultado['score'] = int(etapas_ok / len(resultado['etapas']) * 100)

    return resultado


# ════════════════════════════════════════════════════════════════════════
# 4. DETECÇÕES AUTOMATIZADAS
# ════════════════════════════════════════════════════════════════════════

def detectar_os_paradas(dias: int = 15) -> list:
    """Demandas ativas sem visita há mais de N dias."""
    limite = (datetime.now() - timedelta(days=dias)).isoformat()[:10]
    alertas = []
    with get_db() as conn:
        try:
            rows = conn.execute('''
                SELECT d.id, d.numero_os, e.nome AS empresa_nome,
                       MAX(vt.data_visita) AS ultima_visita
                FROM demandas d
                LEFT JOIN empresas e ON e.id = d.empresa_id
                LEFT JOIN planejamentos p ON p.demanda_id = d.id
                LEFT JOIN visitas_tecnicas vt ON vt.planejamento_id = p.id
                WHERE d.status NOT IN ('concluido','cancelado','removido')
                  AND (d.empresa_id IS NULL OR d.empresa_id > 0)
                GROUP BY d.id, d.numero_os, e.nome
                HAVING (ultima_visita IS NULL OR ultima_visita < ?)
                   AND d.criado_em < ?
                LIMIT 100
            ''', (limite, limite)).fetchall()
        except Exception:
            return []
        for r in rows:
            r = dict(r)
            alertas.append({
                'tipo': 'os_parada', 'severidade': 'medio',
                'descricao': (
                    f'OS {r.get("numero_os") or "—"} ({r.get("empresa_nome") or "—"}) '
                    f'sem visita há mais de {dias} dias.'
                ),
                'entidade_tipo': 'demanda',
                'entidade_id': r['id'],
            })
    return alertas


def detectar_demandas_orfas() -> list:
    """Demandas sem empresa vinculada — invisíveis no painel."""
    alertas = []
    with get_db() as conn:
        try:
            rows = conn.execute(
                'SELECT id, numero_os, titulo FROM demandas '
                'WHERE (empresa_id IS NULL OR empresa_id = 0) LIMIT 100'
            ).fetchall()
        except Exception:
            return []
        for r in rows:
            r = dict(r)
            alertas.append({
                'tipo': 'demanda_orfa', 'severidade': 'alto',
                'descricao': (
                    f'OS {r.get("numero_os") or "—"} sem empresa vinculada — '
                    'não aparece no painel operacional.'
                ),
                'entidade_tipo': 'demanda',
                'entidade_id': r['id'],
            })
    return alertas


def detectar_amostradores_vencendo(dias_alerta: int = 30) -> list:
    """Amostradores com calibração vencendo nos próximos N dias."""
    limite = (datetime.now() + timedelta(days=dias_alerta)).isoformat()[:10]
    hoje = datetime.now().isoformat()[:10]
    alertas = []
    with get_db() as conn:
        try:
            rows = conn.execute('''
                SELECT id, codigo, tipo, proximo_calibracao
                FROM amostradores
                WHERE proximo_calibracao IS NOT NULL
                  AND proximo_calibracao <= ?
                  AND (ativo = 1 OR ativo IS NULL)
                ORDER BY proximo_calibracao
                LIMIT 50
            ''', (limite,)).fetchall()
        except Exception:
            return []
        for r in rows:
            r = dict(r)
            vencido = r.get('proximo_calibracao', '') < hoje
            alertas.append({
                'tipo': 'amostrador_vencendo' if not vencido else 'amostrador_vencido',
                'severidade': 'critico' if vencido else 'alto',
                'descricao': (
                    f'Amostrador {r.get("codigo") or r["id"]} ({r.get("tipo") or "—"}) '
                    + ('com calibração VENCIDA!' if vencido
                       else f'vence em {r.get("proximo_calibracao")[:10]}.')
                ),
                'entidade_tipo': 'amostrador',
                'entidade_id': r['id'],
            })
    return alertas


def detectar_empresas_duplicadas() -> list:
    """Empresas com nomes similares (possíveis duplicatas)."""
    divs = []
    with get_db() as conn:
        try:
            rows = conn.execute(
                'SELECT id, nome FROM empresas ORDER BY nome LIMIT 500'
            ).fetchall()
        except Exception:
            return []

    def _norm(s: str) -> str:
        s = s.lower().strip()
        s = re.sub(r'\b(ltda|s\.?a\.?|eireli|me|epp|sa|s\/a|microempresa)\b', '', s)
        s = re.sub(r'[^\w\s]', ' ', s)
        return re.sub(r'\s+', ' ', s).strip()

    empresas = [(dict(r)['id'], dict(r)['nome'], _norm(dict(r)['nome'])) for r in rows]
    vistos: set = set()
    for i, (id1, n1, norm1) in enumerate(empresas):
        for id2, n2, norm2 in empresas[i+1:]:
            par = (min(id1, id2), max(id1, id2))
            if par in vistos:
                continue
            t1, t2 = set(norm1.split()), set(norm2.split())
            if not t1 or not t2:
                continue
            jac = len(t1 & t2) / len(t1 | t2)
            if jac >= 0.75:
                vistos.add(par)
                divs.append({
                    'tipo': 'empresa_duplicada', 'severidade': 'medio',
                    'descricao': (
                        f'Possível duplicata: "{n1}" (#{id1}) e "{n2}" (#{id2}) '
                        f'— similaridade {int(jac*100)}%.'
                    ),
                    'entidade_tipo': 'empresa',
                    'entidade_id': id1,
                })
    return divs


def detectar_coletas_sem_resultado(dias: int = 45) -> list:
    """Coletas enviadas ao lab há mais de N dias sem resultado registrado."""
    limite = (datetime.now() - timedelta(days=dias)).isoformat()[:10]
    alertas = []
    with get_db() as conn:
        for tbl in ('coletas_ruido', 'coletas_quimico'):
            try:
                rows = conn.execute(f'''
                    SELECT id, empresa_nome, data_coleta, os, status
                    FROM {tbl}
                    WHERE data_coleta < ?
                      AND (status IS NULL OR status NOT IN ('resultado_ok','concluido','cancelado'))
                    LIMIT 30
                ''', (limite,)).fetchall()
            except Exception:
                continue
            for r in rows:
                r = dict(r)
                alertas.append({
                    'tipo': 'coleta_sem_resultado', 'severidade': 'alto',
                    'descricao': (
                        f'{tbl.replace("coletas_","").capitalize()} — '
                        f'{r.get("empresa_nome") or "—"} '
                        f'(OS {r.get("os") or "—"}, {r.get("data_coleta") or "—"}) '
                        f'há mais de {dias} dias sem resultado.'
                    ),
                    'entidade_tipo': tbl,
                    'entidade_id': r['id'],
                })
    return alertas


def detectar_visitas_sem_coleta(dias: int = 7) -> list:
    """Visitas concluídas há mais de N dias sem coleta técnica registrada."""
    limite = (datetime.now() - timedelta(days=dias)).isoformat()[:10]
    alertas = []
    with get_db() as conn:
        try:
            rows = conn.execute('''
                SELECT vt.id, vt.data_visita, vt.tecnico, e.nome AS empresa_nome
                FROM visitas_tecnicas vt
                LEFT JOIN empresas e ON e.id = vt.empresa_id
                LEFT JOIN coletas_ruido cr ON cr.visita_id = vt.id
                LEFT JOIN coletas_quimico cq ON cq.visita_id = vt.id
                WHERE vt.resultado = 'concluido'
                  AND vt.data_visita < ?
                  AND cr.id IS NULL
                  AND cq.id IS NULL
                LIMIT 50
            ''', (limite,)).fetchall()
        except Exception:
            return []
        for r in rows:
            r = dict(r)
            alertas.append({
                'tipo': 'visita_sem_coleta', 'severidade': 'medio',
                'descricao': (
                    f'Visita em {r.get("empresa_nome") or "—"} '
                    f'({r.get("data_visita") or "—"}) concluída sem coleta técnica registrada.'
                ),
                'entidade_tipo': 'visita',
                'entidade_id': r['id'],
            })
    return alertas


# ════════════════════════════════════════════════════════════════════════
# 5. PERSISTÊNCIA NO BANCO
# ════════════════════════════════════════════════════════════════════════

def salvar_divergencias(divergencias: list) -> int:
    """Persiste divergências — evita duplicar se já existe igual aberta."""
    salvos = 0
    ph = _ph()
    with get_db() as conn:
        for d in divergencias:
            existing = conn.execute(
                f'SELECT id FROM divergencias WHERE tipo={ph} AND entidade_tipo={ph} '
                f'AND entidade_id={ph} AND status={ph}',
                (d['tipo'], d['entidade_tipo'], d['entidade_id'], 'aberta')
            ).fetchone()
            if existing:
                continue
            conn.execute(
                f'INSERT INTO divergencias (tipo, severidade, entidade_tipo, entidade_id, descricao) '
                f'VALUES ({ph},{ph},{ph},{ph},{ph})',
                (d['tipo'], d.get('severidade', 'medio'),
                 d['entidade_tipo'], d['entidade_id'], d['descricao'])
            )
            salvos += 1
    return salvos


def justificar_divergencia(div_id: int, motivo: str, descricao: str, tecnico: str) -> bool:
    ph = _ph()
    with get_db() as conn:
        conn.execute(
            f'INSERT INTO justificativas_operacionais '
            f'(divergencia_id, motivo, descricao, tecnico) VALUES ({ph},{ph},{ph},{ph})',
            (div_id, motivo, descricao, tecnico)
        )
        conn.execute(
            f'UPDATE divergencias SET status={ph}, resolvido_em=CURRENT_TIMESTAMP, '
            f'resolvido_por={ph} WHERE id={ph}',
            ('justificada', tecnico, div_id)
        )
    return True


def resolver_divergencia(div_id: int, tecnico: str) -> bool:
    ph = _ph()
    with get_db() as conn:
        conn.execute(
            f'UPDATE divergencias SET status={ph}, resolvido_em=CURRENT_TIMESTAMP, '
            f'resolvido_por={ph} WHERE id={ph}',
            ('resolvida', tecnico, div_id)
        )
    return True


# ════════════════════════════════════════════════════════════════════════
# 6. EXECUÇÃO GERAL
# ════════════════════════════════════════════════════════════════════════

def run_consistencia_geral() -> dict:
    """
    Executa todas as verificações automáticas e persiste resultados.
    Retorna resumo da execução.
    """
    ts = datetime.now().isoformat()
    resultado = {'ts': ts, 'divergencias_novas': 0, 'checks': {}}

    checks = [
        ('os_paradas',          lambda: detectar_os_paradas(15)),
        ('demandas_orfas',      detectar_demandas_orfas),
        ('amostradores',        detectar_amostradores_vencendo),
        ('coletas_sem_result',  lambda: detectar_coletas_sem_resultado(45)),
        ('visitas_sem_coleta',  lambda: detectar_visitas_sem_coleta(7)),
        ('empresas_dupl',       detectar_empresas_duplicadas),
    ]

    todos = []
    for nome, fn in checks:
        try:
            items = fn()
            resultado['checks'][nome] = len(items)
            todos.extend(items)
        except Exception as e:
            resultado['checks'][nome] = f'erro:{e}'

    resultado['divergencias_novas'] = salvar_divergencias(todos)

    # Registra execução nos eventos
    try:
        from .db import registrar_evento
        registrar_evento(
            'consistencia_check',
            f'Check automático: {resultado["divergencias_novas"]} divergências novas',
            None, 'sistema', 'sistema', None
        )
    except Exception:
        pass

    return resultado


# ════════════════════════════════════════════════════════════════════════
# 7. CONSULTAS
# ════════════════════════════════════════════════════════════════════════

def listar_divergencias(status: str = 'aberta', limit: int = 100,
                        tipo: str = None, severidade: str = None) -> list:
    ph = _ph()
    params = [status]
    where_extra = ''
    if tipo:
        where_extra += f' AND d.tipo={ph}'
        params.append(tipo)
    if severidade:
        where_extra += f' AND d.severidade={ph}'
        params.append(severidade)
    with get_db() as conn:
        rows = conn.execute(f'''
            SELECT d.*, j.motivo, j.descricao AS just_descricao, j.tecnico AS just_tecnico
            FROM divergencias d
            LEFT JOIN justificativas_operacionais j ON j.divergencia_id = d.id
            WHERE d.status={ph}{where_extra}
            ORDER BY
              CASE d.severidade
                WHEN 'critico' THEN 1 WHEN 'alto' THEN 2
                WHEN 'medio'   THEN 3 ELSE 4
              END,
              d.detectado_em DESC
            LIMIT {limit}
        ''', params).fetchall()
        return [dict(r) for r in rows]


def stats_consistencia() -> dict:
    with get_db() as conn:
        try:
            rows = conn.execute('''
                SELECT severidade, COUNT(*) as cnt
                FROM divergencias WHERE status='aberta'
                GROUP BY severidade
            ''').fetchall()
        except Exception:
            rows = []
        contagens = {r['severidade']: r['cnt'] for r in rows} if rows else {}
        total = sum(contagens.values())
        return {
            'critico': contagens.get('critico', 0),
            'alto':    contagens.get('alto', 0),
            'medio':   contagens.get('medio', 0),
            'baixo':   contagens.get('baixo', 0),
            'total':   total,
        }
