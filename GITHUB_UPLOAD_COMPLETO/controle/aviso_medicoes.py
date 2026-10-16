# -*- coding: utf-8 -*-
"""Aviso por e-mail de demanda de medição nova (pedido do Matheus, 16/10/2026).

Toda demanda de medição que chega (pelo CRM ou pelo Planner) vai por e-mail ao
grupo Medições (medicoes@, membros Wesley, Helbert, Luiz e Matheus), com a Fabi
e o Luiz em cópia direta — a Fabi não é membro do grupo e grupo do M365 só cai
na caixa de quem segue a conversa no Outlook.

O envio roda numa thread com espera: quem chama ainda está dentro da transação,
então o aviso relê a demanda do banco depois do commit (se não existir, não
manda) e grava o evento `aviso_medicoes_email` para nunca mandar duas vezes.

Variáveis (opcionais):
  AVISO_MEDICOES_ATIVO   '1' liga, '0' desliga. Sem ela, só liga em produção
                         no Railway (máquina local com credencial do Graph
                         nunca dispara e-mail real por engano).
  AVISO_MEDICOES_PARA    destinatário (padrão medicoes@ocupacional.com.br)
  AVISO_MEDICOES_CC      cópia, separada por vírgula (padrão Fabi e Luiz)
  ORQ_MAIL_FROM          remetente (o mesmo do orquestrador; caixa de verdade)
"""
import logging
import os
import threading
import time

from .db import get_db, row_to_dict

log = logging.getLogger(__name__)

PARA_PADRAO = 'medicoes@ocupacional.com.br'
CC_PADRAO = 'gestaoengenharia@ocupacional.com.br,coordenacao@ocupacional.com.br'
PORTAL_URL = 'https://medicoes-ocupacional.up.railway.app/'
ESPERA_S = 20


def ativo():
    v = (os.environ.get('AVISO_MEDICOES_ATIVO') or '').strip()
    if v:
        return v == '1'
    return (os.environ.get('RAILWAY_ENVIRONMENT_NAME') or '').strip().lower() == 'production'


def _ja_avisado(conn, demanda_id):
    return conn.execute("SELECT 1 FROM eventos WHERE tipo='aviso_medicoes_email' AND ref_id=? LIMIT 1",
                        (demanda_id,)).fetchone() is not None


def montar(conn, demanda_id):
    """Monta o e-mail da demanda. Devolve dict (assunto, corpo) ou None se ela
    não existe ou já foi concluída."""
    from .routes import _dados_campo_da_demanda
    r = conn.execute(
        "SELECT d.id, d.numero_os, d.titulo, d.status, d.origem, d.prazo, d.descricao, d.dados_os, "
        "e.nome AS empresa_nome FROM demandas d LEFT JOIN empresas e ON e.id=d.empresa_id WHERE d.id=?",
        (demanda_id,)).fetchone()
    if not r:
        return None
    d = row_to_dict(r)
    if (d.get('status') or '') in ('concluida', 'cancelada'):
        return None
    itens = [row_to_dict(x) for x in conn.execute(
        "SELECT agente, qtd_pontos_prevista FROM medicoes WHERE demanda_id=? ORDER BY id", (demanda_id,)).fetchall()]
    dc = _dados_campo_da_demanda(d)
    empresa = d.get('empresa_nome') or ''
    os_num = d.get('numero_os') or ''
    origem = 'CRM' if d.get('origem') == 'crm_os' else 'Planner'
    assunto = 'Nova demanda de medição: ' + ' - '.join(x for x in [os_num, empresa] if x)
    prazo = str(dc.get('prazo') or '')[:10]
    if prazo and len(prazo) == 10:
        prazo = '/'.join(reversed(prazo.split('-')))
    linhas = ['Fabi e Luiz,', '',
              f'Chegou uma demanda de medição nova pelo {origem}. Ela já está no Portal de Medições para planejar.',
              '']
    linhas += [x for x in [f'OS: {os_num}' if os_num else '', f'Empresa: {empresa}' if empresa else '',
                           f'Prazo: {prazo}' if prazo else '',
                           f'Aberta por: {dc["solicitante"]}' if dc.get('solicitante') else ''] if x]
    if itens:
        linhas += ['', 'Itens:'] + [f'- {it["agente"]} x{it["qtd_pontos_prevista"]}' for it in itens]
    if dc.get('observacoes'):
        linhas += ['', 'Observações do comercial:' if origem == 'CRM' else 'Descrição da tarefa:',
                   dc['observacoes']]
    if dc.get('endereco') or dc.get('contato'):
        linhas += ['', 'Local da medição:' if dc.get('local_confirmado') else 'Cadastro da empresa no CRM:']
        linhas += [x for x in [f'Endereço: {dc["endereco"]}' if dc.get('endereco') else '',
                               f'Contato: {dc["contato"]}' if dc.get('contato') else ''] if x]
    linhas += ['', f'Portal de Medições: {PORTAL_URL}', '',
               'E-mail automático do Portal de Medições.']
    return {'assunto': assunto[:250], 'corpo': '\n'.join(linhas)}


def _enviar(demanda_id):
    from .graph import graph_post
    from .orquestrador import EMAIL_REMETENTE
    with get_db() as conn:
        if _ja_avisado(conn, demanda_id):
            return False
        msg = montar(conn, demanda_id)
        if not msg:
            return False
        para = (os.environ.get('AVISO_MEDICOES_PARA') or PARA_PADRAO).strip()
        cc = [x.strip() for x in (os.environ.get('AVISO_MEDICOES_CC') or CC_PADRAO).split(',') if x.strip()]
        payload = {'message': {
            'subject': msg['assunto'],
            'body': {'contentType': 'Text', 'content': msg['corpo']},
            'toRecipients': [{'emailAddress': {'address': para}}],
            'ccRecipients': [{'emailAddress': {'address': x}} for x in cc],
        }, 'saveToSentItems': True}
        graph_post(f'/users/{EMAIL_REMETENTE}/sendMail', payload)
        conn.execute("INSERT INTO eventos (tipo, descricao, ref_id, ref_tipo) VALUES ('aviso_medicoes_email', ?, ?, 'demanda')",
                     (f'{msg["assunto"]} -> {para} cc {", ".join(cc)}'[:500], demanda_id))
    return True


def _rodar(demanda_id, espera):
    try:
        if espera:
            time.sleep(espera)
        if _enviar(demanda_id):
            log.info('[aviso_medicoes] e-mail da demanda %s enviado', demanda_id)
    except Exception as e:   # aviso nunca derruba nada
        log.warning('[aviso_medicoes] demanda %s: %s', demanda_id, e)


def avisar(demanda_id, espera=ESPERA_S):
    """Agenda o e-mail da demanda nova. Não bloqueia e nunca levanta."""
    if not demanda_id or not ativo():
        return False
    threading.Thread(target=_rodar, args=(demanda_id, espera), daemon=True).start()
    return True
