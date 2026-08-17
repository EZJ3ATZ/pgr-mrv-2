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


def _ph():
    # _PGCursor.execute() converte ? → %s automaticamente — usar sempre ?
    return '?'


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

def detectar_os_paradas(dias: int = 15) -> list:
    """Demandas ativas sem visita há mais de N dias."""
    limite = (datetime.now() - timedelta(days=dias)).isoformat()[:10]
    alertas = []
    with get_db() as conn:
        try:
            # HAVING não pode referenciar alias de SELECT no PostgreSQL — repete a
            # expressão MAX(); o filtro por criado_em é de linha, vai no WHERE.
            rows = conn.execute('''
                SELECT d.id, d.numero_os, e.nome AS empresa_nome,
                       MAX(vt.data_visita) AS ultima_visita
                FROM demandas d
                LEFT JOIN empresas e ON e.id = d.empresa_id
                LEFT JOIN planejamentos p ON p.demanda_id = d.id
                LEFT JOIN visitas_tecnicas vt ON vt.planejamento_id = p.id
                WHERE d.status NOT IN ('concluido','cancelado','removido')
                  AND (d.empresa_id IS NULL OR d.empresa_id > 0)
                  AND d.criado_em < ?
                GROUP BY d.id, d.numero_os, e.nome
                HAVING MAX(vt.data_visita) IS NULL OR MAX(vt.data_visita) < ?
                LIMIT 100
            ''', (limite, limite)).fetchall()
        except Exception as e:
            print(f'[consistencia] os_paradas falhou: {e}')
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
            # A calibração vence pela validade do certificado (cert_validade).
            # As colunas 'proximo_calibracao'/'ativo' nunca existiram no schema —
            # a query antiga falhava nos dois bancos e retornava [] silenciosamente.
            rows = conn.execute('''
                SELECT id, codigo, tipo, cert_validade AS proximo_calibracao
                FROM amostradores
                WHERE cert_validade IS NOT NULL AND cert_validade != ''
                  AND cert_validade <= ?
                  AND COALESCE(arquivado, 0) = 0
                ORDER BY cert_validade
                LIMIT 50
            ''', (limite,)).fetchall()
        except Exception as e:
            print(f'[consistencia] amostradores_vencendo falhou: {e}')
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
            # coletas_quimico não tem coluna 'os' (só coletas_ruido) — seleciona
            # NULL para manter o mesmo formato de linha nas duas tabelas.
            os_col = 'os' if tbl == 'coletas_ruido' else 'NULL AS os'
            try:
                rows = conn.execute(f'''
                    SELECT id, empresa_nome, data_coleta, {os_col}, status
                    FROM {tbl}
                    WHERE data_coleta < ?
                      AND (status IS NULL OR status NOT IN ('resultado_ok','concluido','cancelado'))
                    LIMIT 30
                ''', (limite,)).fetchall()
            except Exception as e:
                print(f'[consistencia] coletas_sem_resultado {tbl} falhou: {e}')
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
