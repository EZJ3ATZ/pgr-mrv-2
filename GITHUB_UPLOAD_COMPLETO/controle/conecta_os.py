# -*- coding: utf-8 -*-
"""Andamento da OS de medição no OCP Conecta (pedido do Matheus, 16/10/2026).

A OS de medição que o CRM abre também existe no Conecta (Portal Interno), só para
acompanhar. Ela anda lá conforme anda aqui:
  planejamento feito  -> marco "planejada": conclui L1, executor = técnico do plano
  demanda concluída   -> marco "concluida": conclui L2 e L3

Roda no agendador a cada 15 min (e sob demanda), olha todas as demandas `crm_os`
e manda ao Conecta só o marco que ainda não foi mandado (evento
`conecta_os_marco`). O Conecta é idempotente: conclui só a etapa ativa que falta.

Variáveis:
  CONECTA_OS_KEY  chave da API (sha256 em os_config.api_chave_medicoes_sha256
                  no Conecta). Sem ela, não faz nada.
  CONECTA_OS_URL  padrão https://conectaocp.up.railway.app/api/os/medicao
"""
import json
import logging
import os
import urllib.request

from .db import get_db, row_to_dict

log = logging.getLogger(__name__)

URL_PADRAO = 'https://conectaocp.up.railway.app/api/os/medicao'
ORDEM = {'planejada': 1, 'concluida': 2}


def _marco(etapa):
    if etapa == 'concluida':
        return 'concluida'
    if etapa in ('planejada', 'em_campo'):
        return 'planejada'
    return None


def _ja_mandado(conn, demanda_id):
    rows = conn.execute("SELECT descricao FROM eventos WHERE tipo='conecta_os_marco' AND ref_id=?",
                        (demanda_id,)).fetchall()
    return max([ORDEM.get((row_to_dict(r).get('descricao') or '').split(' ')[0], 0) for r in rows] or [0])


def _email_do_tecnico(conn, nome):
    if not nome:
        return None
    r = conn.execute("SELECT email FROM usuarios WHERE LOWER(nome)=LOWER(?) AND email IS NOT NULL LIMIT 1",
                     (nome.strip(),)).fetchone()
    return row_to_dict(r)['email'] if r else None


def _post(url, chave, corpo):
    req = urllib.request.Request(url, data=json.dumps(corpo).encode(), method='POST',
                                 headers={'Content-Type': 'application/json', 'x-api-key': chave})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode() or '{}')


def pendentes():
    """Lista (demanda_id, origem_id, marco, executor_email, detalhe) do que falta mandar."""
    from .os_medicao import situacao
    with get_db() as conn:
        rows = [row_to_dict(r) for r in conn.execute(
            "SELECT id, origem_ref FROM demandas WHERE origem='crm_os' AND origem_ref LIKE 'crm:%'").fetchall()]
    out = []
    for d in rows:
        origem_id = d['origem_ref'][4:]
        s = situacao(origem_id)
        marco = _marco(s.get('etapa')) if s.get('encontrada') else None
        if not marco:
            continue
        with get_db() as conn:
            if _ja_mandado(conn, d['id']) >= ORDEM[marco]:
                continue
            email = _email_do_tecnico(conn, s.get('tecnico'))
        if marco == 'planejada':
            detalhe = 'planejamento feito' + (f' para {s["data_prevista"]}' if s.get('data_prevista') else '') \
                      + (f', técnico {s["tecnico"]}' if s.get('tecnico') else '')
        else:
            detalhe = 'medição concluída' + (f' em {str(s["concluida_em"])[:10]}' if s.get('concluida_em') else '')
        out.append((d['id'], origem_id, marco, email, detalhe))
    return out


def sincronizar():
    chave = (os.environ.get('CONECTA_OS_KEY') or '').strip()
    if not chave:
        return {'ativo': False}
    url = (os.environ.get('CONECTA_OS_URL') or URL_PADRAO).strip()
    feitos, erros = [], []
    for did, origem_id, marco, email, detalhe in pendentes():
        try:
            r = _post(url, chave, {'origem': 'crm', 'origem_id': origem_id, 'marco': marco,
                                   'executor_email': email, 'detalhe': detalhe})
            if not r.get('ok'):
                raise RuntimeError(r.get('erro') or 'resposta sem ok')
            if not r.get('encontrada'):
                continue   # OS ainda não está no Conecta: tenta de novo na próxima volta
            with get_db() as conn:
                conn.execute("INSERT INTO eventos (tipo, descricao, ref_id, ref_tipo) "
                             "VALUES ('conecta_os_marco', ?, ?, 'demanda')",
                             (f'{marco} {r.get("numero") or ""} {", ".join(r.get("concluidas") or [])}'[:500], did))
            feitos.append({'demanda_id': did, 'marco': marco, 'concluidas': r.get('concluidas')})
        except Exception as e:
            log.warning('[conecta_os] demanda %s (%s): %s', did, marco, e)
            erros.append({'demanda_id': did, 'erro': str(e)[:200]})
    return {'ativo': True, 'feitos': feitos, 'erros': erros}
