# -*- coding: utf-8 -*-
"""Blueprint Pericias — onda 1: leitura dos autos + rol de quesitos.

Fluxo que este modulo atende (definido pelo time de pericias):
    autos do PJe -> peças fatiadas -> ata (modalidade, perito, prazos)
    -> REVISÃO HUMANA -> rol de quesitos (catálogo + caso concreto) -> .docx

Duas decisoes de projeto que atravessam o arquivo:

1. O fatiamento das pecas e DETERMINISTICO (vem do indice de marcadores do PDF
   do PJe), entao nenhuma etapa aqui depende de IA para achar peca.

2. A leitura da ata e SEMPRE rascunho: `status='rascunho'` ate alguem confirmar
   em /confirmar. A ata nao tem formato unico entre regionais e prazo lido
   errado significa preclusao — o sistema propoe, a pessoa responde.
"""
import io
import json
import os
import re
import zipfile
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, jsonify, request, send_file
from flask_login import current_user

from .db import get_db, init_db, row_to_dict
from .pericias_extract import (
    MODALIDADE_LABEL, PRAZOS, analisar_autos, categoria_da_peca,
)

pericias_bp = Blueprint('pericias', __name__, url_prefix='/pericias')

_BRT = timezone(timedelta(hours=-3))
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOGO_SEED = os.path.join(BASE_DIR, 'data', 'quesitos_catalogo.json')

STATUS = ('rascunho', 'confirmado', 'quesitos_enviados', 'encerrado')
STATUS_LABEL = {
    'rascunho': 'Aguardando revisão',
    'confirmado': 'Revisado',
    'quesitos_enviados': 'Quesitos entregues',
    'encerrado': 'Encerrado',
}
# Manutencao/diagnostico e admin-only; o time de pericias ve o fluxo limpo.
_ADMIN_ONLY = ('/pericias/catalogo/importar', '/pericias/admin')


def _agora():
    return datetime.now(timezone.utc).astimezone(_BRT).replace(tzinfo=None).strftime(
        '%Y-%m-%d %H:%M:%S')


def _quem():
    if current_user.is_authenticated:
        return (getattr(current_user, 'nome', None)
                or getattr(current_user, 'email', '') or 'usuário')
    return 'desconhecido'


# ── Schema ─────────────────────────────────────────────────────────────
_pericias_ready = False


def init_pericias():
    """Cria as tabelas do modulo. Idempotente, roda 1x por processo."""
    global _pericias_ready
    if _pericias_ready:
        return
    init_db()
    from .db import USE_PG
    pk = 'SERIAL PRIMARY KEY' if USE_PG else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    with get_db() as conn:
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS pericias_processos (
                id                  {pk},
                numero_cnj          TEXT,
                reclamante          TEXT,
                reclamada           TEXT,
                trt                 TEXT,
                vara                TEXT,
                comarca             TEXT,
                rito                TEXT,
                juiz                TEXT,
                data_audiencia      TEXT,
                modalidades         TEXT,
                modalidades_fora    TEXT,
                perito_nomeado      TEXT,
                peritos_json        TEXT,
                prazo_quesitos      TEXT,
                diligencia_ini      TEXT,
                diligencia_fim      TEXT,
                prazo_laudo         TEXT,
                vista_ini           TEXT,
                vista_fim           TEXT,
                esclarec_ini        TEXT,
                esclarec_fim        TEXT,
                audiencia_instrucao TEXT,
                confianca           INTEGER DEFAULT 0,
                avisos_json         TEXT,
                status              TEXT DEFAULT 'rascunho',
                resultado           TEXT,
                empresa_id          INTEGER,
                arquivo_nome        TEXT,
                total_paginas       INTEGER,
                paginas_nativas     INTEGER,
                total_pecas         INTEGER,
                observacao          TEXT,
                criado_em           TEXT DEFAULT CURRENT_TIMESTAMP,
                criado_por          TEXT,
                revisado_em         TEXT,
                revisado_por        TEXT
            )
        ''')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_perproc_cnj '
                     'ON pericias_processos(numero_cnj)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_perproc_status '
                     'ON pericias_processos(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_perproc_prazo '
                     'ON pericias_processos(prazo_quesitos)')

        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS pericias_pecas (
                id             {pk},
                processo_id    INTEGER NOT NULL,
                ordem          INTEGER,
                tipo_pje       TEXT,
                categoria      TEXT,
                data_juntada   TEXT,
                pag_ini        INTEGER,
                pag_fim        INTEGER,
                paginas        INTEGER,
                densidade      INTEGER,
                escaneada      INTEGER DEFAULT 0,
                texto_integral INTEGER DEFAULT 0,
                texto          TEXT
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_perpecas_proc '
                     'ON pericias_pecas(processo_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_perpecas_cat '
                     'ON pericias_pecas(processo_id, categoria)')

        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS pericias_quesitos (
                id          {pk},
                hash        TEXT,
                materia     TEXT,
                texto       TEXT NOT NULL,
                ocorrencias INTEGER DEFAULT 1,
                origem      TEXT DEFAULT 'catalogo',
                ativo       INTEGER DEFAULT 1,
                criado_em   TEXT DEFAULT CURRENT_TIMESTAMP,
                criado_por  TEXT
            )
        ''')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_perques_hash '
                     'ON pericias_quesitos(hash)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_perques_materia '
                     'ON pericias_quesitos(materia, ativo)')

        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS pericias_rol (
                id          {pk},
                processo_id INTEGER NOT NULL,
                quesito_id  INTEGER,
                ordem       INTEGER DEFAULT 0,
                materia     TEXT,
                texto       TEXT NOT NULL,
                origem      TEXT DEFAULT 'catalogo',
                criado_em   TEXT DEFAULT CURRENT_TIMESTAMP,
                criado_por  TEXT
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_perrol_proc '
                     'ON pericias_rol(processo_id, ordem)')
    _pericias_ready = True


# ── Guard ──────────────────────────────────────────────────────────────
@pericias_bp.before_request
def _guard():
    if not current_user.is_authenticated:
        return jsonify({'erro': 'Login necessário', 'redirect': '/auth/login'}), 401
    if any(request.path.startswith(p) for p in _ADMIN_ONLY):
        if getattr(current_user, 'role', '') != 'admin':
            return jsonify({'erro': 'Apenas administradores'}), 403


# ── Helpers ────────────────────────────────────────────────────────────
def _csv(lista):
    return ','.join(lista) if lista else ''


def _lista(csv_txt):
    return [x for x in (csv_txt or '').split(',') if x]


def _dias_para(iso):
    """Dias entre hoje e a data ISO. Negativo = venceu."""
    if not iso:
        return None
    try:
        return (datetime.strptime(iso[:10], '%Y-%m-%d').date() - date.today()).days
    except ValueError:
        return None


def _processo_dict(row):
    d = row_to_dict(row)
    d['modalidades'] = _lista(d.get('modalidades'))
    d['modalidades_fora'] = _lista(d.get('modalidades_fora'))
    d['modalidades_label'] = [MODALIDADE_LABEL.get(m, m) for m in d['modalidades']]
    d['modalidades_fora_label'] = [MODALIDADE_LABEL.get(m, m) for m in d['modalidades_fora']]
    d['status_label'] = STATUS_LABEL.get(d.get('status'), d.get('status'))
    for campo in ('avisos_json', 'peritos_json'):
        try:
            d[campo.replace('_json', '')] = json.loads(d.get(campo) or '[]')
        except (TypeError, ValueError):
            d[campo.replace('_json', '')] = []
        d.pop(campo, None)
    d['dias_para_quesitos'] = _dias_para(d.get('prazo_quesitos'))
    d['dias_para_laudo'] = _dias_para(d.get('prazo_laudo'))
    return d


# ── Upload e leitura dos autos ─────────────────────────────────────────
@pericias_bp.route('/autos', methods=['POST'])
def upload_autos():
    """Recebe o PDF dos autos, fatia as pecas e le a ata.

    Devolve o processo em `status='rascunho'`. Nada aqui vale como prazo antes
    de passar por /confirmar.
    """
    init_pericias()
    arq = request.files.get('arquivo') or request.files.get('pdf')
    if not arq or not (arq.filename or '').lower().endswith('.pdf'):
        return jsonify({'erro': 'Envie o PDF dos autos no campo "arquivo".'}), 400

    raw = arq.read()
    if not raw:
        return jsonify({'erro': 'Arquivo vazio.'}), 400
    try:
        dados = analisar_autos(raw)
    except Exception as e:
        return jsonify({'erro': 'Não foi possível ler o PDF: %s' % e}), 400

    cab, ext = dados['cabecalho'], dados['extracao']
    if not cab.get('tem_indice'):
        ext['avisos'].insert(0, 'O PDF não tem índice de peças do PJe — as peças não '
                                'foram separadas. Baixe os autos completos pelo PJe.')

    prazos = ext.get('prazos') or {}
    campos = {
        'numero_cnj': cab.get('numero_cnj'),
        'reclamante': cab.get('reclamante'), 'reclamada': cab.get('reclamada'),
        'trt': cab.get('trt'), 'vara': cab.get('vara'), 'comarca': cab.get('comarca'),
        'rito': cab.get('rito'), 'juiz': ext.get('juiz'),
        'data_audiencia': ext.get('data_audiencia'),
        'modalidades': _csv(ext.get('modalidades')),
        'modalidades_fora': _csv(ext.get('modalidades_fora')),
        'perito_nomeado': ext.get('perito_nomeado'),
        'peritos_json': json.dumps(ext.get('peritos') or [], ensure_ascii=False),
        'confianca': ext.get('confianca') or 0,
        'avisos_json': json.dumps(ext.get('avisos') or [], ensure_ascii=False),
        'arquivo_nome': arq.filename,
        'total_paginas': cab.get('total_paginas'),
        'paginas_nativas': cab.get('paginas_nativas'),
        'total_pecas': len(dados['pecas']),
        'criado_por': _quem(),
    }
    for chave, _rotulo in PRAZOS:
        campos[chave] = (prazos.get(chave) or {}).get('data')

    with get_db() as conn:
        antigo = None
        if campos['numero_cnj']:
            antigo = conn.execute('SELECT id, status FROM pericias_processos '
                                  'WHERE numero_cnj = ?',
                                  (campos['numero_cnj'],)).fetchone()
        divergencias = []
        if antigo:
            ant = row_to_dict(conn.execute('SELECT * FROM pericias_processos WHERE id = ?',
                                           (row_to_dict(antigo)['id'],)).fetchone())
            processo_id = ant['id']
            reaproveitado = True
            # Reenvio dos autos (ata nova, peca juntada). Se alguem JA revisou,
            # a leitura automatica nao pode passar por cima do que a pessoa
            # decidiu — senao um reenvio silencioso troca modalidade e prazo de
            # um processo ja conferido. Nesse caso so os dados do arquivo sao
            # atualizados, e a divergencia volta como aviso para decidir.
            if (ant.get('status') or 'rascunho') == 'rascunho':
                livres = [k for k in campos if k != 'criado_por']
            else:
                livres = ['arquivo_nome', 'total_paginas', 'paginas_nativas',
                          'total_pecas', 'confianca', 'avisos_json']
                for campo in ['modalidades', 'perito_nomeado'] + [k for k, _ in PRAZOS]:
                    novo, gravado = campos.get(campo), ant.get(campo)
                    if novo and str(novo) != str(gravado or ''):
                        divergencias.append({
                            'campo': campo,
                            'gravado': gravado,
                            'lido_agora': novo,
                        })
            conn.execute('UPDATE pericias_processos SET %s WHERE id = ?'
                         % ', '.join(f'{k} = ?' for k in livres),
                         tuple(campos[k] for k in livres) + (processo_id,))
            conn.execute('DELETE FROM pericias_pecas WHERE processo_id = ?', (processo_id,))
        else:
            cols = list(campos.keys())
            cur = conn.execute(
                'INSERT INTO pericias_processos (%s) VALUES (%s)'
                % (', '.join(cols), ', '.join(['?'] * len(cols))),
                tuple(campos[c] for c in cols))
            processo_id = cur.lastrowid
            reaproveitado = False

        for p in dados['pecas']:
            conn.execute(
                'INSERT INTO pericias_pecas (processo_id, ordem, tipo_pje, categoria, '
                'data_juntada, pag_ini, pag_fim, paginas, densidade, escaneada, '
                'texto_integral, texto) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (processo_id, p['ordem'], p['tipo_pje'], p['categoria'], p['data_juntada'],
                 p['pag_ini'], p['pag_fim'], p['paginas'], p['densidade'],
                 1 if p['escaneada'] else 0, 1 if p['texto_integral'] else 0, p['texto']))

    if divergencias:
        rotulo = dict(PRAZOS)
        rotulo['modalidades'] = 'Modalidade'
        rotulo['perito_nomeado'] = 'Perito nomeado'
        ext['avisos'].insert(0,
            'Este processo já estava revisado, então a leitura nova NÃO substituiu o que '
            'você confirmou. Divergiu em: %s. Ajuste à mão se a mudança for real.'
            % '; '.join('%s (gravado "%s", nos autos agora "%s")'
                        % (rotulo.get(d['campo'], d['campo']), d['gravado'] or '—',
                           d['lido_agora']) for d in divergencias))

    return jsonify({
        'ok': True, 'processo_id': processo_id, 'reaproveitado': reaproveitado,
        'cabecalho': cab, 'extracao': ext, 'divergencias': divergencias,
        'pecas_por_categoria': _conta_categorias(dados['pecas']),
        'aviso_revisao': 'Confira modalidade, perito e prazos antes de usar. '
                         'A ata muda de formato entre varas.',
    })


def _conta_categorias(pecas):
    fora = {}
    for p in pecas:
        fora[p['categoria']] = fora.get(p['categoria'], 0) + 1
    return fora


# ── Processos ──────────────────────────────────────────────────────────
@pericias_bp.route('/processos')
def listar_processos():
    init_pericias()
    status = (request.args.get('status') or '').strip()
    busca = (request.args.get('q') or '').strip()
    sql = 'SELECT * FROM pericias_processos WHERE 1=1'
    args = []
    if status in STATUS:
        sql += ' AND status = ?'
        args.append(status)
    if busca:
        sql += (' AND (COALESCE(numero_cnj,\'\') LIKE ? OR UPPER(COALESCE(reclamante,\'\')) '
                'LIKE ? OR UPPER(COALESCE(reclamada,\'\')) LIKE ?)')
        alvo = '%' + busca.upper() + '%'
        args += [alvo, alvo, alvo]
    sql += ' ORDER BY CASE WHEN prazo_quesitos IS NULL THEN 1 ELSE 0 END, prazo_quesitos, id DESC'
    with get_db() as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()
    return jsonify({'total': len(rows), 'processos': [_processo_dict(r) for r in rows]})


@pericias_bp.route('/processos/<int:pid>')
def detalhe_processo(pid):
    init_pericias()
    with get_db() as conn:
        row = conn.execute('SELECT * FROM pericias_processos WHERE id = ?', (pid,)).fetchone()
        if not row:
            return jsonify({'erro': 'Processo não encontrado'}), 404
        pecas = conn.execute(
            'SELECT id, ordem, tipo_pje, categoria, data_juntada, pag_ini, pag_fim, '
            'paginas, densidade, escaneada, texto_integral, LENGTH(texto) AS chars '
            'FROM pericias_pecas WHERE processo_id = ? ORDER BY pag_ini', (pid,)).fetchall()
        rol = conn.execute('SELECT COUNT(*) AS c FROM pericias_rol WHERE processo_id = ?',
                           (pid,)).fetchone()
    d = _processo_dict(row)
    d['pecas'] = [row_to_dict(p) for p in pecas]
    d['pecas_por_categoria'] = _conta_categorias(
        [{'categoria': p['categoria']} for p in d['pecas']])
    d['quesitos_no_rol'] = row_to_dict(rol)['c'] if rol else 0
    d['prazos_rotulo'] = [{'chave': k, 'rotulo': r, 'data': d.get(k),
                           'dias': _dias_para(d.get(k))} for k, r in PRAZOS]
    return jsonify(d)


@pericias_bp.route('/processos/<int:pid>/pecas/<int:peca_id>')
def texto_peca(peca_id, pid):
    init_pericias()
    with get_db() as conn:
        row = conn.execute('SELECT * FROM pericias_pecas WHERE id = ? AND processo_id = ?',
                           (peca_id, pid)).fetchone()
    if not row:
        return jsonify({'erro': 'Peça não encontrada'}), 404
    d = row_to_dict(row)
    if not d.get('texto_integral'):
        d['aviso'] = ('Esta peça não é usada no fluxo da perícia, então só a amostra '
                      'de 3 páginas foi lida. Para o texto completo, reenvie os autos '
                      'marcando esta categoria.')
    if d.get('escaneada'):
        d['aviso_ocr'] = ('Peça digitalizada (%d caracteres por página). O texto abaixo é '
                          'só o cabeçalho do PJe — o conteúdo exige OCR.'
                          % (d.get('densidade') or 0))
    return jsonify(d)


@pericias_bp.route('/processos/<int:pid>/confirmar', methods=['POST'])
def confirmar_processo(pid):
    """Gate humano. Grava o que a pessoa revisou e tira do rascunho.

    Aceita correcao de qualquer campo lido da ata. Sem isso o processo fica em
    rascunho para sempre — de proposito.
    """
    init_pericias()
    body = request.get_json(silent=True) or {}
    editaveis = ['numero_cnj', 'reclamante', 'reclamada', 'trt', 'vara', 'comarca',
                 'rito', 'juiz', 'data_audiencia', 'perito_nomeado', 'observacao',
                 'empresa_id', 'resultado'] + [k for k, _ in PRAZOS]
    sets, vals = [], []
    for campo in editaveis:
        if campo in body:
            sets.append(f'{campo} = ?')
            vals.append(body[campo] if body[campo] not in ('',) else None)
    if 'modalidades' in body:
        mods = body['modalidades']
        sets.append('modalidades = ?')
        vals.append(_csv(mods if isinstance(mods, list) else _lista(mods)))

    novo_status = body.get('status') or 'confirmado'
    if novo_status not in STATUS:
        return jsonify({'erro': 'Status inválido'}), 400

    with get_db() as conn:
        atual = conn.execute('SELECT id, modalidades FROM pericias_processos WHERE id = ?',
                             (pid,)).fetchone()
        if not atual:
            return jsonify({'erro': 'Processo não encontrado'}), 404
        mods_final = body.get('modalidades')
        if mods_final is None:
            mods_final = _lista(row_to_dict(atual).get('modalidades'))
        elif not isinstance(mods_final, list):
            mods_final = _lista(mods_final)
        if novo_status != 'rascunho' and not mods_final:
            return jsonify({'erro': 'Informe a modalidade da perícia antes de confirmar.'}), 400

        sets += ['status = ?', 'revisado_em = ?', 'revisado_por = ?']
        vals += [novo_status, _agora(), _quem()]
        conn.execute('UPDATE pericias_processos SET %s WHERE id = ?' % ', '.join(sets),
                     tuple(vals) + (pid,))
        row = conn.execute('SELECT * FROM pericias_processos WHERE id = ?', (pid,)).fetchone()
    return jsonify({'ok': True, 'processo': _processo_dict(row)})


@pericias_bp.route('/processos/<int:pid>', methods=['DELETE'])
def excluir_processo(pid):
    init_pericias()
    if getattr(current_user, 'role', '') != 'admin':
        return jsonify({'erro': 'Apenas administradores'}), 403
    with get_db() as conn:
        conn.execute('DELETE FROM pericias_rol WHERE processo_id = ?', (pid,))
        conn.execute('DELETE FROM pericias_pecas WHERE processo_id = ?', (pid,))
        conn.execute('DELETE FROM pericias_processos WHERE id = ?', (pid,))
    return jsonify({'ok': True})


# ── Catalogo de quesitos ───────────────────────────────────────────────
@pericias_bp.route('/catalogo')
def listar_catalogo():
    """Busca no catalogo. `nucleo=1` traz so o que se repete em 5+ processos —
    é o bloco que entra em quase toda peca e nao precisa de decisao."""
    init_pericias()
    materia = (request.args.get('materia') or '').strip()
    busca = (request.args.get('q') or '').strip()
    nucleo = request.args.get('nucleo') in ('1', 'true', 'sim')
    try:
        limite = min(500, max(1, int(request.args.get('limite') or 100)))
    except (TypeError, ValueError):
        limite = 100

    sql = 'SELECT * FROM pericias_quesitos WHERE ativo = 1'
    args = []
    if materia:
        sql += ' AND materia = ?'
        args.append(materia)
    if nucleo:
        sql += ' AND ocorrencias >= 5'
    if busca:
        sql += ' AND UPPER(texto) LIKE ?'
        args.append('%' + busca.upper() + '%')
    sql += ' ORDER BY ocorrencias DESC, id LIMIT %d' % limite
    with get_db() as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()
        resumo = conn.execute(
            'SELECT materia, COUNT(*) AS n, '
            'SUM(CASE WHEN ocorrencias >= 5 THEN 1 ELSE 0 END) AS nucleo '
            'FROM pericias_quesitos WHERE ativo = 1 GROUP BY materia ORDER BY n DESC'
        ).fetchall()
    return jsonify({
        'total': len(rows),
        'quesitos': [row_to_dict(r) for r in rows],
        'por_materia': [row_to_dict(r) for r in resumo],
    })


@pericias_bp.route('/catalogo', methods=['POST'])
def criar_quesito():
    """Quesito escrito a mao entra no catalogo com origem='manual'."""
    init_pericias()
    body = request.get_json(silent=True) or {}
    texto = (body.get('texto') or '').strip()
    materia = (body.get('materia') or '').strip() or 'insalubridade'
    if len(texto) < 20:
        return jsonify({'erro': 'Texto do quesito muito curto.'}), 400
    import hashlib
    chave = re.sub(r'\s+', ' ', texto.lower()).strip()
    h = hashlib.md5(chave.encode('utf-8')).hexdigest()[:12]
    with get_db() as conn:
        ja = conn.execute('SELECT id FROM pericias_quesitos WHERE hash = ?', (h,)).fetchone()
        if ja:
            return jsonify({'ok': True, 'id': row_to_dict(ja)['id'], 'ja_existia': True})
        cur = conn.execute(
            'INSERT INTO pericias_quesitos (hash, materia, texto, ocorrencias, origem, '
            'criado_por) VALUES (?,?,?,?,?,?)',
            (h, materia, texto, 1, 'manual', _quem()))
        novo = cur.lastrowid
    return jsonify({'ok': True, 'id': novo, 'ja_existia': False})


@pericias_bp.route('/catalogo/importar', methods=['POST'])
def importar_catalogo():
    """Carrega data/quesitos_catalogo.json no banco. Admin-only, idempotente:
    quesito ja existente tem a frequencia atualizada, nao duplica."""
    init_pericias()
    caminho = CATALOGO_SEED
    if not os.path.exists(caminho):
        return jsonify({'erro': 'Arquivo de catálogo não encontrado em %s' % caminho}), 404
    with open(caminho, encoding='utf-8') as fh:
        pacote = json.load(fh)
    novos = atualizados = 0
    with get_db() as conn:
        for q in pacote.get('quesitos') or []:
            h, texto = q.get('hash'), (q.get('texto') or '').strip()
            if not h or len(texto) < 20:
                continue
            ja = conn.execute('SELECT id FROM pericias_quesitos WHERE hash = ?',
                              (h,)).fetchone()
            if ja:
                conn.execute('UPDATE pericias_quesitos SET ocorrencias = ?, materia = ? '
                             'WHERE id = ?',
                             (q.get('ocorrencias') or 1, q.get('materia'),
                              row_to_dict(ja)['id']))
                atualizados += 1
            else:
                conn.execute(
                    'INSERT INTO pericias_quesitos (hash, materia, texto, ocorrencias, '
                    'origem, criado_por) VALUES (?,?,?,?,?,?)',
                    (h, q.get('materia'), texto, q.get('ocorrencias') or 1,
                     'catalogo', _quem()))
                novos += 1
    return jsonify({'ok': True, 'novos': novos, 'atualizados': atualizados,
                    'arquivo': os.path.basename(caminho)})


# ── Rol de quesitos do processo ────────────────────────────────────────
@pericias_bp.route('/processos/<int:pid>/rol')
def ler_rol(pid):
    init_pericias()
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM pericias_rol WHERE processo_id = ? '
                            'ORDER BY ordem, id', (pid,)).fetchall()
    return jsonify({'total': len(rows), 'rol': [row_to_dict(r) for r in rows]})


@pericias_bp.route('/processos/<int:pid>/rol', methods=['PUT'])
def salvar_rol(pid):
    """Substitui o rol do processo pela lista enviada. A ordem do array e a
    ordem que sai na peca."""
    init_pericias()
    body = request.get_json(silent=True) or {}
    itens = body.get('rol')
    if not isinstance(itens, list):
        return jsonify({'erro': 'Envie "rol" como lista.'}), 400

    limpos = []
    for i, item in enumerate(itens):
        if isinstance(item, dict):
            texto = (item.get('texto') or '').strip()
            qid = item.get('quesito_id')
            materia = (item.get('materia') or '').strip() or None
            origem = (item.get('origem') or ('catalogo' if qid else 'manual')).strip()
        else:
            texto, qid, materia, origem = str(item).strip(), None, None, 'manual'
        if len(texto) < 10:
            continue
        limpos.append((pid, qid, i + 1, materia, texto, origem, _quem()))

    with get_db() as conn:
        existe = conn.execute('SELECT id FROM pericias_processos WHERE id = ?',
                              (pid,)).fetchone()
        if not existe:
            return jsonify({'erro': 'Processo não encontrado'}), 404
        conn.execute('DELETE FROM pericias_rol WHERE processo_id = ?', (pid,))
        for reg in limpos:
            conn.execute(
                'INSERT INTO pericias_rol (processo_id, quesito_id, ordem, materia, '
                'texto, origem, criado_por) VALUES (?,?,?,?,?,?,?)', reg)
    return jsonify({'ok': True, 'total': len(limpos)})


@pericias_bp.route('/processos/<int:pid>/rol/sugerir')
def sugerir_rol(pid):
    """Sugestao SEM IA: o nucleo do catalogo nas materias da pericia.

    61% dos quesitos das pecas ja entregues sao repeticao literal — esse bloco
    e catalogo, nao criacao. A parte caso-especifica (o que a inicial alegou,
    qual agente, qual setor) continua sendo trabalho do perito; a IA entra
    depois, na onda seguinte, e so nessa parte.
    """
    init_pericias()
    with get_db() as conn:
        row = conn.execute('SELECT * FROM pericias_processos WHERE id = ?', (pid,)).fetchone()
        if not row:
            return jsonify({'erro': 'Processo não encontrado'}), 404
        proc = _processo_dict(row)
        mods = proc['modalidades'] or ['insalubridade']
        marcadores = ','.join(['?'] * len(mods))
        sugeridos = conn.execute(
            'SELECT * FROM pericias_quesitos WHERE ativo = 1 AND ocorrencias >= 5 '
            'AND materia IN (%s) ORDER BY ocorrencias DESC, id' % marcadores,
            tuple(mods)).fetchall()
        # Materias deferidas sem nada no catalogo — o time precisa saber ANTES
        # de montar a peca, nao depois.
        com_base = {row_to_dict(r)['materia'] for r in conn.execute(
            'SELECT DISTINCT materia FROM pericias_quesitos WHERE ativo = 1').fetchall()}
    faltando = [MODALIDADE_LABEL.get(m, m) for m in mods if m not in com_base]
    return jsonify({
        'modalidades': proc['modalidades'],
        'total': len(sugeridos),
        'sugeridos': [row_to_dict(r) for r in sugeridos],
        'materias_sem_catalogo': faltando,
        'aviso': ('Sem quesito no catálogo para: %s. Escrever à mão nesta perícia.'
                  % ', '.join(faltando)) if faltando else None,
    })


# ── Geracao da peca em .docx ───────────────────────────────────────────
def _esc(txt):
    return (str(txt or '').replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;'))


def _par(texto, negrito=False, centro=False, tamanho=22, espaco=160):
    """Paragrafo docx com formatacao direta (sem styles.xml)."""
    rpr = '<w:rPr>%s<w:sz w:val="%d"/><w:szCs w:val="%d"/></w:rPr>' % (
        '<w:b/>' if negrito else '', tamanho, tamanho)
    ppr = '<w:pPr>%s<w:spacing w:after="%d"/><w:jc w:val="%s"/></w:pPr>' % (
        '', espaco, 'center' if centro else 'both')
    return '<w:p>%s<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r></w:p>' % (
        ppr, rpr, _esc(texto))


def _docx_bytes(paragrafos):
    """Monta um .docx minimo valido. O projeto ja gera docx por manipulacao de
    XML em zip (laudos de calor/ruido/quimico); aqui o documento nasce do zero
    porque nao existe template de peca de quesitos."""
    corpo = ''.join(paragrafos)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>%s<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1418" w:right="1134" w:bottom="1418" w:left="1701"/>'
        '</w:sectPr></w:body></w:document>' % corpo)
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
        '2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', content_types)
        z.writestr('_rels/.rels', rels)
        z.writestr('word/document.xml', document)
    buf.seek(0)
    return buf


@pericias_bp.route('/processos/<int:pid>/rol.docx')
def baixar_rol_docx(pid):
    """Peca de quesitos em .docx, no formato que o jurídico do cliente recebe.

    A numeracao sai LITERAL no texto ("01- ..."), nao como lista automatica do
    Word: os arquivos antigos usavam numeracao automatica e, quando o texto era
    lido de volta por qualquer ferramenta, o numero simplesmente nao existia.
    """
    init_pericias()
    with get_db() as conn:
        row = conn.execute('SELECT * FROM pericias_processos WHERE id = ?', (pid,)).fetchone()
        if not row:
            return jsonify({'erro': 'Processo não encontrado'}), 404
        rol = conn.execute('SELECT * FROM pericias_rol WHERE processo_id = ? '
                           'ORDER BY ordem, id', (pid,)).fetchall()
    proc = _processo_dict(row)
    if not rol:
        return jsonify({'erro': 'O rol de quesitos está vazio.'}), 400

    vara = ('%s VARA DO TRABALHO DE %s' % (proc.get('vara') or '', (proc.get('comarca') or ''))
            ).strip().upper() or 'VARA DO TRABALHO'
    ps = [
        _par('EXMO. SR. DR. JUIZ DO TRABALHO DA %s' % vara, negrito=True),
        _par(''),
        _par('PROCESSO Nº: %s' % (proc.get('numero_cnj') or ''), negrito=True),
        _par('RECLAMANTE: %s' % (proc.get('reclamante') or '')),
        _par('RECLAMADA: %s' % (proc.get('reclamada') or '')),
        _par(''),
        _par('A reclamada, já qualificada nos autos do processo em epígrafe, vem, '
             'respeitosamente, à presença de Vossa Excelência, em cumprimento ao '
             'determinado em Ata de Audiência, apresentar seus quesitos técnicos e '
             'indicar assistente técnico para acompanhamento da perícia designada.'),
        _par('Nos termos dos artigos 466, § 2º, e 474 do Código de Processo Civil, requer '
             'seja o assistente técnico previamente comunicado da data, horário e local '
             'da diligência pericial.'),
        _par(''),
    ]
    if proc.get('perito_nomeado'):
        ps.append(_par('Perito nomeado: %s' % proc['perito_nomeado']))
        ps.append(_par(''))

    ordem_materia, atual, n = [], None, 0
    for r in rol:
        item = row_to_dict(r)
        mat = item.get('materia') or (proc['modalidades'][0] if proc['modalidades'] else None)
        if mat != atual:
            atual = mat
            ordem_materia.append(mat)
            ps.append(_par(MODALIDADE_LABEL.get(mat, mat or 'QUESITOS').upper(),
                           negrito=True, centro=True))
        n += 1
        ps.append(_par('%02d- %s' % (n, item['texto'])))

    ps += [
        _par(''),
        _par('Sendo estas as considerações a fazer nesta fase de instrução processual, '
             'requer a reclamada a juntada destes aos autos.'),
        _par('Termos em que, pede deferimento.'),
        _par(''),
        _par('%s, %s.' % ((proc.get('comarca') or 'Belo Horizonte').title(),
                          date.today().strftime('%d/%m/%Y')), centro=True),
    ]

    nome = 'Quesitos - %s - %s.docx' % (
        (proc.get('numero_cnj') or 'processo').replace('.', '-'),
        (proc.get('reclamante') or '').split(' ')[0].title() or 'reclamante')
    return send_file(_docx_bytes(ps), as_attachment=True, download_name=nome,
                     mimetype='application/vnd.openxmlformats-officedocument.'
                              'wordprocessingml.document')


# ── Painel ─────────────────────────────────────────────────────────────
@pericias_bp.route('/stats')
def stats():
    """Numeros para o card da home e o topo da aba."""
    init_pericias()
    with get_db() as conn:
        tot = row_to_dict(conn.execute(
            'SELECT COUNT(*) AS processos, '
            "SUM(CASE WHEN status='rascunho' THEN 1 ELSE 0 END) AS rascunhos "
            'FROM pericias_processos').fetchone())
        cat = row_to_dict(conn.execute(
            'SELECT COUNT(*) AS quesitos, '
            'SUM(CASE WHEN ocorrencias >= 5 THEN 1 ELSE 0 END) AS nucleo '
            'FROM pericias_quesitos WHERE ativo = 1').fetchone())
        prox = conn.execute(
            'SELECT id, numero_cnj, reclamante, reclamada, prazo_quesitos, status '
            'FROM pericias_processos WHERE prazo_quesitos IS NOT NULL '
            "AND status IN ('rascunho','confirmado') "
            'ORDER BY prazo_quesitos LIMIT 8').fetchall()
    proximos = []
    for r in prox:
        d = row_to_dict(r)
        d['dias'] = _dias_para(d.get('prazo_quesitos'))
        proximos.append(d)
    return jsonify({
        'processos': tot.get('processos') or 0,
        'rascunhos': tot.get('rascunhos') or 0,
        'quesitos_catalogo': cat.get('quesitos') or 0,
        'quesitos_nucleo': cat.get('nucleo') or 0,
        'vencendo': [p for p in proximos if p['dias'] is not None and p['dias'] <= 7],
        'proximos_prazos': proximos,
    })
