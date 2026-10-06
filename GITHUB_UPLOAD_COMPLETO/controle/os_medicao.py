# -*- coding: utf-8 -*-
"""OS de MEDIÇÃO que chega do CRM pronta (padrão de 06/10/2026).

Decisão do Matheus (06/10/2026): OS de medição não passa pela distribuição do
Luiz e da Valéria no Portal Interno; sai do CRM e cai direto aqui, com cada
agente e a quantidade definidos. O padrão completo está em
`Downloads\\Padrao da OS de medicao (CRM para Portal de Medicoes) 06-10-2026.md`.

Nada aqui lê texto: o produto do catálogo do CRM vira agente pela tabela fixa
(`catalogo_medicao.py`) e a quantidade vem como veio. A demanda nasce com
`agentes_manual` (vence a extração automática e sobrevive ao re-extrair) e com
as linhas de `medicoes` (que a previsão de estoque lê).

Rotas (auth server-to-server pelo `x-crm-secret`, ver `_require_login`):
  POST /controle/os/medicao                    recebe (ou, com teste=true, só confere)
  GET  /controle/os/medicao/situacao?origem_id  andamento para o CRM mostrar
"""
import json
import logging
import os
from datetime import date, timedelta

from flask import jsonify, request

from .catalogo_medicao import resolver_produto
from .db import data_iso, get_db, registrar_evento, row_to_dict

log = logging.getLogger(__name__)

URGENCIAS = {'padrao': 10, '30': 7, '50': 5, '70': 3}   # dias úteis para executar


def _dias_uteis(n, a_partir=None):
    d = a_partir or date.today()
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _txt(v, limite=500):
    return str(v).strip()[:limite] if v not in (None, '') else ''


def _quantidade(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f >= 1 and f == int(f) else None


def validar(p):
    """Confere a OS contra o padrão. Devolve (erros, itens, avisos, dias_campo)."""
    erros, itens, avisos, dias_campo = [], [], [], 0

    def erro(campo, msg):
        erros.append({'campo': campo, 'mensagem': msg})

    if not _txt(p.get('origem_id')):
        erro('origem_id', 'Informe o id do negócio (origem_id).')
    emp = p.get('empresa') or {}
    if not _txt(emp.get('nome')):
        erro('empresa.nome', 'Informe o nome da empresa.')
    local = p.get('local') or {}
    if not _txt(local.get('cidade')) or not _txt(local.get('uf')):
        erro('local', 'Informe a cidade e a UF do local da medição.')
    contato = p.get('contato_local') or {}
    if not _txt(contato.get('nome')) or not (_txt(contato.get('telefone')) or _txt(contato.get('email'))):
        erro('contato_local', 'Informe o contato no local: nome e telefone ou e-mail.')
    urg = _txt(p.get('urgencia')) or 'padrao'
    if urg not in URGENCIAS:
        erro('urgencia', 'Urgência deve ser padrao, 30, 50 ou 70.')
    if _txt(p.get('prazo')) and not data_iso(p.get('prazo')):
        erro('prazo', 'Prazo inválido (use AAAA-MM-DD).')

    brutos = p.get('itens') or []
    if not isinstance(brutos, list) or not brutos:
        erro('itens', 'A OS de medição precisa de pelo menos um item.')
        brutos = []
    for i, it in enumerate(brutos):
        nome = _txt((it or {}).get('produto'), 300)
        qtd = _quantidade((it or {}).get('quantidade'))
        r = resolver_produto(nome)
        campo = f'itens[{i}]'
        if not r:
            erro(campo, f'"{nome}" não está na tabela de produtos de medição.')
            continue
        if qtd is None:
            erro(campo, f'{r["produto"]}: a quantidade (pontos) tem de ser um número inteiro a partir de 1.')
            continue
        if r['situacao'] == 'generico':
            erro(campo, f'"{r["produto"]}" não diz o que medir: especifique os agentes e os pontos.')
            continue
        if r['situacao'] == 'logistica':
            dias_campo += qtd
            continue
        if r['situacao'] == 'confirmar':
            avisos.append(f'{r["produto"]}: entra como "{r["agente"]}"; o técnico confere a forma no planejamento.')
        if r['situacao'] == 'sem_metodo':
            avisos.append(f'{r["produto"]}: sem método no guia; o técnico confirma com o laboratório.')
        itens.append({**r, 'quantidade': qtd, 'observacao': _txt((it or {}).get('observacao'), 300)})
    if brutos and not itens and not any(e['campo'].startswith('itens[') for e in erros):
        erro('itens', 'Nenhum item de medição (a diária sozinha não é medição).')
    return erros, itens, avisos, dias_campo


def avisos_de_estoque(itens):
    """Para cada item químico, confere se há tubo do método em estoque para os
    pontos pedidos (06/10/2026). Hoje 14 tipos de tubo do catálogo nunca
    estiveram no inventário (formaldeído com 11 pontos pendentes) e o técnico
    descobria na véspera da visita. O laboratório leva ~7 dias para mandar."""
    from .routes import _buscar_metodos_agente, _extrair_tipos_amostrador
    quimicos = [it for it in itens if it.get('tipo') in ('quimico', 'particulado')]
    if not quimicos:
        return []
    with get_db() as conn:
        estoque = {(row_to_dict(r).get('tipo') or '').upper(): row_to_dict(r)['n'] for r in conn.execute(
            "SELECT tipo, COUNT(*) AS n FROM amostradores WHERE status='disponivel' "
            "AND COALESCE(arquivado,0)=0 GROUP BY tipo").fetchall()}
    out = []
    for it in quimicos:
        tipos = sorted({t.upper() for m in _buscar_metodos_agente(it['agente'])
                        for t in _extrair_tipos_amostrador(m.get('amostradorCod', ''))})
        if not tipos:
            continue
        tem = sum(estoque.get(t, 0) for t in tipos)
        if tem < it['quantidade']:
            out.append(f'Estoque: {it["produto"]} pede {it["quantidade"]} tubo(s) '
                       f'({" ou ".join(tipos)}) e há {tem}. Pedir ao laboratório (leva ~7 dias).')
    return out


def avisos_de_duplicidade(p):
    """Transição (até o Ploomes sair e o MAESTRO parar): a mesma OS pode chegar
    pelo Planner e pelo CRM. Mesmo nº MAESTRO numa demanda do Planner em aberto
    = revisão; mesma empresa com OS de medição aberta no Planner = aviso.
    Devolve (avisos, revisar)."""
    emp = p.get('empresa') or {}
    cnpj, nome = _txt(emp.get('cnpj'), 20), _txt(emp.get('nome'), 300)
    maestro = _txt(p.get('numero_maestro'), 20)
    avisos, revisar = [], 0
    with get_db() as conn:
        if maestro:
            r = conn.execute("SELECT id FROM demandas WHERE origem='planner' AND numero_os=? "
                             "AND status!='concluida' LIMIT 1", (maestro,)).fetchone()
            if r:
                avisos.append(f'A OS {maestro} também chegou pelo Planner (demanda #{row_to_dict(r)["id"]}). '
                              f'Confira para não medir duas vezes.')
                revisar = 1
        if not revisar and (cnpj or nome):
            rows = conn.execute(
                "SELECT d.id, d.numero_os FROM demandas d JOIN empresas e ON e.id=d.empresa_id "
                "WHERE d.origem='planner' AND d.status IN ('aberta','em_andamento','pendente') "
                "AND ((? <> '' AND e.cnpj=?) OR e.nome=?) ORDER BY d.id DESC LIMIT 3",
                (cnpj, cnpj, nome)).fetchall()
            if rows:
                lista = ', '.join(f'#{row_to_dict(x)["id"]} (OS {row_to_dict(x)["numero_os"] or "sem nº"})' for x in rows)
                avisos.append(f'Esta empresa já tem OS de medição aberta pelo Planner: {lista}. '
                              f'Confira se não é a mesma.')
    return avisos, revisar


def _prazo(p):
    if data_iso(p.get('prazo')):
        return data_iso(p.get('prazo'))
    return _dias_uteis(URGENCIAS[_txt(p.get('urgencia')) or 'padrao']).isoformat()


def _descricao(p, itens, avisos, dias_campo, prazo):
    loc, ct = p.get('local') or {}, p.get('contato_local') or {}
    end = ', '.join(x for x in [_txt(loc.get('endereco')), _txt(loc.get('cidade')) + '/' + _txt(loc.get('uf'))] if x)
    urg = _txt(p.get('urgencia')) or 'padrao'
    linhas = ['OS de medição aberta pelo CRM.',
              f'Local: {_txt(loc.get("unidade")) or "sede"}, {end}',
              'Contato no local: ' + ' · '.join(x for x in [_txt(ct.get('nome')), _txt(ct.get('telefone')),
                                                          _txt(ct.get('email'))] if x),
              f'Prazo: {prazo}' + ('' if urg == 'padrao' else f' (urgência {urg}%)')]
    if dias_campo:
        linhas.append(f'Dias de campo: {dias_campo}')
    linhas.append('Itens:')
    linhas += [f'- {it["produto"]} x{it["quantidade"]}' + (f' ({it["observacao"]})' if it['observacao'] else '')
               for it in itens]
    info = p.get('informacoes_campo')
    if info:
        linhas.append('Informações de campo: ' + (json.dumps(info, ensure_ascii=False) if isinstance(info, dict) else _txt(info, 2000)))
    if _txt(p.get('observacoes')):
        linhas.append('Observações: ' + _txt(p.get('observacoes'), 3000))
    if avisos:
        linhas.append('Conferir: ' + ' | '.join(avisos))
    return '\n'.join(linhas)


def _iniciada(conn, demanda_id):
    for tabela in ('coletas_ruido', 'coletas_quimico', 'coletas_outros', 'visitas_tecnicas'):
        try:
            r = conn.execute(f'SELECT 1 FROM {tabela} WHERE demanda_id=? LIMIT 1', (demanda_id,)).fetchone()
        except Exception:
            continue
        if r:
            return True
    return False


def receber(p, teste=False):
    """Recebe a OS de medição. Devolve (corpo, status_http)."""
    erros, itens, avisos, dias_campo = validar(p)
    if erros:
        return {'ok': False, 'erro': 'A OS de medição não está no padrão.', 'campos': erros}, 400
    try:
        avisos += avisos_de_estoque(itens)
    except Exception as e:   # aviso nunca derruba a OS
        log.warning('[os_medicao] conferência de estoque falhou: %s', e)
    revisar_dup = 0
    try:
        av_dup, revisar_dup = avisos_de_duplicidade(p)
        avisos += av_dup
    except Exception as e:
        log.warning('[os_medicao] conferência de duplicidade falhou: %s', e)
    resumo = [{'produto': it['produto'], 'agente': it['agente'], 'quantidade': it['quantidade'],
               'situacao': it['situacao']} for it in itens]
    if teste:
        return {'ok': True, 'teste': True, 'itens': resumo, 'avisos': avisos, 'dias_campo': dias_campo}, 200

    from .orquestrador import casar_ou_criar_empresa
    origem_ref = 'crm:' + _txt(p.get('origem_id'), 200)
    emp = p.get('empresa') or {}
    nome_emp = _txt(emp.get('nome'), 300)
    cnpj = _txt(emp.get('cnpj'), 20) or None
    numero = _txt(p.get('numero_os'), 60) or _txt(p.get('numero_maestro'), 20) or None
    unidade = _txt((p.get('local') or {}).get('unidade'), 200)
    titulo = ' - '.join(x for x in [numero, nome_emp, unidade] if x)
    prazo = _prazo(p)
    desc = _descricao(p, itens, avisos, dias_campo, prazo)
    agentes = json.dumps([{'tipo': it['tipo'], 'qtd': it['quantidade'], 'texto': it['agente']} for it in itens],
                         ensure_ascii=False)
    checklist = json.dumps([it['produto'] for it in itens], ensure_ascii=False)
    dados = json.dumps({**p, 'dias_campo': dias_campo}, ensure_ascii=False, default=str)

    with get_db() as conn:
        ex = conn.execute("SELECT id FROM demandas WHERE origem='crm_os' AND origem_ref=? "
                          "ORDER BY id DESC LIMIT 1", (origem_ref,)).fetchone()
        if ex:
            did = row_to_dict(ex)['id']
            if _iniciada(conn, did):
                return {'ok': False, 'demanda_id': did,
                        'erro': 'A medição desta OS já começou (há coleta registrada). A alteração não foi '
                                'aplicada: combine com o técnico.'}, 409
            conn.execute("UPDATE demandas SET numero_os=COALESCE(?, numero_os), titulo=?, nome_tarefa=?, "
                         "descricao=?, checklist=?, agentes_manual=?, dados_os=?, prazo=?, atualizado_em=? "
                         "WHERE id=?",
                         (numero, titulo, titulo, desc, checklist, agentes, dados, prazo,
                          _agora(), did))
            conn.execute('DELETE FROM medicoes WHERE demanda_id=?', (did,))
            criada = False
            tem_plano = conn.execute('SELECT 1 FROM planejamentos WHERE demanda_id=? LIMIT 1', (did,)).fetchone()
            if tem_plano:
                avisos.append('Já existe planejamento para esta OS: confira as linhas com os itens novos.')
        else:
            empresa_id, metodo, score, revisar = casar_ou_criar_empresa(conn, cnpj, nome_emp, numero or '')
            conn.execute(
                "INSERT INTO demandas (numero_os, empresa_id, cnpj, titulo, nome_tarefa, descricao, checklist, "
                "agentes_manual, dados_os, origem_ref, status, origem, tipo_demanda, prazo, "
                "empresa_match_score, empresa_match_metodo, needs_review, criado_em, atualizado_em) "
                "VALUES (?,?,?,?,?,?,?,?,?,?, 'pendente', 'crm_os', 'operacional', ?, ?,?,?, ?, ?)",
                (numero, empresa_id, cnpj, titulo, titulo, desc, checklist, agentes, dados, origem_ref,
                 prazo, score, metodo, 1 if (revisar or revisar_dup) else 0, _agora(), _agora()))
            did = row_to_dict(conn.execute("SELECT id FROM demandas WHERE origem_ref=? ORDER BY id DESC LIMIT 1",
                                           (origem_ref,)).fetchone())['id']
            criada = True
        for it in itens:
            conn.execute("INSERT INTO medicoes (demanda_id, agente, qtd_pontos_prevista, qtd_pontos_feita, "
                         "status, observacao) VALUES (?,?,?,0,'pendente',?)",
                         (did, it['agente'], it['quantidade'], it['observacao'] or None))
    try:
        registrar_evento('os_medicao_crm', f'{"Criada" if criada else "Atualizada"}: {titulo} '
                         f'({len(itens)} itens)', did, 'demanda', 'crm', request.remote_addr if request else None)
    except Exception as e:
        log.warning('[os_medicao] evento falhou: %s', e)
    return {'ok': True, 'demanda_id': did, 'criada': criada, 'numero_os': numero, 'prazo': prazo,
            'itens': resumo, 'avisos': avisos, 'dias_campo': dias_campo}, (201 if criada else 200)


def situacao(origem_id):
    """Andamento da OS de medição para o CRM mostrar."""
    ref = 'crm:' + _txt(origem_id, 200)
    with get_db() as conn:
        d = conn.execute("SELECT id, numero_os, status, prazo, data_conclusao FROM demandas "
                         "WHERE origem='crm_os' AND origem_ref=? ORDER BY id DESC LIMIT 1", (ref,)).fetchone()
        if not d:
            return {'encontrada': False}
        d = row_to_dict(d)
        plano = conn.execute("SELECT status, data_prevista, tecnico FROM planejamentos WHERE demanda_id=? "
                             "ORDER BY id DESC LIMIT 1", (d['id'],)).fetchone()
        coletas = 0
        for tabela in ('coletas_ruido', 'coletas_quimico', 'coletas_outros'):
            try:
                coletas += row_to_dict(conn.execute(f'SELECT COUNT(*) AS c FROM {tabela} WHERE demanda_id=?',
                                                    (d['id'],)).fetchone())['c'] or 0
            except Exception:
                pass
    plano = row_to_dict(plano) if plano else None
    if d['status'] == 'concluida':
        etapa = 'concluida'
    elif coletas:
        etapa = 'em_campo'
    elif plano:
        etapa = 'planejada'
    else:
        etapa = 'recebida'
    return {'encontrada': True, 'demanda_id': d['id'], 'numero_os': d['numero_os'], 'etapa': etapa,
            'prazo': d['prazo'], 'data_prevista': (plano or {}).get('data_prevista'),
            'tecnico': (plano or {}).get('tecnico'), 'coletas': coletas,
            'concluida_em': d.get('data_conclusao')}


def _agora():
    from datetime import datetime
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def registrar_rotas(bp):
    def _autorizado():
        segredo = os.environ.get('CRM_PLANNER_SECRET', '')
        return bool(segredo) and request.headers.get('x-crm-secret') == segredo

    @bp.route('/os/medicao', methods=['POST'])
    def os_medicao_receber():
        if not _autorizado():
            return jsonify({'ok': False, 'erro': 'não autorizado'}), 401
        p = request.get_json(silent=True) or {}
        try:
            corpo, status = receber(p, teste=bool(p.get('teste')))
        except Exception as e:
            log.exception('[os_medicao] falha ao receber')
            return jsonify({'ok': False, 'erro': f'Não foi possível registrar a OS de medição: {e}'}), 500
        return jsonify(corpo), status

    @bp.route('/os/medicao/situacao')
    def os_medicao_situacao():
        if not _autorizado():
            return jsonify({'ok': False, 'erro': 'não autorizado'}), 401
        origem_id = request.args.get('origem_id', '')
        if not origem_id.strip():
            return jsonify({'ok': False, 'erro': 'informe origem_id'}), 400
        return jsonify(situacao(origem_id))
