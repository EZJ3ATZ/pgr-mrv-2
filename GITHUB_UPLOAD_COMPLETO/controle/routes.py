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
    mesclar_empresas_duplicatas,
    list_raw_tasks, stats_raw_pipeline,
    list_operational_demands, list_operational_por_empresa,
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


@controle_bp.route('/agentes')
def get_agentes():
    """Retorna todos os agentes do guia_metodos.json."""
    import json
    try:
        guia_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  '..', 'guia_metodos.json')
        with open(guia_path, 'r', encoding='utf-8') as f:
            guia = json.load(f)
        by_cas = guia.get('by_cas', {})
        agentes = []
        seen = set()
        for cas, entry in by_cas.items():
            if isinstance(entry, list):
                entry = entry[0]
            nome = (entry.get('nome') or '').strip()
            if not nome or nome in seen:
                continue
            seen.add(nome)
            # tipo do amostrador extraido do amostradorCod ex: "SKC 226-01 (TCP*****)"
            tipo_amostrador = ''
            cod = entry.get('amostradorCod', '')
            if '(' in cod and ')' in cod:
                tipo_amostrador = cod[cod.index('(')+1:cod.index(')')].replace('*','').strip()
            agentes.append({
                'nome': nome,
                'cas': entry.get('cas', cas),
                'metodo': entry.get('metodoCod', ''),
                'metodo_desc': entry.get('metodoDesc', ''),
                'vazao': entry.get('vazao', ''),
                'volume': entry.get('volume', ''),
                'amostrador': entry.get('amostradorCod', ''),
                'amostrador_desc': entry.get('amostradorDesc', ''),
                'tipo_amostrador': tipo_amostrador,
                'unidade': entry.get('unidade', ''),
                'cuidados': entry.get('cuidados', ''),
            })
        # Adicionar grupos BTX e BTXE (nao existem individualmente no guia)
        grupos = [
            {
                'nome': 'BTX (Benzeno + Tolueno + Xileno)',
                'cas': '71-43-2 / 108-88-3 / 1330-20-7',
                'metodo': 'NIOSH 1501',
                'metodo_desc': 'CROMATOGRAFIA DE GASES COM DETECTOR DE IONIZACAO DE CHAMA',
                'vazao': '0,02 A 0,2 L/MIN',
                'volume': '2 A 10 L',
                'amostrador': 'SKC 226-01 (TCP*****)',
                'amostrador_desc': 'TUBO DE CARVAO ATIVADO COCONUT SHELL CHARCOAL, 6X70 mm, 2 SECOES DE 50/100 mg',
                'tipo_amostrador': 'TCP',
                'unidade': 'ppm',
                'cuidados': 'TRANSPORTE DE ROTINA. NAO NECESSITA REFRIGERACAO.',
            },
            {
                'nome': 'BTXE (Benzeno + Tolueno + Xileno + Etilbenzeno)',
                'cas': '71-43-2 / 108-88-3 / 1330-20-7 / 100-41-4',
                'metodo': 'NIOSH 1501',
                'metodo_desc': 'CROMATOGRAFIA DE GASES COM DETECTOR DE IONIZACAO DE CHAMA',
                'vazao': '0,02 A 0,2 L/MIN',
                'volume': '2 A 10 L',
                'amostrador': 'SKC 226-01 (TCP*****)',
                'amostrador_desc': 'TUBO DE CARVAO ATIVADO COCONUT SHELL CHARCOAL, 6X70 mm, 2 SECOES DE 50/100 mg',
                'tipo_amostrador': 'TCP',
                'unidade': 'ppm',
                'cuidados': 'TRANSPORTE DE ROTINA. NAO NECESSITA REFRIGERACAO.',
            },
        ]
        agentes = grupos + agentes
        return jsonify({'agentes': agentes, 'total': len(agentes)})
    except Exception as e:
        return jsonify({'erro': str(e), 'agentes': []}), 500


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


@controle_bp.route('/demandas/<int:did>/agentes')
def get_demanda_agentes(did):
    """Retorna agentes de medição extraídos da descrição da OS."""
    try:
        from .parser_agentes import extrair_agentes, resumo_agentes
        init_db()
        with get_db() as conn:
            row = conn.execute('SELECT descricao FROM demandas WHERE id=?', (did,)).fetchone()
        if not row:
            return jsonify({'erro': 'nao encontrada'}), 404
        agentes = extrair_agentes(row['descricao'] or '')
        return jsonify({'agentes': agentes, 'resumo': resumo_agentes(agentes)})
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'trace': traceback.format_exc()}), 500


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
    agente_avulso = (d.get('agente_avulso') or '').strip()
    demanda_id_avulso = d.get('demanda_id')

    if not amostrador_id:
        return jsonify({'erro': 'amostrador_id obrigatorio'}), 400

    # Modo avulso: cria medicao on-the-fly quando não tem medicao pré-cadastrada
    if not medicao_id and agente_avulso:
        if not demanda_id_avulso:
            return jsonify({'erro': 'demanda_id obrigatorio para entrada avulsa'}), 400
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO medicoes (demanda_id, agente, qtd_pontos_prevista, qtd_pontos_feita, status)
                   VALUES (?, ?, 1, 0, 'pendente')""",
                (demanda_id_avulso, agente_avulso))
            medicao_id = cur.lastrowid

    if not medicao_id:
        return jsonify({'erro': 'medicao_id ou agente_avulso+demanda_id obrigatorios'}), 400

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


# ── Analytics / BI ───────────────────────────────────────────────────
@controle_bp.route('/analytics')
def analytics():
    """Dados consolidados para o painel de BI."""
    init_db()
    with get_db() as conn:

        # Top agentes medidos (realizados)
        por_agente = [row_to_dict(r) for r in conn.execute("""
            SELECT agente, COUNT(*) AS qtd
            FROM medicoes WHERE status='realizado'
            GROUP BY agente ORDER BY qtd DESC LIMIT 12
        """).fetchall()]

        # Por tecnico (avaliador)
        por_tecnico = [row_to_dict(r) for r in conn.execute("""
            SELECT avaliador, COUNT(*) AS qtd
            FROM baixas
            WHERE avaliador IS NOT NULL AND avaliador != ''
            GROUP BY avaliador ORDER BY qtd DESC LIMIT 10
        """).fetchall()]

        # Top 10 empresas por demandas
        top_empresas = [row_to_dict(r) for r in conn.execute("""
            SELECT e.nome,
                   COUNT(d.id) AS total,
                   SUM(CASE WHEN d.status='concluida' THEN 1 ELSE 0 END) AS concluidas,
                   SUM(CASE WHEN d.status!='concluida' THEN 1 ELSE 0 END) AS pendentes
            FROM demandas d
            JOIN empresas e ON e.id = d.empresa_id
            GROUP BY e.id, e.nome
            ORDER BY total DESC LIMIT 10
        """).fetchall()]

        # Status amostradores
        status_rows = conn.execute("""
            SELECT status, COUNT(*) AS qtd FROM amostradores GROUP BY status
        """).fetchall()
        status_amostr = {r['status']: r['qtd'] for r in status_rows}

        # Status demandas
        dem_rows = conn.execute("""
            SELECT status, COUNT(*) AS qtd FROM demandas GROUP BY status
        """).fetchall()
        dem_por_status = {r['status']: r['qtd'] for r in dem_rows}

        # Evolucao mensal (ultimos 12 meses) — usa amostradores.data_medicao
        evolucao = [row_to_dict(r) for r in conn.execute("""
            SELECT strftime('%Y-%m', data_medicao) AS mes, COUNT(*) AS qtd
            FROM amostradores
            WHERE data_medicao IS NOT NULL
              AND data_medicao >= date('now','-12 months')
            GROUP BY mes ORDER BY mes
        """).fetchall()]

        # Demandas abertas por mes
        demandas_por_mes = [row_to_dict(r) for r in conn.execute("""
            SELECT strftime('%Y-%m', criado_em) AS mes, COUNT(*) AS qtd
            FROM demandas
            WHERE criado_em IS NOT NULL
              AND criado_em >= date('now','-12 months')
            GROUP BY mes ORDER BY mes
        """).fetchall()]

        # Amostradores por tipo
        por_tipo_amostrador = [row_to_dict(r) for r in conn.execute("""
            SELECT tipo AS tipo_amostrador, COUNT(*) AS qtd
            FROM amostradores GROUP BY tipo ORDER BY qtd DESC
        """).fetchall()]

        # KPIs
        total_dem  = sum(dem_por_status.values()) or 1
        concluidas = dem_por_status.get('concluida', 0)
        taxa_conclusao = round(concluidas / total_dem * 100, 1)

        dem_atrasadas = conn.execute("""
            SELECT COUNT(*) AS c FROM demandas
            WHERE status != 'concluida' AND prazo < date('now')
        """).fetchone()['c']

        total_med = conn.execute(
            "SELECT COUNT(*) AS c FROM medicoes WHERE status='realizado'"
        ).fetchone()['c']

        kpis = {
            'taxa_conclusao':     taxa_conclusao,
            'demandas_atrasadas': dem_atrasadas,
            'total_medicoes':     total_med,
            'total_demandas':     sum(dem_por_status.values()),
            'concluidas':         concluidas,
        }

    return jsonify({
        'por_agente':         por_agente,
        'por_tecnico':        por_tecnico,
        'top_empresas':       top_empresas,
        'status_amostradores': status_amostr,
        'dem_por_status':     dem_por_status,
        'evolucao_mensal':    evolucao,
        'demandas_por_mes':   demandas_por_mes,
        'por_tipo_amostrador': por_tipo_amostrador,
        'kpis':               kpis,
    })


# ── Coletas de Campo ──────────────────────────────────────────────────
from .db import (list_coletas_ruido, get_coleta_ruido, save_coleta_ruido,
                 list_coletas_quimico, get_coleta_quimico, save_coleta_quimico)

@controle_bp.route('/coletas/ruido')
def api_list_coletas_ruido():
    init_db()
    return jsonify(list_coletas_ruido(request.args.to_dict()))

@controle_bp.route('/coletas/ruido', methods=['POST'])
def api_save_coleta_ruido():
    init_db()
    d = request.json or {}
    cid = save_coleta_ruido(d)
    return jsonify({'ok': True, 'id': cid})

@controle_bp.route('/coletas/ruido/<int:cid>')
def api_get_coleta_ruido(cid):
    init_db()
    c = get_coleta_ruido(cid)
    return (jsonify(c), 200) if c else (jsonify({'erro': 'nao encontrada'}), 404)

@controle_bp.route('/coletas/ruido/<int:cid>', methods=['DELETE'])
def api_del_coleta_ruido(cid):
    init_db()
    with get_db() as conn:
        conn.execute('DELETE FROM coletas_ruido_func WHERE coleta_id=?', (cid,))
        conn.execute('DELETE FROM coletas_ruido WHERE id=?', (cid,))
    return jsonify({'ok': True})

@controle_bp.route('/coletas/quimico')
def api_list_coletas_quimico():
    init_db()
    return jsonify(list_coletas_quimico(request.args.to_dict()))

@controle_bp.route('/coletas/quimico', methods=['POST'])
def api_save_coleta_quimico():
    init_db()
    d = request.json or {}
    cid = save_coleta_quimico(d)
    return jsonify({'ok': True, 'id': cid})

@controle_bp.route('/coletas/quimico/<int:cid>')
def api_get_coleta_quimico(cid):
    init_db()
    c = get_coleta_quimico(cid)
    return (jsonify(c), 200) if c else (jsonify({'erro': 'nao encontrada'}), 404)

@controle_bp.route('/coletas/quimico/<int:cid>', methods=['DELETE'])
def api_del_coleta_quimico(cid):
    init_db()
    with get_db() as conn:
        conn.execute('DELETE FROM coletas_quimico_amostr WHERE coleta_id=?', (cid,))
        conn.execute('DELETE FROM coletas_quimico WHERE id=?', (cid,))
    return jsonify({'ok': True})


# ── Wizard: salvar medicao completa ──────────────────────────────────
@controle_bp.route('/medicoes', methods=['POST'])
def api_salvar_medicao_wizard():
    """Recebe payload do wizard Central Operacional e salva em coletas_ruido ou coletas_quimico."""
    init_db()
    d = request.json or {}
    tipo = d.get('tipo', '')

    if tipo == 'ruido':
        cr = d.get('campo_ruido') or {}
        payload_ruido = {
            'empresa_id':          d.get('empresa_id'),
            'empresa_nome':        d.get('empresa_nome', ''),
            'acompanhante':        cr.get('acomp', ''),
            'cargo_acompanhante':  cr.get('cargo_acomp', ''),
            'tecnico':             cr.get('tecnico') or d.get('avaliador', ''),
            'data_coleta':         d.get('data', ''),
            'hora_inicio':         cr.get('hora_ini', ''),
            'hora_termino':        cr.get('hora_fim', ''),
            'calibracao_inicial':  _safe_float(cr.get('cal_ini')),
            'calibracao_final':    _safe_float(cr.get('cal_fim')),
            'status':              'concluida',
            'trabalhadores':       cr.get('trabalhadores', []),
            'termos':              cr.get('termos', []),
            # extras para relatório
            'calibrador':          cr.get('calibrador', ''),
            'unidade':             d.get('unidade', ''),
            'cidade':              d.get('cidade', ''),
            'resp_empresa':        d.get('resp_empresa', ''),
            'os':                  d.get('os', ''),
            'itens':               d.get('itens', []),
        }
        cid = save_coleta_ruido(payload_ruido)
        return jsonify({'ok': True, 'id': cid, 'tipo': 'ruido'})

    elif tipo == 'quimico':
        cq = d.get('campo_quimico') or {}
        payload_q = {
            'empresa_id':    d.get('empresa_id'),
            'empresa_nome':  d.get('empresa_nome', ''),
            'avaliador':     d.get('avaliador', ''),
            'data_coleta':   d.get('data', ''),
            'status':        'concluida',
            'func_nome':     cq.get('func_nome', ''),
            'func_funcao':   cq.get('func_funcao', ''),
            'func_setor':    cq.get('func_setor', ''),
            'func_jornada':  cq.get('func_jornada', ''),
            'ventilacao':    cq.get('ventilacao', ''),
            'ambiente':      cq.get('ambiente', ''),
            'temperatura':   cq.get('temperatura', ''),
            'umidade':       cq.get('umidade', ''),
            'bomba':         cq.get('bomba', ''),
            'id_bomba':      cq.get('id_bomba', ''),
            'substancias':   cq.get('substancias', ''),
            'fracao':        cq.get('fracao', ''),
            'amostradores':  cq.get('amostradores', []),
        }
        cid = save_coleta_quimico(payload_q)
        return jsonify({'ok': True, 'id': cid, 'tipo': 'quimico'})

    return jsonify({'ok': True, 'aviso': 'tipo nao mapeado, nao salvo'})


def _safe_float(v):
    try: return float(str(v).replace(',', '.'))
    except: return None


# ── Relatório PDF de Ruído ────────────────────────────────────────────
@controle_bp.route('/relatorio/ruido', methods=['POST'])
def gerar_relatorio_ruido():
    """Gera PDF do relatório/planilha de campo de ruído.
    Aceita payload direto (do wizard) ou coleta_id para buscar do banco.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        Table, TableStyle, HRFlowable, KeepTogether)
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    except ImportError:
        return jsonify({'erro': 'reportlab nao instalado'}), 500

    d = request.json or {}
    cid = d.get('coleta_id')

    # Buscar do banco se fornecido ID
    if cid:
        coleta = get_coleta_ruido(int(cid))
        if coleta:
            d = coleta
            d['trabalhadores'] = coleta.get('trabalhadores', [])

    # Extrair campos
    empresa_nome   = d.get('empresa_nome', d.get('empresa', {}).get('nome', '—'))
    cnpj           = d.get('cnpj', d.get('empresa', {}).get('cnpj', ''))
    unidade        = d.get('unidade', '')
    cidade         = d.get('cidade', '')
    resp_empresa   = d.get('resp_empresa', '')
    os_num         = d.get('os', '')
    data_coleta    = d.get('data_coleta', d.get('data', ''))
    hora_ini       = d.get('hora_inicio', d.get('hora_ini', ''))
    hora_fim       = d.get('hora_termino', d.get('hora_fim', ''))
    tecnico        = d.get('tecnico', '')
    acomp          = d.get('acompanhante', d.get('acomp', ''))
    cargo_acomp    = d.get('cargo_acompanhante', d.get('cargo_acomp', ''))
    calibrador     = d.get('calibrador', '')
    cal_ini        = d.get('calibracao_inicial', d.get('cal_ini', ''))
    cal_fim        = d.get('calibracao_final', d.get('cal_fim', ''))
    desvio         = d.get('desvio_calibracao', '')
    status_cal     = d.get('status_calibracao', '')
    trabalhadores  = d.get('trabalhadores', [])
    termos         = d.get('termos', [])
    itens_ghe      = d.get('itens', [])

    # Calcular desvio se não veio calculado
    if not desvio and cal_ini and cal_fim:
        try:
            desvio = round(float(str(cal_fim).replace(',','.')) - float(str(cal_ini).replace(',','.')), 2)
        except: desvio = ''

    # Formatar data
    if data_coleta and '-' in str(data_coleta):
        try:
            from datetime import datetime as _dt
            data_fmt = _dt.strptime(data_coleta, '%Y-%m-%d').strftime('%d/%m/%Y')
        except: data_fmt = data_coleta
    else:
        data_fmt = data_coleta or '___/___/______'

    # ─── Estilos ───────────────────────────────────────────────────
    AZUL      = colors.HexColor('#1E3A8A')
    AZUL_CLR  = colors.HexColor('#DBEAFE')
    CINZA     = colors.HexColor('#F3F4F6')
    BORDA     = colors.HexColor('#CBD5E1')
    VERDE     = colors.HexColor('#16A34A')
    LARANJA   = colors.HexColor('#D97706')
    PRETO     = colors.black
    BRANCO    = colors.white

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
        title=f'Relatório de Ruído — {empresa_nome}')

    styles = getSampleStyleSheet()

    def sty(name, **kw):
        base = styles.get(name, styles['Normal'])
        return ParagraphStyle(f'_sty_{name}_{id(kw)}', parent=base, **kw)

    titulo_pg = sty('Title', fontSize=13, textColor=AZUL, alignment=TA_CENTER,
                    fontName='Helvetica-Bold', spaceAfter=2)
    subtit_pg = sty('Normal', fontSize=8.5, textColor=colors.HexColor('#475569'),
                    alignment=TA_CENTER, spaceAfter=10)
    sec_hdr   = sty('Normal', fontSize=8, fontName='Helvetica-Bold', textColor=BRANCO)
    cell_bold = sty('Normal', fontSize=8, fontName='Helvetica-Bold', textColor=PRETO)
    cell_reg  = sty('Normal', fontSize=8, fontName='Helvetica', textColor=PRETO)
    cell_sml  = sty('Normal', fontSize=7.5, fontName='Helvetica', textColor=PRETO)
    assin_sty = sty('Normal', fontSize=8, fontName='Helvetica', textColor=PRETO)
    footer_sty= sty('Normal', fontSize=6.5, textColor=colors.HexColor('#64748B'),
                    alignment=TA_CENTER)

    W = A4[0] - 3.6*cm   # largura útil

    elements = []

    # ─── Cabeçalho ─────────────────────────────────────────────────
    hdr_data = [[
        Paragraph('<b>OCUPACIONAL ENGENHARIA</b><br/><font size="7" color="#64748B">Higiene Ocupacional e Segurança do Trabalho</font>', sty('Normal', fontSize=10, fontName='Helvetica-Bold', textColor=AZUL)),
        Paragraph('<b>PLANILHA DE CAMPO — RUÍDO</b><br/><font size="7">Dosimetria de Ruído | NR-15 Anexo 1 | NHO-01 FUNDACENTRO</font>', sty('Normal', fontSize=10, fontName='Helvetica-Bold', textColor=AZUL, alignment=TA_RIGHT)),
    ]]
    hdr_tbl = Table(hdr_data, colWidths=[W*0.55, W*0.45])
    hdr_tbl.setStyle(TableStyle([
        ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('LINEBELOW',(0,0),(-1,-1),1.5,AZUL),
        ('BOTTOMPADDING',(0,0),(-1,-1),6),
    ]))
    elements.append(hdr_tbl)
    elements.append(Spacer(1, 8))

    # ─── Identificação da Empresa ───────────────────────────────────
    def sec_label(txt):
        t = Table([[Paragraph(txt, sec_hdr)]], colWidths=[W])
        t.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,-1),AZUL),
            ('TOPPADDING',(0,0),(-1,-1),3), ('BOTTOMPADDING',(0,0),(-1,-1),3),
            ('LEFTPADDING',(0,0),(-1,-1),6),
        ]))
        return t

    def info_row(pairs, widths=None):
        """pairs = [(label, value), ...] numa linha"""
        n = len(pairs)
        if not widths:
            widths = [W/n]*n
        cells = []
        for lbl, val in pairs:
            cells.append(Paragraph(f'<b>{lbl}:</b> {val or "—"}', cell_reg))
        t = Table([cells], colWidths=widths)
        t.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,-1),CINZA),
            ('GRID',(0,0),(-1,-1),0.4,BORDA),
            ('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4),
            ('LEFTPADDING',(0,0),(-1,-1),5),
        ]))
        return t

    elements.append(sec_label('1. IDENTIFICAÇÃO DA EMPRESA'))
    elements.append(Spacer(1, 2))
    elements.append(info_row([('Empresa', empresa_nome), ('CNPJ', cnpj or ''), ('OS', os_num)],
                              [W*0.50, W*0.30, W*0.20]))
    elements.append(Spacer(1, 1))
    elements.append(info_row([('Unidade/Obra', unidade), ('Cidade', cidade), ('Responsável', resp_empresa)],
                              [W*0.40, W*0.25, W*0.35]))
    elements.append(Spacer(1, 8))

    # ─── Dados da Medição ───────────────────────────────────────────
    elements.append(sec_label('2. DADOS DA MEDIÇÃO'))
    elements.append(Spacer(1, 2))
    elements.append(info_row([('Data', data_fmt), ('Hora Início', hora_ini), ('Hora Término', hora_fim)],
                              [W*0.35, W*0.25, W*0.40]))
    elements.append(Spacer(1, 1))
    elements.append(info_row([('Profissional Técnico', tecnico), ('Acompanhante', acomp), ('Cargo Acompanhante', cargo_acomp)],
                              [W*0.35, W*0.35, W*0.30]))
    elements.append(Spacer(1, 8))

    # ─── Calibração ─────────────────────────────────────────────────
    elements.append(sec_label('3. CALIBRAÇÃO DO EQUIPAMENTO'))
    elements.append(Spacer(1, 2))

    # Status calibração
    if desvio != '':
        try:
            dev_num = float(str(desvio).replace(',','.'))
            ok_cal  = abs(dev_num) <= 0.5
            status_cal_txt = ('✓ APROVADA' if ok_cal else '✗ REPROVADA') + f' (Δ = {desvio} dB)'
            status_cal_color = VERDE if ok_cal else LARANJA
        except:
            status_cal_txt = str(status_cal) or '—'
            status_cal_color = PRETO
    else:
        status_cal_txt = status_cal or '—'
        status_cal_color = PRETO

    cal_rows = [
        [Paragraph('<b>Calibrador</b>', cell_bold), Paragraph('<b>Cal. Inicial (dB)</b>', cell_bold),
         Paragraph('<b>Cal. Final (dB)</b>', cell_bold), Paragraph('<b>Desvio</b>', cell_bold),
         Paragraph('<b>Status</b>', cell_bold)],
        [Paragraph(str(calibrador) or '—', cell_reg),
         Paragraph(str(cal_ini) or '—', cell_reg),
         Paragraph(str(cal_fim) or '—', cell_reg),
         Paragraph(str(desvio) if desvio != '' else '—', cell_reg),
         Paragraph(f'<font color="{status_cal_color.hexval() if hasattr(status_cal_color,"hexval") else "#16A34A"}">{status_cal_txt}</font>', cell_reg)],
    ]
    cal_tbl = Table(cal_rows, colWidths=[W*0.28, W*0.18, W*0.18, W*0.14, W*0.22])
    cal_tbl.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),AZUL_CLR),
        ('GRID',(0,0),(-1,-1),0.4,BORDA),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
        ('FONTSIZE',(0,0),(-1,-1),8),
        ('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4),
        ('LEFTPADDING',(0,0),(-1,-1),5),
        ('ALIGN',(1,0),(3,-1),'CENTER'),
    ]))
    elements.append(cal_tbl)
    elements.append(Spacer(1, 8))

    # ─── Trabalhadores ───────────────────────────────────────────────
    elements.append(sec_label('4. IDENTIFICAÇÃO DOS TRABALHADORES AVALIADOS'))
    elements.append(Spacer(1, 2))

    trab_head = [
        Paragraph('<b>N°</b>', cell_bold),
        Paragraph('<b>N° Série Dosímetro</b>', cell_bold),
        Paragraph('<b>Nome Completo</b>', cell_bold),
        Paragraph('<b>Cargo/Função</b>', cell_bold),
        Paragraph('<b>Setor/GHE</b>', cell_bold),
        Paragraph('<b>Almoço/Pausa</b>', cell_bold),
    ]
    trab_rows = [trab_head]

    # Garantir pelo menos 5 linhas
    trabs_fill = list(trabalhadores) if trabalhadores else []
    while len(trabs_fill) < 5:
        trabs_fill.append({})

    for i, tr in enumerate(trabs_fill, 1):
        trab_rows.append([
            Paragraph(str(i), cell_reg),
            Paragraph(tr.get('serie_dosimetro','') or '', cell_reg),
            Paragraph(tr.get('nome','') or '', cell_reg),
            Paragraph(tr.get('cargo','') or '', cell_sml),
            Paragraph(tr.get('setor','') or '', cell_sml),
            Paragraph(tr.get('almoco','') or '', cell_reg),
        ])

    # Linha GHE (agentes do planejamento)
    if itens_ghe:
        ghe_txt = '; '.join([it.get('ghe','') or it.get('agente','') for it in itens_ghe if it.get('ghe') or it.get('agente')])
        trab_rows.append([
            Paragraph('GHEs:', cell_bold),
            Paragraph(ghe_txt[:120], cell_sml),
            '', '', '', '',
        ])

    trab_tbl = Table(trab_rows, colWidths=[W*0.05, W*0.17, W*0.26, W*0.20, W*0.18, W*0.14],
                     repeatRows=1)
    trab_tbl.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),AZUL_CLR),
        ('GRID',(0,0),(-1,-1),0.4,BORDA),
        ('FONTSIZE',(0,0),(-1,-1),8),
        ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
        ('LEFTPADDING',(0,0),(-1,-1),4),
        ('ALIGN',(0,0),(0,-1),'CENTER'),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[BRANCO, CINZA]),
        ('SPAN',(1,-1),(5,-1)) if itens_ghe else ('NOP',(0,0),(0,0)),
    ]))
    elements.append(trab_tbl)
    elements.append(Spacer(1, 8))

    # ─── Resultados (a preencher em campo / após leitura) ────────────
    elements.append(sec_label('5. RESULTADOS DAS MEDIÇÕES (Preencher após leitura dos dosímetros)'))
    elements.append(Spacer(1, 2))

    res_head = [
        Paragraph('<b>N°</b>', cell_bold),
        Paragraph('<b>Nome</b>', cell_bold),
        Paragraph('<b>Dose (%)</b>', cell_bold),
        Paragraph('<b>Nível Eq. dB(A)</b>', cell_bold),
        Paragraph('<b>Lavg dB(A)</b>', cell_bold),
        Paragraph('<b>Critério NR-15</b>', cell_bold),
        Paragraph('<b>Classificação</b>', cell_bold),
    ]
    res_rows = [res_head]
    for i in range(1, len(trabs_fill)+1):
        nm = trabs_fill[i-1].get('nome','') if i-1 < len(trabs_fill) else ''
        res_rows.append([
            Paragraph(str(i), cell_reg),
            Paragraph(nm or '', cell_sml),
            Paragraph('', cell_reg),
            Paragraph('', cell_reg),
            Paragraph('', cell_reg),
            Paragraph('100% / 85 dB(A)', cell_sml),
            Paragraph('', cell_reg),
        ])
    res_tbl = Table(res_rows, colWidths=[W*0.04, W*0.22, W*0.10, W*0.14, W*0.14, W*0.18, W*0.18],
                    repeatRows=1)
    res_tbl.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),AZUL_CLR),
        ('GRID',(0,0),(-1,-1),0.4,BORDA),
        ('FONTSIZE',(0,0),(-1,-1),8),
        ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
        ('LEFTPADDING',(0,0),(-1,-1),4),
        ('ALIGN',(0,0),(0,-1),'CENTER'),
        ('ALIGN',(2,0),(5,-1),'CENTER'),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[BRANCO, CINZA]),
    ]))
    elements.append(res_tbl)
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        '<font size="7" color="#475569">Critério NR-15 Anexo 1 (Portaria 3214/78): '
        'Limite 85 dB(A) para 8h/dia — Dose = 100%. '
        'NHO-01 FUNDACENTRO: Nível de Ação 80 dB(A) (NA), Limite de Tolerância 85 dB(A) (LT). '
        'Classificação: ≤ 80 dB(A) = Baixo | 80–85 = Moderado (NA) | > 85 = Alto (LT).</font>',
        cell_reg))
    elements.append(Spacer(1, 8))

    # ─── Termo de Responsabilidade ───────────────────────────────────
    elements.append(sec_label('6. TERMO DE RESPONSABILIDADE — DOSÍMETROS'))
    elements.append(Spacer(1, 2))

    termo_head = [
        Paragraph('<b>N°</b>', cell_bold),
        Paragraph('<b>Nome</b>', cell_bold),
        Paragraph('<b>Função</b>', cell_bold),
        Paragraph('<b>Assinatura</b>', cell_bold),
        Paragraph('<b>Data Entrega</b>', cell_bold),
        Paragraph('<b>Data Devolução</b>', cell_bold),
    ]
    termo_rows = [termo_head]

    termos_fill = list(termos) if termos else []
    while len(termos_fill) < 5:
        termos_fill.append({})

    for i, tr in enumerate(termos_fill, 1):
        termo_rows.append([
            Paragraph(str(i), cell_reg),
            Paragraph(tr.get('nome','') or '', cell_reg),
            Paragraph(tr.get('funcao','') or '', cell_reg),
            Paragraph('', cell_reg),   # assinatura (campo em branco)
            Paragraph(tr.get('data_entrega', data_fmt) or '', cell_reg),
            Paragraph('', cell_reg),   # data devolução
        ])

    termo_tbl = Table(termo_rows, colWidths=[W*0.04, W*0.24, W*0.20, W*0.22, W*0.15, W*0.15],
                      repeatRows=1)
    termo_tbl.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),AZUL_CLR),
        ('GRID',(0,0),(-1,-1),0.4,BORDA),
        ('FONTSIZE',(0,0),(-1,-1),8),
        ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),14),
        ('LEFTPADDING',(0,0),(-1,-1),4),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[BRANCO, CINZA]),
    ]))
    elements.append(termo_tbl)
    elements.append(Spacer(1, 10))

    # ─── Assinaturas finais ──────────────────────────────────────────
    assin_rows = [[
        Paragraph('_________________________________<br/><font size="7">Profissional Técnico</font><br/>'
                  f'<font size="7">{tecnico}</font>', assin_sty),
        Paragraph('_________________________________<br/><font size="7">Responsável da Empresa / Acompanhante</font><br/>'
                  f'<font size="7">{acomp}</font>', assin_sty),
        Paragraph(f'_________________________________<br/><font size="7">Data: {data_fmt}</font>',
                  assin_sty),
    ]]
    assin_tbl = Table(assin_rows, colWidths=[W/3, W/3, W/3])
    assin_tbl.setStyle(TableStyle([
        ('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('VALIGN',(0,0),(-1,-1),'TOP'),
        ('TOPPADDING',(0,0),(-1,-1),4),
    ]))
    elements.append(assin_tbl)
    elements.append(Spacer(1, 10))

    # ─── Rodapé ──────────────────────────────────────────────────────
    elements.append(HRFlowable(width=W, thickness=0.5, color=BORDA))
    elements.append(Spacer(1, 3))
    elements.append(Paragraph(
        'Normas: NR-15 Anexo 1 (Portaria 3214/78) — NHO-01 FUNDACENTRO '
        '— ABNT NBR 10151 — Portaria MTb 1.297/2017 | '
        f'Gerado em: {datetime.now().strftime("%d/%m/%Y %H:%M")} | Ocupacional Engenharia',
        footer_sty))

    doc.build(elements)
    buf.seek(0)
    nome_safe = re.sub(r'[^\w-]', '_', empresa_nome)[:40]
    data_safe = data_fmt.replace('/', '-')
    return send_file(buf, as_attachment=True,
        download_name=f'planilha_ruido_{nome_safe}_{data_safe}.pdf',
        mimetype='application/pdf')


# ══════════════════════════════════════════════════════════════════════
# MICROSOFT GRAPH — Planner, Outlook, SharePoint
# ══════════════════════════════════════════════════════════════════════

@controle_bp.route('/graph/status')
def graph_status():
    """Verifica conexão com Microsoft Graph."""
    try:
        from .graph import graph_ok, CLIENT_ID, TENANT_ID
        from .planner_sync import get_sync_status
        ok   = graph_ok()
        sync = get_sync_status()
        return jsonify({
            'configurado':  ok,
            'client_id':   (CLIENT_ID[:8] + '...') if CLIENT_ID else '',
            'tenant_id':   (TENANT_ID[:8] + '...') if TENANT_ID else '',
            'last_sync':   sync.get('last_sync'),
            'last_stats':  sync.get('stats', {}),
        })
    except Exception as e:
        return jsonify({'configurado': False, 'erro': str(e)}), 500


# ── Pipeline / Operational Demands ───────────────────────────────────

@controle_bp.route('/operacional')
def get_operacional():
    """Demandas operacionais limpas (só clientes reais, sem interna/administrativa)."""
    init_db()
    return jsonify(list_operational_demands(request.args.to_dict()))


@controle_bp.route('/operacional/por_empresa')
def get_operacional_por_empresa():
    """Demandas operacionais agrupadas por empresa."""
    init_db()
    return jsonify(list_operational_por_empresa(request.args.to_dict()))


@controle_bp.route('/pipeline/raw_tasks')
def get_raw_tasks():
    """Raw tasks do Planner (staging — todas, com status do pipeline)."""
    init_db()
    limit = min(int(request.args.get('limit', 200)), 1000)
    filtros = {k: v for k, v in request.args.to_dict().items() if k != 'limit'}
    return jsonify(list_raw_tasks(filtros, limit=limit))


@controle_bp.route('/pipeline/stats')
def get_pipeline_stats():
    """Estatísticas do pipeline: quantas ignoradas por bucket, etc."""
    init_db()
    return jsonify(stats_raw_pipeline())


@controle_bp.route('/graph/sync', methods=['POST'])
def graph_sync_manual():
    """Dispara sync manual do Planner em background (evita timeout do gunicorn)."""
    init_db()
    try:
        from .planner_sync import sync_planner
        import threading
        from flask import current_app
        d = request.json or {}
        group_filter = d.get('group_id')
        label_filter = d.get('label_filter', 'Medições')  # padrão: só tasks com label Medições

        app = current_app._get_current_object()

        def _run():
            with app.app_context():
                try:
                    sync_planner(group_filter=group_filter, label_filter=label_filter)
                except Exception as ex:
                    import logging, traceback
                    logging.getLogger(__name__).error('[graph/sync] erro no background: %s', ex)
                    # Salva erro no DB para debug
                    try:
                        with get_db() as c:
                            c.execute("INSERT OR REPLACE INTO ms_sync_state (chave,valor,atualizado_em) VALUES ('last_sync_error',?,CURRENT_TIMESTAMP)",
                                      (traceback.format_exc()[:2000],))
                    except Exception:
                        pass

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({'ok': True, 'mensagem': 'Sync iniciado em background — verifique /graph/status em alguns minutos'})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/graph/debug_labels')
def graph_debug_labels():
    """Debug: retorna os labels (categoryDescriptions) de todos os planos conhecidos.
    Útil para validar qual categoryN corresponde a 'Medições'."""
    init_db()
    try:
        from .graph import graph_ok, get_plans_for_group, get_teams_groups, get_plan_category_map
        if not graph_ok():
            return jsonify({'erro': 'Sem autenticação Graph API'}), 503
        grupos = get_teams_groups()
        resultado = []
        for g in grupos[:10]:  # limita a 10 grupos para não demorar
            gid   = g.get('id', '')
            gnome = g.get('displayName', '')
            try:
                planos = get_plans_for_group(gid)
            except Exception:
                continue
            for p in planos[:5]:
                pid   = p.get('id', '')
                pnome = p.get('title', '')
                try:
                    cat_map = get_plan_category_map(pid)
                    # Filtrar só labels não-nulos
                    labels = {k: v for k, v in cat_map.items() if v}
                    medicoes_cats = [k for k, v in cat_map.items() if v and 'medic' in v.lower().replace('ç','c').replace('õ','o')]
                    resultado.append({
                        'grupo':         gnome,
                        'plano':         pnome,
                        'plan_id':       pid,
                        'labels':        labels,
                        'medicoes_cats': medicoes_cats,
                    })
                except Exception as e:
                    resultado.append({'grupo': gnome, 'plano': pnome, 'erro': str(e)})
        return jsonify(resultado)
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'tb': traceback.format_exc()[:1000]}), 500


@controle_bp.route('/graph/sync_mini')
def graph_sync_mini():
    """Sync SÍNCRONO mini: processa as primeiras N tasks de um plano específico.
    Uso: /controle/graph/sync_mini?plan_id=<ID>&limit=20&label_filter=Medições
    Retorna resultado direto (sem background), para diagnóstico."""
    plan_id = request.args.get('plan_id', 'JOHzljvSKkmfSsQ7SekCnWUAA8cz').strip()
    limit   = int(request.args.get('limit', 50))
    label_filter = request.args.get('label_filter', 'Medições').strip()

    try:
        from .graph import graph_ok, get_plan_tasks, get_plan_buckets, get_plan_category_map, get_task_details
        from .planner_sync import _task_has_label, _find_category_ids, _normalize, _parse_date
        from .db import upsert_raw_task, mark_raw_task
        import json

        if not graph_ok():
            return jsonify({'erro': 'Sem autenticação Graph API'}), 503

        init_db()

        cat_map = get_plan_category_map(plan_id)
        category_ids = _find_category_ids(cat_map, label_filter)
        buckets = get_plan_buckets(plan_id)
        bucket_map = {b['id']: b.get('name', '') for b in buckets}

        tarefas = get_plan_tasks(plan_id)[:limit]

        resultado = {
            'plan_id': plan_id,
            'label_filter': label_filter,
            'category_ids': list(category_ids),
            'total_buscado': len(tarefas),
            'com_label': 0,
            'sem_label': 0,
            'gravadas_raw': 0,
            'demandas_criadas': 0,
            'demandas_atualizadas': 0,
            'sample_com_label': [],
            'erros': [],
        }

        with get_db() as conn:
            conn.execute('PRAGMA foreign_keys = OFF')
            for tarefa in tarefas:
                tid    = tarefa['id']
                titulo = tarefa.get('title', '')
                bucket = bucket_map.get(tarefa.get('bucketId', ''), '')
                tem    = _task_has_label(tarefa, category_ids) if category_ids else False

                raw_data = {
                    'planner_plan_id':   tarefa.get('planId', ''),
                    'planner_plan_nome': 'Entregas Técnicas',
                    'planner_bucket_id': tarefa.get('bucketId', ''),
                    'planner_bucket':    bucket,
                    'planner_group_id':  '4c80214b-6801-414a-9fc7-27feff0b3de6',
                    'planner_group_nome':'Ocupacional',
                    'titulo':            titulo,
                    'descricao':         '',
                    'checklist_json':    '[]',
                    'raw_json':          json.dumps(tarefa) if tem else '',
                    'percent_complete':  tarefa.get('percentComplete', 0),
                    'prazo':             _parse_date(tarefa.get('dueDateTime')),
                    'criado_em_ms':      _parse_date(tarefa.get('createdDateTime')),
                    'concluido_em_ms':   _parse_date(tarefa.get('completedDateTime')),
                    'ms_assignee_id':    (list(tarefa.get('assignments', {}).keys()) or [None])[0],
                    'ms_assignees_json': json.dumps(list(tarefa.get('assignments', {}).keys())),
                    'etiquetas_json':    json.dumps(tarefa.get('appliedCategories', {})),
                }
                try:
                    upsert_raw_task(conn, tid, raw_data)
                    resultado['gravadas_raw'] += 1
                except Exception as e:
                    resultado['erros'].append(f'upsert_raw: {e}')

                if tem:
                    resultado['com_label'] += 1
                    resultado['sample_com_label'].append({
                        'titulo': titulo[:80],
                        'bucket': bucket,
                        'applied': tarefa.get('appliedCategories', {}),
                    })
                else:
                    resultado['sem_label'] += 1

        return jsonify(resultado)
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'tb': traceback.format_exc()[:2000]}), 500


@controle_bp.route('/graph/debug_plan')
def graph_debug_plan():
    """Debug: inspeciona labels de um plano específico por plan_id.
    Uso: /controle/graph/debug_plan?plan_id=<ID>"""
    plan_id = request.args.get('plan_id', '').strip()
    if not plan_id:
        return jsonify({'erro': 'Informe ?plan_id=<ID do plano no Planner>'}), 400
    try:
        from .graph import graph_ok, get_plan_category_map, get_plan_buckets, get_plan_tasks
        if not graph_ok():
            return jsonify({'erro': 'Sem autenticação Graph API'}), 503
        cat_map = get_plan_category_map(plan_id)
        labels_nao_nulos = {k: v for k, v in cat_map.items() if v}
        medicoes_cats = [k for k, v in cat_map.items() if v and 'medic' in v.lower().replace('ç', 'c').replace('õ', 'o')]
        buckets = get_plan_buckets(plan_id)
        tasks_sample = get_plan_tasks(plan_id)
        # Mostra applied categories de até 5 tarefas
        tasks_info = [
            {'titulo': t.get('title', '')[:80],
             'appliedCategories': t.get('appliedCategories', {}),
             'bucket_id': t.get('bucketId', '')}
            for t in tasks_sample[:10]
        ]
        return jsonify({
            'plan_id': plan_id,
            'all_labels': cat_map,
            'labels_nao_nulos': labels_nao_nulos,
            'medicoes_cats': medicoes_cats,
            'buckets': [{'id': b['id'], 'nome': b.get('name', '')} for b in buckets],
            'total_tasks': len(tasks_sample),
            'tasks_sample': tasks_info,
        })
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'tb': traceback.format_exc()[:1000]}), 500


@controle_bp.route('/graph/debug_groups')
def graph_debug_groups():
    """Debug: lista TODOS os grupos M365, com contagem de planos Planner."""
    try:
        from .graph import graph_ok, get_teams_groups, get_plans_for_group
        if not graph_ok():
            return jsonify({'erro': 'Sem autenticação Graph API'}), 503
        grupos = get_teams_groups()
        resultado = []
        for g in grupos:
            gid   = g.get('id', '')
            gnome = g.get('displayName', '')
            try:
                planos = get_plans_for_group(gid)
                if planos:
                    resultado.append({
                        'id': gid, 'nome': gnome,
                        'planos': [{'id': p['id'], 'titulo': p.get('title', '')} for p in planos]
                    })
            except Exception:
                pass  # grupo sem Planner
        return jsonify({'total_grupos': len(grupos), 'grupos_com_planner': resultado})
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'tb': traceback.format_exc()[:1000]}), 500


@controle_bp.route('/graph/debug_empresa_titulos')
def graph_debug_empresa_titulos():
    """Debug: retorna títulos das demandas vinculadas a uma empresa (por id ou nome parcial)."""
    try:
        empresa_id = request.args.get('empresa_id', type=int)
        search = request.args.get('search', '')
        with get_db() as conn:
            if empresa_id:
                rows = conn.execute(
                    'SELECT d.id, d.titulo, d.empresa_id, e.nome empresa_nome, d.origem FROM demandas d LEFT JOIN empresas e ON e.id=d.empresa_id WHERE d.empresa_id=? LIMIT 20',
                    (empresa_id,)
                ).fetchall()
            elif search:
                rows = conn.execute(
                    "SELECT d.id, d.titulo, d.empresa_id, e.nome empresa_nome, d.origem FROM demandas d LEFT JOIN empresas e ON e.id=d.empresa_id WHERE d.titulo LIKE ? OR e.nome LIKE ? LIMIT 20",
                    (f'%{search}%', f'%{search}%')
                ).fetchall()
            else:
                return jsonify({'erro': 'passe empresa_id= ou search='})
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/graph/sync_error')
def graph_sync_error():
    """Retorna o último erro de sync para debug."""
    try:
        with get_db() as conn:
            row = conn.execute("SELECT valor, atualizado_em FROM ms_sync_state WHERE chave='last_sync_error'").fetchone()
        if row:
            return jsonify({'erro': row['valor'], 'quando': row['atualizado_em']})
        return jsonify({'erro': None, 'msg': 'Nenhum erro registrado'})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/graph/groups')
def graph_list_groups():
    """Lista grupos Teams/Microsoft 365 disponíveis."""
    try:
        from .graph import get_teams_groups
        grupos = get_teams_groups()
        return jsonify([{
            'id':        g['id'],
            'nome':      g.get('displayName', ''),
            'descricao': g.get('description', ''),
            'email':     g.get('mail', ''),
        } for g in grupos])
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/graph/plans')
def graph_list_plans():
    """Lista planos do Planner de um grupo (query param: group_id)."""
    gid = request.args.get('group_id', '')
    if not gid:
        return jsonify({'erro': 'group_id obrigatorio'}), 400
    try:
        from .graph import get_plans_for_group, get_plan_buckets
        planos = get_plans_for_group(gid)
        result = []
        for p in planos:
            buckets = []
            try:
                buckets = [{'id': b['id'], 'nome': b.get('name', '')}
                           for b in get_plan_buckets(p['id'])]
            except Exception:
                pass
            result.append({'id': p['id'], 'titulo': p.get('title', ''), 'buckets': buckets})
        return jsonify(result)
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/graph/users')
def graph_list_users():
    """Lista usuários Microsoft da organização."""
    try:
        from .graph import list_org_users
        users = list_org_users()
        return jsonify([{
            'id':    u['id'],
            'nome':  u.get('displayName', ''),
            'email': u.get('mail') or u.get('userPrincipalName', ''),
            'cargo': u.get('jobTitle', ''),
            'dept':  u.get('department', ''),
        } for u in users])
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/eventos')
def api_eventos():
    """Retorna log de eventos operacionais (últimos 200)."""
    init_db()
    with get_db() as conn:
        rows = conn.execute("""
            SELECT e.*, u.display_name AS ms_user_nome, u.email AS ms_user_email
            FROM eventos e
            LEFT JOIN ms_users u ON u.ms_id = e.ms_user_id
            ORDER BY e.criado_em DESC LIMIT 200
        """).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


# ── Matching empresa ──────────────────────────────────────────────────

@controle_bp.route('/empresas/pendentes')
def api_empresas_pendentes():
    """Lista empresas criadas automaticamente pelo Planner que precisam de validação."""
    init_db()
    with get_db() as conn:
        rows = conn.execute('''
            SELECT e.id, e.nome, e.cnpj, e.criado_em,
                   COUNT(d.id) AS total_demandas
            FROM empresas e
            LEFT JOIN demandas d ON d.empresa_id = e.id
            WHERE e.pendente = 1
            GROUP BY e.id
            ORDER BY total_demandas DESC, e.criado_em DESC
        ''').fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@controle_bp.route('/empresas/pendentes/<int:pend_id>/vincular', methods=['POST'])
def api_vincular_empresa_pendente(pend_id):
    """
    Vincula empresa pendente a uma empresa existente (ou a confirma como nova).
    Body: {"empresa_id": 123}  → usa empresa existente
    Body: {"confirmar": true}  → confirma pendente como empresa real (remove flag)
    """
    init_db()
    d = request.json or {}
    with get_db() as conn:
        if 'empresa_id' in d:
            destino = int(d['empresa_id'])
            # Remapear demandas da pendente para a real
            conn.execute('UPDATE demandas SET empresa_id=? WHERE empresa_id=?', (destino, pend_id))
            conn.execute('DELETE FROM empresas WHERE id=?', (pend_id,))
            return jsonify({'ok': True, 'acao': 'remapeada', 'empresa_id': destino})
        elif d.get('confirmar'):
            conn.execute('UPDATE empresas SET pendente=0 WHERE id=?', (pend_id,))
            return jsonify({'ok': True, 'acao': 'confirmada'})
        else:
            return jsonify({'erro': 'Informe empresa_id ou confirmar:true'}), 400


@controle_bp.route('/demandas/match-empresas', methods=['POST'])
def api_match_empresas():
    """Re-executa matching de empresa em todas as demandas sem vínculo."""
    init_db()
    try:
        from .empresa_match import match_todas_demandas
        with get_db() as conn:
            conn.execute('PRAGMA foreign_keys = OFF')
            stats = match_todas_demandas(conn)
        return jsonify({'ok': True, **stats})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/demandas/reclassificar', methods=['POST'])
def api_reclassificar():
    """Reclassifica TODAS as demandas do Planner (operacional/interna/administrativa)
    e extrai OS do título quando ainda não preenchida."""
    init_db()
    try:
        from .classificador import reclassificar_lote
        with get_db() as conn:
            stats = reclassificar_lote(conn)
        return jsonify({'ok': True, **stats})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/empresas/mesclar', methods=['POST'])
def api_mesclar_empresas():
    """Mescla empresas duplicadas (mesmo nome normalizado)."""
    init_db()
    try:
        n = mesclar_empresas_duplicatas()
        return jsonify({'ok': True, 'mescladas': n})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@controle_bp.route('/admin/saude')
def admin_saude():
    """Diagnóstico completo do banco e integrações."""
    init_db()
    try:
        from .monitoring import diagnostico_banco
        return jsonify(diagnostico_banco())
    except Exception as e:
        import traceback
        return jsonify({'erro': str(e), 'tb': traceback.format_exc()[:1000]}), 500


@controle_bp.route('/admin/log_evento', methods=['POST'])
def admin_log_evento():
    """Registra evento de analytics (PostHog server-side)."""
    d = request.json or {}
    try:
        from .monitoring import track_evento
        track_evento(
            d.get('evento', 'acao_usuario'),
            usuario=d.get('usuario', 'web'),
            **{k: v for k, v in d.items() if k not in ('evento', 'usuario')}
        )
    except Exception:
        pass
    return jsonify({'ok': True})


@controle_bp.route('/eventos', methods=['POST'])
def api_registrar_evento():
    """Registra evento manualmente (ações do usuário)."""
    init_db()
    d = request.json or {}
    with get_db() as conn:
        conn.execute(
            'INSERT INTO eventos (tipo,descricao,ref_id,ref_tipo,usuario,criado_em) '
            'VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)',
            (d.get('tipo', 'acao'), d.get('descricao', ''),
             d.get('ref_id'), d.get('ref_tipo'), d.get('usuario', 'sistema'))
        )
    return jsonify({'ok': True})
