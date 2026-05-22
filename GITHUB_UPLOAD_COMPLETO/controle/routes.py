# -*- coding: utf-8 -*-
"""Endpoints REST do modulo Controle de Medicoes e Amostradores."""
import io
import os
import re
import json
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file

from .db import (
    get_db, init_db, row_to_dict, list_amostradores, list_demandas,
    get_demanda_completa, upsert_empresa, stats_dashboard,
    registrar_sync, list_sync_log,
    list_demandas_por_empresa, get_empresa_demandas,
    list_amostradores_vencendo, contar_vencendo,
    mesclar_empresas_duplicatas
)
from .import_xlsx import importar_amostradores, importar_medicoes, importar_demandas_planner

controle_bp = Blueprint('controle', __name__, url_prefix='/controle')


# ── Dashboard ─────────────────────────────────────────────────────────
@controle_bp.route('/stats')
def stats():
    init_db()
    return jsonify(stats_dashboard())


# ── Importacao ────────────────────────────────────────────────────────
@controle_bp.route('/import/amostradores', methods=['POST'])
def import_amostr():
    init_db()
    f = request.files.get('file')
    user = request.form.get('user', 'Matheus')
    if not f:
        return jsonify({'erro': 'Nenhum arquivo'}), 400
    try:
        nome_arq = f.filename or 'arquivo.xlsx'
        res = importar_amostradores(f.read())
        registrar_sync('amostradores', nome_arq, res.get('inserted', 0), res.get('updated', 0), user)
        return jsonify({'ok': True, **res})
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'tb': traceback.format_exc()[:500]}), 500


@controle_bp.route('/import/medicoes', methods=['POST'])
def import_med():
    init_db()
    f = request.files.get('file')
    user = request.form.get('user', 'Matheus')
    if not f:
        return jsonify({'erro': 'Nenhum arquivo'}), 400
    try:
        nome_arq = f.filename or 'arquivo.xlsx'
        res = importar_medicoes(f.read())
        registrar_sync('medicoes', nome_arq, res.get('medicoes_inseridas', 0), 0, user)
        return jsonify({'ok': True, **res})
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'tb': traceback.format_exc()[:500]}), 500


@controle_bp.route('/import/planner', methods=['POST'])
def import_planner():
    """Importa export do Microsoft Planner (Demandas_Medicoes_*.xlsx)."""
    init_db()
    f = request.files.get('file')
    user = request.form.get('user', 'Matheus')
    if not f:
        return jsonify({'erro': 'Nenhum arquivo'}), 400
    try:
        nome_arq = f.filename or 'arquivo.xlsx'
        res = importar_demandas_planner(f.read())
        registrar_sync('planner', nome_arq, res.get('demandas_inseridas', 0), res.get('demandas_atualizadas', 0), user)
        return jsonify({'ok': True, **res})
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'tb': traceback.format_exc()[:500]}), 500


@controle_bp.route('/sync_log')
def get_sync_log():
    """Retorna historico das ultimas importacoes."""
    init_db()
    return jsonify(list_sync_log(limit=int(request.args.get('limit', 20))))


# ── Amostradores ──────────────────────────────────────────────────────
@controle_bp.route('/amostradores')
def get_amostradores():
    init_db()
    return jsonify(list_amostradores(request.args.to_dict()))


@controle_bp.route('/amostradores', methods=['POST'])
def cria_amostrador():
    init_db()
    d = request.json or {}
    if not d.get('codigo') or not d.get('tipo'):
        return jsonify({'erro': 'codigo e tipo obrigatorios'}), 400
    with get_db() as conn:
        cur = conn.execute("""
            INSERT INTO amostradores (codigo, tipo, status, data_entrada, observacao)
            VALUES (?, ?, ?, ?, ?)""",
            (d['codigo'], d['tipo'], d.get('status', 'Estoque'),
             d.get('data_entrada', datetime.now().strftime('%Y-%m-%d')),
             d.get('observacao', '')))
        return jsonify({'ok': True, 'id': cur.lastrowid})


@controle_bp.route('/amostradores/<int:aid>', methods=['PUT'])
def update_amostrador(aid):
    init_db()
    d = request.json or {}
    fields = []
    params = []
    for k in ('status', 'tipo', 'codigo', 'avaliador', 'data_medicao', 'observacao', 'data_entrada'):
        if k in d:
            fields.append(f'{k}=?'); params.append(d[k])
    if 'empresa' in d:
        emp_id = upsert_empresa('', d['empresa']) if d['empresa'] else None
        fields.append('empresa_id=?'); params.append(emp_id)
    if not fields:
        return jsonify({'erro': 'nada para atualizar'}), 400
    fields.append('atualizado_em=CURRENT_TIMESTAMP')
    params.append(aid)
    with get_db() as conn:
        conn.execute(f'UPDATE amostradores SET {",".join(fields)} WHERE id=?', params)
    return jsonify({'ok': True})


@controle_bp.route('/amostradores/<int:aid>', methods=['DELETE'])
def delete_amostrador(aid):
    init_db()
    with get_db() as conn:
        conn.execute('DELETE FROM amostradores WHERE id=?', (aid,))
    return jsonify({'ok': True})


# ── Manutencao / bulk updates ─────────────────────────────────────────
@controle_bp.route('/amostradores/fix_data_entrada', methods=['POST'])
def fix_data_entrada():
    """Atualiza data_entrada de amostradores com muitos dias parados."""
    init_db()
    d = request.json or {}
    nova_data  = d.get('data_entrada', '2025-10-23')  # default: 23/10/2025
    dias_min   = int(d.get('dias_min', 200))
    ids_manual = d.get('ids', [])  # lista de IDs especificos, opcional

    with get_db() as conn:
        if ids_manual:
            placeholders = ','.join('?' * len(ids_manual))
            cur = conn.execute(
                f"UPDATE amostradores SET data_entrada=?, atualizado_em=CURRENT_TIMESTAMP "
                f"WHERE id IN ({placeholders})",
                [nova_data] + ids_manual
            )
        else:
            cur = conn.execute(
                """UPDATE amostradores SET data_entrada=?, atualizado_em=CURRENT_TIMESTAMP
                   WHERE status IN ('Estoque','Reservado')
                     AND data_entrada IS NOT NULL
                     AND CAST(julianday('now') - julianday(data_entrada) AS INTEGER) > ?""",
                (nova_data, dias_min)
            )
        afetados = cur.rowcount
    return jsonify({'ok': True, 'afetados': afetados, 'nova_data': nova_data})


# ── Vencimento (laboratorio cobra apos N dias) ────────────────────────
@controle_bp.route('/amostradores_vencendo')
def get_vencendo():
    init_db()
    return jsonify({
        'stats': contar_vencendo(),
        'amostradores': list_amostradores_vencendo()
    })


@controle_bp.route('/amostradores/<int:aid>/envio_lab', methods=['POST'])
def marcar_envio_lab(aid):
    """Registra que o amostrador foi enviado ao laboratorio (inicia contagem)."""
    init_db()
    d = request.json or {}
    data_envio = d.get('data_envio_lab') or datetime.now().strftime('%Y-%m-%d')
    dias       = int(d.get('dias_validade', 45) or 45)
    lote       = d.get('lote', '')
    obs        = d.get('observacao_venc', '')
    with get_db() as conn:
        conn.execute("""
            UPDATE amostradores
            SET data_envio_lab=?, dias_validade=?, lote=?, observacao_venc=?,
                atualizado_em=CURRENT_TIMESTAMP
            WHERE id=?""",
            (data_envio, dias, lote, obs, aid))
    return jsonify({'ok': True})


@controle_bp.route('/amostradores/envio_lab_lote', methods=['POST'])
def marcar_envio_lab_lote():
    """Registra envio ao lab em lote (varios amostradores de uma vez)."""
    init_db()
    d = request.json or {}
    ids = d.get('ids', [])
    if not ids: return jsonify({'erro': 'sem ids'}), 400
    data_envio = d.get('data_envio_lab') or datetime.now().strftime('%Y-%m-%d')
    dias       = int(d.get('dias_validade', 45) or 45)
    lote       = d.get('lote', '')
    placeholders = ','.join(['?'] * len(ids))
    with get_db() as conn:
        conn.execute(f"""
            UPDATE amostradores
            SET data_envio_lab=?, dias_validade=?, lote=?,
                atualizado_em=CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})""",
            [data_envio, dias, lote] + ids)
    return jsonify({'ok': True, 'afetados': len(ids)})


# ── Demandas ──────────────────────────────────────────────────────────
@controle_bp.route('/demandas')
def get_demandas():
    init_db()
    return jsonify(list_demandas(request.args.to_dict()))


@controle_bp.route('/demandas_por_empresa')
def get_demandas_por_empresa():
    """Demandas agrupadas por empresa, com progresso total da empresa."""
    init_db()
    return jsonify(list_demandas_por_empresa(request.args.to_dict()))


@controle_bp.route('/empresa/<int:eid>/demandas')
def get_empresa(eid):
    init_db()
    d = get_empresa_demandas(eid)
    return (jsonify(d), 200) if d else (jsonify({'erro': 'nao encontrada'}), 404)


@controle_bp.route('/demanda/<int:did>/contato', methods=['POST'])
def marcar_contato(did):
    """Marca que o contato com cliente foi feito (SLA)."""
    init_db()
    d = request.json or {}
    feito = 1 if d.get('feito', True) else 0
    user = d.get('por', 'Matheus')
    with get_db() as conn:
        conn.execute("""
            UPDATE demandas SET contato_feito=?, contato_feito_em=CURRENT_TIMESTAMP,
                                contato_feito_por=?
            WHERE id=?""", (feito, user, did))
    return jsonify({'ok': True, 'contato_feito': bool(feito)})


@controle_bp.route('/empresa/<int:eid>/contato', methods=['POST'])
def marcar_contato_empresa(eid):
    """Marca contato feito em TODAS as demandas pendentes da empresa."""
    init_db()
    d = request.json or {}
    feito = 1 if d.get('feito', True) else 0
    user = d.get('por', 'Matheus')
    with get_db() as conn:
        cur = conn.execute("""
            UPDATE demandas SET contato_feito=?, contato_feito_em=CURRENT_TIMESTAMP,
                                contato_feito_por=?
            WHERE empresa_id=? AND status != 'concluida'""",
            (feito, user, eid))
        afetadas = cur.rowcount
    return jsonify({'ok': True, 'afetadas': afetadas})


@controle_bp.route('/demandas/<int:did>')
def get_demanda(did):
    init_db()
    d = get_demanda_completa(did)
    return (jsonify(d), 200) if d else (jsonify({'erro': 'nao encontrada'}), 404)


@controle_bp.route('/demandas', methods=['POST'])
def cria_demanda():
    init_db()
    d = request.json or {}
    empresa_id = upsert_empresa(d.get('cnpj', ''), d.get('empresa', ''),
                                cidade=d.get('cidade'), uf=d.get('uf'))
    if not empresa_id:
        return jsonify({'erro': 'empresa obrigatoria'}), 400
    with get_db() as conn:
        cur = conn.execute("""
            INSERT INTO demandas (numero_os, empresa_id, prazo, status, observacao)
            VALUES (?, ?, ?, ?, ?)""",
            (d.get('numero_os', ''), empresa_id, d.get('prazo'),
             d.get('status', 'pendente'), d.get('observacao', '')))
        did = cur.lastrowid
        # Medicoes
        for m in d.get('medicoes', []):
            conn.execute("""
                INSERT INTO medicoes
                    (demanda_id, agente, tipo_amostrador, qtd_pontos_prevista,
                     necessita_laudo, observacao)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (did, m.get('agente', ''), m.get('tipo_amostrador', ''),
                 m.get('qtd_pontos_prevista', 1),
                 m.get('necessita_laudo', ''), m.get('observacao', '')))
        return jsonify({'ok': True, 'id': did})


# ── Baixa de amostrador ───────────────────────────────────────────────
@controle_bp.route('/baixa', methods=['POST'])
def dar_baixa():
    """Da baixa em uma medicao usando um amostrador.
    Atualiza: amostrador.status, medicao.status/qtd_feita, demanda.status,
    e registra historico em baixas.
    """
    init_db()
    d = request.json or {}
    medicao_id    = d.get('medicao_id')
    amostrador_id = d.get('amostrador_id')
    if not medicao_id or not amostrador_id:
        return jsonify({'erro': 'medicao_id e amostrador_id obrigatorios'}), 400

    avaliador        = d.get('avaliador', '')
    bomba            = d.get('bomba', '')
    vazao_calibrada  = float(d.get('vazao_calibrada', 0) or 0)
    vol_recomendado  = float(d.get('volume_recomendado', 0) or 0)
    data_medicao     = d.get('data_medicao', datetime.now().strftime('%Y-%m-%d'))
    obs              = d.get('observacao', '')

    # Calcular tempos a partir da faixa de vazao recomendada (se enviada)
    vazao_min = float(d.get('vazao_min', 0) or 0)
    vazao_max = float(d.get('vazao_max', 0) or 0)
    vol_min   = float(d.get('volume_min', 0) or 0)
    vol_max   = float(d.get('volume_max', 0) or vol_recomendado)
    tempo_min = (vol_min / vazao_calibrada) if (vazao_calibrada > 0 and vol_min > 0) else 0
    tempo_max = (vol_max / vazao_calibrada) if (vazao_calibrada > 0 and vol_max > 0) else 0

    avisos = []
    if vazao_calibrada > 0:
        if vazao_min > 0 and vazao_calibrada < vazao_min:
            avisos.append(f'Vazao calibrada ({vazao_calibrada}) abaixo do minimo do metodo ({vazao_min})')
        if vazao_max > 0 and vazao_calibrada > vazao_max:
            avisos.append(f'Vazao calibrada ({vazao_calibrada}) acima do maximo do metodo ({vazao_max})')

    with get_db() as conn:
        am = conn.execute('SELECT * FROM amostradores WHERE id=?', (amostrador_id,)).fetchone()
        me = conn.execute('SELECT * FROM medicoes WHERE id=?', (medicao_id,)).fetchone()
        if not am: return jsonify({'erro': 'amostrador nao encontrado'}), 404
        if not me: return jsonify({'erro': 'medicao nao encontrada'}), 404

        # Registrar baixa
        conn.execute("""
            INSERT INTO baixas
                (medicao_id, amostrador_id, avaliador, bomba, vazao_calibrada,
                 volume_recomendado, tempo_calculado_min, tempo_calculado_max,
                 data_medicao, observacao)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (medicao_id, amostrador_id, avaliador, bomba, vazao_calibrada,
             vol_recomendado, tempo_min, tempo_max, data_medicao, obs))

        # Buscar empresa da demanda para vincular ao amostrador
        empresa_id = conn.execute(
            'SELECT empresa_id FROM demandas WHERE id=?',
            (me['demanda_id'],)).fetchone()['empresa_id']

        # Atualizar amostrador: muda status, vincula empresa, avaliador e data
        conn.execute("""
            UPDATE amostradores
            SET status='Laboratorio', empresa_id=?, avaliador=?, data_medicao=?,
                atualizado_em=CURRENT_TIMESTAMP
            WHERE id=?""",
            (empresa_id, avaliador, data_medicao, amostrador_id))

        # Incrementar pontos realizados da medicao
        nova_qtd = (me['qtd_pontos_feita'] or 0) + 1
        novo_status = 'realizado' if nova_qtd >= (me['qtd_pontos_prevista'] or 1) else 'parcial'
        conn.execute("""
            UPDATE medicoes
            SET qtd_pontos_feita=?, status=?
            WHERE id=?""",
            (nova_qtd, novo_status, medicao_id))

        # Atualizar status da demanda se todas medicoes realizadas
        dem_id = me['demanda_id']
        pend = conn.execute(
            "SELECT COUNT(*) c FROM medicoes WHERE demanda_id=? AND status!='realizado'",
            (dem_id,)).fetchone()['c']
        if pend == 0:
            conn.execute("UPDATE demandas SET status='concluida' WHERE id=?", (dem_id,))

    return jsonify({
        'ok': True,
        'tempo_min': round(tempo_min, 2),
        'tempo_max': round(tempo_max, 2),
        'avisos': avisos
    })


# ── Empresas ──────────────────────────────────────────────────────────
@controle_bp.route('/empresas')
def get_empresas():
    """Lista/busca empresas. Param ?q= filtra por nome ou CNPJ."""
    init_db()
    q = request.args.get('q', '').strip()
    sql = "SELECT id, nome, cnpj, contato, telefone, email, cidade, uf FROM empresas WHERE 1=1"
    params = []
    if q:
        sql += " AND (nome LIKE ? OR cnpj LIKE ?)"
        params.extend([f'%{q}%', f'%{q}%'])
    sql += " ORDER BY nome LIMIT 100"
    with get_db() as conn:
        return jsonify([row_to_dict(r) for r in conn.execute(sql, params).fetchall()])


@controle_bp.route('/empresas', methods=['POST'])
def cria_empresa():
    init_db()
    d = request.json or {}
    nome = (d.get('nome') or '').strip()
    cnpj = (d.get('cnpj') or '').strip()
    if not nome:
        return jsonify({'erro': 'nome obrigatorio'}), 400
    # Verificar duplicacao por nome ou CNPJ
    with get_db() as conn:
        if cnpj:
            dup = conn.execute(
                'SELECT id, nome FROM empresas WHERE cnpj=?', (cnpj,)).fetchone()
            if dup:
                return jsonify({'erro': f'CNPJ ja cadastrado em "{dup["nome"]}"', 'id_existente': dup['id']}), 409
        # Nome parecido
        similar = conn.execute(
            'SELECT id, nome FROM empresas WHERE LOWER(nome) = LOWER(?)',
            (nome,)).fetchone()
        if similar:
            return jsonify({'erro': f'Nome ja cadastrado: "{similar["nome"]}"', 'id_existente': similar['id']}), 409
    eid = upsert_empresa(cnpj, nome,
        contato=d.get('contato'), telefone=d.get('telefone'),
        email=d.get('email'), cidade=d.get('cidade'), uf=d.get('uf'))
    return jsonify({'ok': True, 'id': eid})


# ── Estoque de amostradores por agente ────────────────────────────────

# Tipos de amostrador que a empresa NÃO utiliza (não aparecem na previsão)
TIPOS_IGNORADOS_PREVISAO = {'FMD', 'OVM', 'ICR', 'ASL', 'IEC'}

# Aliases de agentes → nome canônico no guia (para lookup exato)
AGENTE_ALIASES = {
    'BTX': 'BENZENO',
    'BTE': 'BENZENO',
    'BTXE': 'BENZENO',
    'BENZENO, TOLUENO E XILENO': 'BENZENO',
    'BENZENO, TOLUENO, XILENO': 'BENZENO',
    'BENZENO, TOLUENO, ETILBENZENO': 'BENZENO',
    'ARSÊNIO': 'ARSÊNIO E COMPOSTOS INORGÂNICOS',
    'ARSENIO': 'ARSÊNIO E COMPOSTOS INORGÂNICOS',
    'ARSÊNIO E COMPOSTOS INORGÂNICOS, COM AS': 'ARSÊNIO E COMPOSTOS INORGÂNICOS',
    'HEXANO, OUTROS ISÔMEROS': 'HEXANO, OUTROS ISÔMEROS QUE NÃO O N-HEXANO',
    'HEXANO, OUTROS ISOMEROS': 'HEXANO, OUTROS ISÔMEROS QUE NÃO O N-HEXANO',
    'HEXANO OUTROS ISÔMEROS': 'HEXANO, OUTROS ISÔMEROS QUE NÃO O N-HEXANO',
}


def _extrair_tipos_amostrador(amostrador_cod):
    """Extrai siglas de tipo (TCP, TAS, IOL, etc) de strings como
       'SKC 226-01 (TCP*****)' ou 'IOL E X2P' ou 'TCG E TCP'.
    """
    if not amostrador_cod:
        return []
    tipos = set()
    s = amostrador_cod.upper()
    # 1) Codigos entre parenteses com asteriscos: (TCP*****)
    for m in re.findall(r'\(([A-Z][A-Z0-9]+)\*+\)', s):
        tipos.add(m)
    # 2) Siglas isoladas separadas por ' E ', ',', '/'
    if not tipos:
        for parte in re.split(r'\s+E\s+|\s*,\s*|\s*/\s*', s):
            parte = parte.strip()
            if re.fullmatch(r'[A-Z][A-Z0-9]{1,4}', parte):
                tipos.add(parte)
    return list(tipos)


def _buscar_metodos_agente(nome_agente):
    """Busca metodos do agente no guia_metodos.json. Aceita nome ou CAS."""
    try:
        guia_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  '..', 'guia_metodos.json')
        import json
        with open(guia_path, 'r', encoding='utf-8') as f:
            guia = json.load(f)
    except Exception as e:
        print(f'[controle] erro carregando guia: {e}')
        return []
    chave = (nome_agente or '').strip()
    if not chave:
        return []
    # Resolver alias primeiro
    alias = AGENTE_ALIASES.get(chave.upper())
    if alias:
        chave = alias
    # Tentar como CAS
    if chave in guia.get('by_cas', {}):
        return guia['by_cas'][chave]
    # Tentar como nome (uppercase)
    key_upper = chave.upper()
    if key_upper in guia.get('by_name', {}):
        cas = guia['by_name'][key_upper]
        return guia.get('by_cas', {}).get(cas, [])

    # Normaliza espaço, newlines e vírgulas para comparação fuzzy
    def _norm(s):
        s = re.sub(r',\s*', ' ', s)  # vírgulas → espaço
        return re.sub(r'[\s]+', ' ', s.strip())

    key_norm = _norm(key_upper)
    # Busca parcial normalizada — evita matches espúrios em substrings curtas
    best = None
    for nome_upper, cas in guia.get('by_name', {}).items():
        nome_norm = _norm(nome_upper)
        if key_norm in nome_norm:
            if best is None or len(nome_norm) < len(best[0]):
                best = (nome_norm, cas)
    if best:
        return guia.get('by_cas', {}).get(best[1], [])
    # Busca inversa normalizada: nome do guia dentro da chave
    for nome_upper, cas in guia.get('by_name', {}).items():
        nome_norm = _norm(nome_upper)
        if len(nome_norm) > 6 and nome_norm in key_norm:
            return guia.get('by_cas', {}).get(cas, [])
    return []


@controle_bp.route('/agente/<path:nome>/estoque')
def estoque_para_agente(nome):
    """Para um agente, retorna:
      - metodos cadastrados (do guia)
      - tipos de amostrador compativeis
      - quantos amostradores em estoque por tipo
      - lista de codigos disponiveis
    """
    init_db()
    metodos = _buscar_metodos_agente(nome)
    if not metodos:
        return jsonify({
            'agente': nome,
            'metodos': [],
            'tipos_compativeis': [],
            'total_disponivel': 0,
            'por_tipo': {},
            'amostradores': [],
            'aviso': 'Agente nao encontrado na guia de metodos'
        })

    tipos_set = set()
    metodos_resumo = []
    for m in metodos:
        tipos_m = _extrair_tipos_amostrador(m.get('amostradorCod', ''))
        tipos_set.update(tipos_m)
        metodos_resumo.append({
            'metodoCod': m.get('metodoCod', ''),
            'vazao': m.get('vazao', ''),
            'volume': m.get('volume', ''),
            'amostradorCod': m.get('amostradorCod', ''),
            'amostradorDesc': m.get('amostradorDesc', ''),
            'tipos': tipos_m,
        })
    tipos = list(tipos_set)

    por_tipo = {}
    amostr = []
    if tipos:
        placeholders = ','.join(['?'] * len(tipos))
        with get_db() as conn:
            rows = conn.execute(f"""
                SELECT a.id, a.codigo, a.tipo, a.status, a.data_entrada,
                       e.nome AS empresa_nome
                FROM amostradores a
                LEFT JOIN empresas e ON e.id = a.empresa_id
                WHERE a.tipo IN ({placeholders})
                ORDER BY
                  CASE WHEN a.status='Estoque' THEN 0 ELSE 1 END,
                  a.tipo, a.codigo
                LIMIT 500
            """, tipos).fetchall()
            for r in rows:
                d = row_to_dict(r)
                amostr.append(d)
                t = d['tipo']
                por_tipo.setdefault(t, {'estoque': 0, 'lab': 0, 'reservado': 0, 'total': 0})
                por_tipo[t]['total'] += 1
                st = (d.get('status') or '').lower()
                if 'estoque' in st: por_tipo[t]['estoque'] += 1
                elif 'lab' in st: por_tipo[t]['lab'] += 1
                elif 'reserv' in st: por_tipo[t]['reservado'] += 1

    total_disponivel = sum(v['estoque'] for v in por_tipo.values())

    return jsonify({
        'agente': nome,
        'metodos': metodos_resumo,
        'tipos_compativeis': tipos,
        'total_disponivel': total_disponivel,
        'por_tipo': por_tipo,
        'amostradores': amostr,
    })


# ── PDF de campo (kit de coleta) ──────────────────────────────────────
@controle_bp.route('/cadeia_pdf', methods=['POST'])
def gerar_cadeia_pdf():
    """Gera PDF com lista de medicoes a fazer em campo.
    Payload: {
      empresa: { nome, cnpj, contato },
      data_medicao: 'dd/mm/aaaa',
      avaliador: str,
      itens: [{ agente, bomba_modelo, bomba_sn, amostrador_tipo, amostrador_codigo,
                vazao_calibrada, tempo_min, tempo_max, observacao }]
    }
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
    except ImportError:
        return jsonify({'erro': 'reportlab nao instalado'}), 500

    d = request.json or {}
    empresa = d.get('empresa', {})
    data_med = d.get('data_medicao', datetime.now().strftime('%d/%m/%Y'))
    avaliador = d.get('avaliador', '')
    itens = d.get('itens', [])
    if not itens:
        return jsonify({'erro': 'sem itens'}), 400

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm)
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle('h1', parent=styles['Title'], fontSize=14, alignment=TA_CENTER, spaceAfter=6)
    h2 = ParagraphStyle('h2', parent=styles['Heading2'], fontSize=10, spaceAfter=4)
    body = ParagraphStyle('body', parent=styles['BodyText'], fontSize=9)
    elements = []

    elements.append(Paragraph(f'<b>FICHA DE CAMPO — COLETA AMBIENTAL</b>', h1))
    elements.append(Paragraph(f'<b>Empresa:</b> {empresa.get("nome","-")}', body))
    if empresa.get('cnpj'):
        elements.append(Paragraph(f'<b>CNPJ:</b> {empresa["cnpj"]}', body))
    if empresa.get('contato'):
        elements.append(Paragraph(f'<b>Contato:</b> {empresa["contato"]}', body))
    elements.append(Paragraph(f'<b>Data da medição:</b> {data_med}    <b>Avaliador:</b> {avaliador}', body))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(f'<b>Total de pontos:</b> {len(itens)}', body))
    elements.append(Spacer(1, 8))

    # Tabela com Paragraph para word-wrap em colunas largas
    cell_style = ParagraphStyle('cell', parent=body, fontSize=8, leading=10)
    cell_bold  = ParagraphStyle('cellb', parent=cell_style, fontName='Helvetica-Bold')
    def P(txt):
        return Paragraph(str(txt) if txt is not None else '—', cell_style)

    head = ['#', 'Agente', 'Amostrador', 'Bomba (S/N)', 'Vazão\n(L/min)', 'Tempo\n(min)', 'Função/Setor']
    data = [head]
    for i, it in enumerate(itens, 1):
        bomba = f'{it.get("bomba_modelo","")} {it.get("bomba_sn","")}'.strip() or '—'
        amostr = f'{it.get("amostrador_tipo","")} {it.get("amostrador_codigo","")}'.strip() or '—'
        tempo = ''
        if it.get('tempo_min') and it.get('tempo_max'):
            tempo = f'{it["tempo_min"]:.0f}–{it["tempo_max"]:.0f}'
        elif it.get('tempo_min'):
            tempo = f'{it["tempo_min"]:.0f}'
        data.append([
            str(i),
            P(it.get('agente','—')),         # word-wrap
            P(amostr),
            P(bomba),
            str(it.get('vazao_calibrada','—')),
            tempo,
            P(it.get('funcao','') or it.get('observacao','')),
        ])

    # Larguras ajustadas: agente e função maiores, vazão/tempo menores
    tbl = Table(data, repeatRows=1,
        colWidths=[0.7*cm, 4.5*cm, 2.8*cm, 3.2*cm, 1.7*cm, 1.6*cm, 3.5*cm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#2E75B6')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 8),
        ('FONTSIZE', (0,1), (-1,-1), 8),
        ('GRID', (0,0), (-1,-1), 0.4, colors.grey),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN', (0,0), (0,-1), 'CENTER'),
        ('ALIGN', (4,0), (5,-1), 'CENTER'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F5F5F5')]),
    ]))
    elements.append(tbl)
    elements.append(Spacer(1, 14))

    # Checklist conferencia
    elements.append(Paragraph('<b>Conferência antes da coleta:</b>', h2))
    elements.append(Paragraph('☐ Todas as bombas calibradas e com bateria carregada', body))
    elements.append(Paragraph('☐ Amostradores rotulados com numero do filtro', body))
    elements.append(Paragraph('☐ Cronometro/relogio ajustado', body))
    elements.append(Paragraph('☐ EPI completo (luvas, oculos, mascara)', body))
    elements.append(Paragraph('☐ Cadeia de custodia preenchida', body))
    elements.append(Spacer(1, 24))
    elements.append(Paragraph('<i>Assinatura do avaliador:</i> _____________________________________', body))

    doc.build(elements)
    buf.seek(0)
    nome_safe = re.sub(r'[^\w-]', '_', empresa.get('nome', 'empresa'))[:40]
    return send_file(buf, as_attachment=True,
        download_name=f'ficha_campo_{nome_safe}_{data_med.replace("/","-")}.pdf',
        mimetype='application/pdf')


# ── Bombas disponiveis (reusa cadastro do quimico) ────────────────────
@controle_bp.route('/bombas')
def get_bombas():
    """Retorna lista de bombas + numeros de serie cadastrados."""
    try:
        # Importar do app principal sem causar import circular
        import sys
        app_mod = sys.modules.get('app')
        if app_mod and hasattr(app_mod, '_PUMP_SN') and hasattr(app_mod, '_PUMP_NAMES'):
            return jsonify({
                'modelos': [
                    {'id': k, 'nome': app_mod._PUMP_NAMES.get(k, k),
                     'sns': app_mod._PUMP_SN.get(k, [])}
                    for k in app_mod._PUMP_SN
                ]
            })
    except Exception as e:
        print(f'[controle] /bombas erro: {e}')
    # Fallback hardcoded
    return jsonify({
        'modelos': [
            {'id': 'airlite', 'nome': 'AIRLITE – SKC', 'sns': ['A060502', 'A061553', 'A061585', 'A062462', 'A63555']},
            {'id': 'bdx',     'nome': 'BDX II – GILLIAN', 'sns': ['38356', '38357', '38358', '38359']},
            {'id': 'turam',   'nome': 'FORMIS – TURAM', 'sns': ['2420120549', '2420120550', '2420120551']},
            {'id': 'inlite',  'nome': 'INLITE VENTUSPRO', 'sns': ['25040902602B', '25040903102B', '25040907102B']},
        ]
    })


# ── Previsão de estoque baseada em demandas pendentes ────────────────
@controle_bp.route('/previsao_estoque')
def previsao_estoque():
    """Cruza demandas pendentes × guia de métodos × estoque atual.
    Retorna para cada tipo de amostrador: qtd necessária, em estoque e falta.
    """
    init_db()
    with get_db() as conn:
        meds = [row_to_dict(r) for r in conn.execute("""
            SELECT m.id, m.agente, m.tipo_amostrador,
                   m.qtd_pontos_prevista, m.qtd_pontos_feita, m.status,
                   d.numero_os, d.prazo, d.empresa_id,
                   e.nome AS empresa_nome
            FROM medicoes m
            JOIN demandas d ON d.id = m.demanda_id
            JOIN empresas e ON e.id = d.empresa_id
            WHERE m.status != 'realizado'
              AND d.status != 'concluida'
            ORDER BY d.prazo ASC NULLS LAST
        """).fetchall()]

    AGENTES_FISICOS = {
        'Ruído', 'Ruido', 'Calor', 'Frio', 'Iluminação', 'Iluminacao',
        'Radiação Ionizante', 'Radiacao Ionizante', 'Radiação Não Ionizante',
        'Vibração Localizada - Mãos e Braços', 'Vibracao Localizada - Maos e Bracos',
        'Vibração de Corpo Inteiro', 'Vibracao de Corpo Inteiro',
        'Pressão Hiperbárica', 'Pressao Hiperbarica',
    }

    necessidades = {}   # tipo -> {qtd_necessaria, falta, medicoes[]}
    agentes_sem_guia = set()
    agentes_fisicos_presentes = set()

    for m in meds:
        agente = m.get('agente', '')
        pontos_faltam = max(0, (m.get('qtd_pontos_prevista') or 1) -
                                (m.get('qtd_pontos_feita') or 0))
        if pontos_faltam == 0:
            continue

        metodos = _buscar_metodos_agente(agente)
        if not metodos:
            if agente in AGENTES_FISICOS or any(f in agente for f in ['Ruído','Ruido','Calor','Vibração','Vibracao','Frio','Radiação','Radiacao','Iluminação']):
                agentes_fisicos_presentes.add(agente)
                continue
            agentes_sem_guia.add(agente)
            # Tenta usar tipo_amostrador ja cadastrado na medicao
            tipo_raw = (m.get('tipo_amostrador') or '').upper().strip()
            if tipo_raw:
                necessidades.setdefault(tipo_raw, {
                    'qtd_necessaria': 0, 'em_estoque': 0, 'falta': 0,
                    'medicoes': [], 'metodo': '(tipo da planilha)',
                    'vazao': '', 'volume': ''
                })
                necessidades[tipo_raw]['qtd_necessaria'] += pontos_faltam
                necessidades[tipo_raw]['medicoes'].append({
                    'empresa': m['empresa_nome'], 'os': m['numero_os'],
                    'agente': agente, 'prazo': m['prazo'], 'pontos': pontos_faltam
                })
            continue

        tipos_vistos = set()
        for met in metodos:
            tipos = _extrair_tipos_amostrador(met.get('amostradorCod', ''))
            for t in tipos:
                if t in tipos_vistos:
                    continue
                if t in TIPOS_IGNORADOS_PREVISAO:
                    continue  # empresa não usa este tipo
                tipos_vistos.add(t)
                necessidades.setdefault(t, {
                    'qtd_necessaria': 0, 'em_estoque': 0, 'falta': 0,
                    'medicoes': [],
                    'metodo': met.get('metodoCod', ''),
                    'vazao': met.get('vazao', ''),
                    'volume': met.get('volume', ''),
                })
                necessidades[t]['qtd_necessaria'] += pontos_faltam
                necessidades[t]['medicoes'].append({
                    'empresa': m['empresa_nome'], 'os': m['numero_os'],
                    'agente': agente, 'prazo': m['prazo'], 'pontos': pontos_faltam
                })

    # Contar estoque atual por tipo
    with get_db() as conn:
        for tipo, dados in necessidades.items():
            r = conn.execute(
                "SELECT COUNT(*) c FROM amostradores WHERE tipo=? AND status='Estoque'",
                (tipo,)).fetchone()
            dados['em_estoque'] = r['c'] if r else 0
            dados['falta'] = max(0, dados['qtd_necessaria'] - dados['em_estoque'])

    # Ordenar: mais crítico primeiro (falta > 0, depois por qtd necessaria)
    lista = sorted(
        [{'tipo': t, **v} for t, v in necessidades.items()],
        key=lambda x: (-x['falta'], -x['qtd_necessaria'])
    )

    return jsonify({
        'necessidades': lista,
        'agentes_sem_guia': sorted(agentes_sem_guia),
        'agentes_fisicos': sorted(agentes_fisicos_presentes),
        'total_medicoes_pendentes': len(meds),
    })


# ── Calculo de tempo (preview, sem persistir) ─────────────────────────
@controle_bp.route('/calc_tempo')
def calc_tempo():
    try:
        vol     = float(request.args.get('volume', 0))
        vazao   = float(request.args.get('vazao', 0))
        if vazao <= 0 or vol <= 0:
            return jsonify({'erro': 'volume e vazao > 0'}), 400
        tempo = vol / vazao
        return jsonify({'tempo_min': round(tempo, 2)})
    except (ValueError, TypeError):
        return jsonify({'erro': 'valores numericos invalidos'}), 400


# ── Devolucao de amostrador ao laboratorio ────────────────────────────
@controle_bp.route('/amostradores/<int:aid>/devolver', methods=['POST'])
def devolver_amostrador(aid):
    """Marca amostrador como Devolvido (sai da contagem de vencimento)."""
    init_db()
    d = request.json or {}
    obs = d.get('observacao', '')
    data_dev = d.get('data_devolucao') or datetime.now().strftime('%Y-%m-%d')
    with get_db() as conn:
        am = conn.execute('SELECT * FROM amostradores WHERE id=?', (aid,)).fetchone()
        if not am:
            return jsonify({'erro': 'nao encontrado'}), 404
        conn.execute("""
            UPDATE amostradores
            SET status='Devolvido',
                observacao = CASE WHEN ? != '' THEN ? ELSE observacao END,
                atualizado_em=CURRENT_TIMESTAMP
            WHERE id=?""",
            (obs, obs, aid))
    return jsonify({'ok': True, 'data_devolucao': data_dev})


@controle_bp.route('/amostradores/devolver_lote', methods=['POST'])
def devolver_lote():
    """Marca vários amostradores como Devolvidos de uma vez."""
    init_db()
    d = request.json or {}
    ids = d.get('ids', [])
    if not ids:
        return jsonify({'erro': 'sem ids'}), 400
    obs = d.get('observacao', 'Devolvido em lote')
    placeholders = ','.join(['?'] * len(ids))
    with get_db() as conn:
        cur = conn.execute(f"""
            UPDATE amostradores
            SET status='Devolvido', observacao=?,
                atualizado_em=CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})""",
            [obs] + ids)
    return jsonify({'ok': True, 'afetados': cur.rowcount})


# ── Utilitários de limpeza ────────────────────────────────────────────
@controle_bp.route('/empresas/mesclar_duplicatas', methods=['POST'])
def mesclar_duplicatas():
    """Consolida empresas com mesmo nome em um único registro."""
    init_db()
    mescladas = mesclar_empresas_duplicatas()
    return jsonify({'ok': True, 'mescladas': mescladas})


# ── Reset (cuidado!) ──────────────────────────────────────────────────
@controle_bp.route('/reset', methods=['POST'])
def reset_db():
    """Apaga todos os dados. Requer header X-Confirm: reset."""
    if request.headers.get('X-Confirm') != 'reset':
        return jsonify({'erro': 'requer header X-Confirm: reset'}), 400
    with get_db() as conn:
        for t in ('baixas', 'medicoes', 'demandas', 'amostradores', 'empresas'):
            conn.execute(f'DELETE FROM {t}')
    return jsonify({'ok': True})


# ── Export Excel ──────────────────────────────────────────────────────
@controle_bp.route('/export/amostradores')
def export_amostradores():
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Amostradores'
    ws.append(['Status', 'Tipo', 'Codigo', 'Data Entrada', 'Empresa', 'Avaliador', 'Data Medicao'])
    for a in list_amostradores():
        ws.append([a.get('status',''), a.get('tipo',''), a.get('codigo',''),
                   a.get('data_entrada',''), a.get('empresa_nome',''),
                   a.get('avaliador',''), a.get('data_medicao','')])
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f'amostradores_{datetime.now().strftime("%Y%m%d")}.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
