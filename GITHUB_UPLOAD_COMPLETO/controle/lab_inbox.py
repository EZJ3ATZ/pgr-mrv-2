# -*- coding: utf-8 -*-
"""Ingestão dos e-mails do laboratório UniScientific → reconcilia amostradores.

Lê via graph (Mail.Read.All, identidade de Aplicação). Classifica cada e-mail
do lab e extrai os códigos de amostrador.

Categorias:
  - remessa      : lab ENVIOU amostradores em branco → entram em posse (estoque)
  - recebimento  : lab CONFIRMOU recebimento dos devolvidos → status 'devolvido'
  - resultado    : laudo (RA) recebido por empresa avaliada
  - pendentes    : lista oficial em posse +30 dias → cobrança (aba Vencimento)

Por segurança, só age sobre códigos que JÁ EXISTEM no inventário (casamento por
código ou tipo+código), evitando falso-positivo de regex (CEP, RA nº, etc.).
"""
import re
import html
from .graph import graph_get
from .db import get_db, row_to_dict

MAILBOX  = 'engenharia19@ocupacional.com.br'
LAB_DOM  = 'uniscientificgroup.com.br'
_CODE_RE = re.compile(r'[A-Z]{2,5}\d{2,}[A-Z0-9]*')


def _norm(c):
    return re.sub(r'\s+', '', str(c or '')).upper()


def _codes(texto):
    t = html.unescape(re.sub(r'<[^>]+>', ' ', texto or '')).upper()
    out = []
    for m in _CODE_RE.finditer(t):
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


def _fetch_lab_emails(top=60):
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


def preview(top=60):
    """Simulação (dry-run): classifica os e-mails do lab, extrai códigos e casa
    com o inventário. NÃO grava nada — só mostra o que seria feito."""
    look = _sistema_lookup()
    emails = _fetch_lab_emails(top)

    resumo = {c: {'emails': 0, 'codigos': 0, 'no_sistema': 0,
                  'exemplos': [], 'fora_sistema': []}
              for c in ('remessa', 'recebimento', 'resultado', 'pendentes')}
    ignorados = 0
    pendentes_oficial = None  # lista do e-mail de pendentes mais recente

    for e in emails:
        cat = _classificar(e['from'], e['subject'])
        if not cat:
            ignorados += 1
            continue
        r = resumo[cat]
        r['emails'] += 1
        if cat == 'resultado':
            continue  # resultado: vínculo por empresa (assunto), sem casar código
        codes = _codes(e['body'])
        no_sis = [c for c in codes if c in look]
        fora = [c for c in codes if c not in look]
        r['codigos'] += len(codes)
        r['no_sistema'] += len(no_sis)
        for c in no_sis:
            if len(r['exemplos']) < 12:
                r['exemplos'].append(c)
        for c in fora:
            if len(r['fora_sistema']) < 8:
                r['fora_sistema'].append(c)
        if cat == 'pendentes' and pendentes_oficial is None:
            pendentes_oficial = {'data': e['data'], 'codigos': no_sis,
                                 'fora_sistema': fora, 'total': len(codes)}

    return {
        'mailbox': MAILBOX,
        'emails_do_lab': sum(resumo[c]['emails'] for c in resumo) + 0,
        'ignorados': ignorados,
        'por_categoria': resumo,
        'pendentes_oficial': pendentes_oficial,
        'inventario_indexado': len(look),
        'modo': 'PREVIEW (nada foi gravado)',
    }
