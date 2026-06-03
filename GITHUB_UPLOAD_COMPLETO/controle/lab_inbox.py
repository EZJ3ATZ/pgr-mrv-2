# -*- coding: utf-8 -*-
"""Ingestão dos e-mails do laboratório UniScientific → reconcilia amostradores.

Lê via graph (Mail.Read.All, identidade de Aplicação). Classifica cada e-mail
do lab, extrai os códigos e — processando em ordem CRONOLÓGICA (o último sinal
de cada código vence) — atualiza o status do amostrador.

Categorias e efeito:
  - remessa      : lab ENVIOU amostradores → 'disponivel' (entra em posse/estoque)
  - recebimento  : lab CONFIRMOU recebimento dos devolvidos → 'devolvido'
  - resultado    : laudo (RA) recebido por empresa → registrado (sem mexer status)
  - pendentes    : lista oficial em posse +30 dias → guardada p/ aba Vencimento

Segurança: só age sobre códigos que JÁ EXISTEM no inventário (casa por código ou
tipo+código). E-mails são removidos do texto antes de extrair (evita falso-positivo
de 'recebimento04@', 'engenharia13@', etc.).
"""
import re
import html
import json
from datetime import datetime
from .graph import graph_get
from .db import get_db, row_to_dict

MAILBOX  = 'engenharia19@ocupacional.com.br'
LAB_DOM  = 'uniscientificgroup.com.br'
_CODE_RE = re.compile(r'[A-Z]{2,5}\d{2,}[A-Z0-9]*')


def _norm(c):
    return re.sub(r'\s+', '', str(c or '')).upper()


def _codes(texto):
    t = html.unescape(re.sub(r'<[^>]+>', ' ', texto or ''))
    t = re.sub(r'\S+@\S+', ' ', t)          # remove e-mails (mata MENTO04/HARIA13/TACAO01)
    t = re.sub(r'https?://\S+', ' ', t)      # remove URLs
    out = []
    for m in _CODE_RE.finditer(t.upper()):
        c = m.group(0)
        if 5 <= len(c) <= 16:
            out.append(c)
    return list(dict.fromkeys(out))


def _classificar(sender, subject):
    s = (sender or '').lower()
    sub = (subject or '').lower()
    if 'pendentes de retorno' in sub:
        return 'pendentes'
    if sub.startswith('ra ') or '// avaliada' in sub or 'resultados' in s:
        return 'resultado'
    if 'recebimento' in s or 'cadeia de custod' in sub:
        return 'recebimento'
    if 'solicita' in s or 'comercial' in s or 'remessa' in sub:
        return 'remessa'
    return None


def _sistema_lookup():
    """norm(codigo) e norm(tipo+codigo) -> dict do amostrador."""
    look = {}
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, codigo, tipo, status FROM amostradores "
            "WHERE COALESCE(arquivado,0)=0").fetchall()
    for r in rows:
        d = row_to_dict(r)
        cod, tp = _norm(d.get('codigo')), _norm(d.get('tipo'))
        for k in {cod, tp + cod}:
            if k:
                look[k] = d
    return look


def _fetch_lab_emails(top=150):
    data = graph_get(
        f"/users/{MAILBOX}/mailFolders/inbox/messages"
        f"?$top={top}&$orderby=receivedDateTime desc"
        f"&$select=id,subject,from,receivedDateTime,body")
    out = []
    for m in data.get('value', []):
        frm = (((m.get('from') or {}).get('emailAddress') or {}).get('address') or '').lower()
        if LAB_DOM not in frm:
            continue
        out.append({
            'subject': m.get('subject', ''),
            'from': frm,
            'data': (m.get('receivedDateTime') or '')[:10],
            'body': (m.get('body') or {}).get('content', ''),
        })
    return out


def _kv_set(conn, chave, valor):
    """Grava em ms_sync_state (cria a tabela se preciso)."""
    conn.execute("CREATE TABLE IF NOT EXISTS ms_sync_state (chave TEXT PRIMARY KEY, valor TEXT, atualizado_em TEXT)")
    if _use_pg():
        conn.execute("INSERT INTO ms_sync_state (chave,valor,atualizado_em) VALUES (?,?,CURRENT_TIMESTAMP) "
                     "ON CONFLICT (chave) DO UPDATE SET valor=EXCLUDED.valor, atualizado_em=CURRENT_TIMESTAMP",
                     (chave, valor))
    else:
        conn.execute("INSERT OR REPLACE INTO ms_sync_state (chave,valor,atualizado_em) VALUES (?,?,CURRENT_TIMESTAMP)",
                     (chave, valor))


def _use_pg():
    import os
    return bool(os.environ.get('DATABASE_URL'))


def sincronizar_lab(apply=False, top=150):
    """Lê os e-mails do lab e reconcilia. apply=False → só simula (preview)."""
    look = _sistema_lookup()
    emails = sorted(_fetch_lab_emails(top), key=lambda e: e['data'])  # cronológico (antigo→novo)

    cat_count = {'remessa': 0, 'recebimento': 0, 'resultado': 0, 'pendentes': 0, 'ignorado': 0}
    fora = set()
    # último sinal de status por código (cronológico → o último vence)
    estado_final = {}   # codigo_sistema_key -> ('disponivel'|'devolvido', data)
    pendentes = None
    resultados = []

    for e in emails:
        cat = _classificar(e['from'], e['subject'])
        cat_count[cat or 'ignorado'] += 1
        if cat == 'resultado':
            resultados.append({'assunto': e['subject'], 'data': e['data']})
            continue
        if cat not in ('remessa', 'recebimento', 'pendentes'):
            continue
        codes = _codes(e['body'])
        no_sis = [c for c in codes if c in look]
        for c in codes:
            if c not in look:
                fora.add(c)
        if cat == 'pendentes':
            pendentes = {'data': e['data'], 'codigos': no_sis, 'total': len(codes)}
        else:
            novo = 'disponivel' if cat == 'remessa' else 'devolvido'
            for c in no_sis:
                estado_final[c] = (novo, e['data'])

    # monta plano de mudanças (status atual != alvo)
    plano = []
    for c, (alvo, data) in estado_final.items():
        amos = look.get(c)
        if amos and (amos.get('status') or '') != alvo:
            plano.append({'id': amos['id'], 'codigo': amos.get('codigo'),
                          'de': amos.get('status'), 'para': alvo, 'fonte_data': data})

    aplicadas = 0
    if apply:
        with get_db() as conn:
            for p in plano:
                conn.execute("UPDATE amostradores SET status=?, atualizado_em=CURRENT_TIMESTAMP WHERE id=?",
                             (p['para'], p['id']))
                aplicadas += 1
            if pendentes is not None:
                _kv_set(conn, 'lab_pendentes', json.dumps(pendentes, ensure_ascii=False))
            if resultados:
                _kv_set(conn, 'lab_resultados', json.dumps(resultados[:30], ensure_ascii=False))

    return {
        'modo': 'APLICADO' if apply else 'PREVIEW (nada gravado)',
        'mailbox': MAILBOX,
        'emails_por_categoria': cat_count,
        'pendentes_oficial': pendentes,
        'mudancas_de_status': plano if not apply else aplicadas,
        'total_mudancas': len(plano),
        'aplicadas': aplicadas,
        'codigos_fora_do_sistema': sorted(fora)[:30],
        'resultados_recentes': resultados[:10],
    }


def preview(top=150):
    return sincronizar_lab(apply=False, top=top)


def get_pendentes_salvos():
    """Lê a última lista de pendentes guardada (para a aba Vencimento)."""
    try:
        with get_db() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS ms_sync_state (chave TEXT PRIMARY KEY, valor TEXT, atualizado_em TEXT)")
            row = conn.execute("SELECT valor FROM ms_sync_state WHERE chave='lab_pendentes'").fetchone()
        if row:
            d = row_to_dict(row)
            return json.loads(d.get('valor') or '{}')
    except Exception:
        pass
    return None
