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
            "SELECT id, codigo, tipo, status, atualizado_em FROM amostradores "
            "WHERE COALESCE(arquivado,0)=0").fetchall()
    for r in rows:
        d = row_to_dict(r)
        cod, tp = _norm(d.get('codigo')), _norm(d.get('tipo'))
        for k in {cod, tp + cod}:
            if k:
                look[k] = d
    return look


def _mailboxes():
    """Caixas a varrer: a oficial do lab + e-mail de cada técnico cadastrado
    (todos @ocupacional.com.br). O app é App-only com Mail.Read.All tenant-wide,
    então alcança todas sem vínculo/OAuth por usuário."""
    boxes = [MAILBOX]
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT email FROM usuarios "
                "WHERE COALESCE(ativo,1)=1 AND email LIKE '%@ocupacional.com.br'"
            ).fetchall()
        for r in rows:
            em = (row_to_dict(r).get('email') or '').strip().lower()
            if em:
                boxes.append(em)
    except Exception:
        pass
    seen, out = set(), []
    for b in boxes:                       # dedupe case-insensitive, preserva ordem
        k = (b or '').lower()
        if k and k not in seen:
            seen.add(k); out.append(b)
    return out


def _fetch_lab_emails(boxes, top=150):
    """Lê e-mails do laboratório em VÁRIAS caixas. Só retorna os que vêm do
    domínio do lab (LAB_DOM) — o resto da caixa do técnico é ignorado."""
    out = []
    erros = {}
    for box in boxes:
        try:
            data = graph_get(
                f"/users/{box}/mailFolders/inbox/messages"
                f"?$top={top}&$orderby=receivedDateTime desc"
                f"&$select=id,subject,from,receivedDateTime,hasAttachments,body")
        except Exception as e:
            erros[box] = str(e)[:140]
            continue
        for m in data.get('value', []):
            frm = (((m.get('from') or {}).get('emailAddress') or {}).get('address') or '').lower()
            # Mantém e-mails do lab + RAs ENCAMINHADOS (ENC:) por técnicos: o
            # remetente vira interno, mas o assunto ainda classifica como resultado.
            if LAB_DOM not in frm and not _classificar(frm, m.get('subject', '')):
                continue
            out.append({
                'id': m.get('id'),
                'subject': m.get('subject', ''),
                'from': frm,
                'data': (m.get('receivedDateTime') or '')[:10],
                'data_full': m.get('receivedDateTime') or '',
                'anexos': bool(m.get('hasAttachments')),
                'body': (m.get('body') or {}).get('content', ''),
                'caixa': box,
            })
    return out, erros


def _codigo_do_anexo_ra(nome):
    """Código do amostrador no NOME do laudo: '81959338-1-TCP4058AV2-EMP-...-Manifesto.pdf'
    → 'TCP4058AV2' (3º segmento separado por '-')."""
    base = (nome or '').rsplit('.', 1)[0]
    parts = base.split('-')
    return parts[2].strip() if len(parts) >= 3 else ''


def _fetch_sent_to_lab(boxes, look, top=110, max_anexo_mb=8, max_downloads=90, parse_anexos=True):
    """E-mails ENVIADOS por cada caixa PARA o laboratório (cadeia de custódia).
    Casa os códigos do inventário no CORPO; se o corpo não tiver, baixa os
    anexos-documento (PDF/xlsx) e casa lá (Fase 2). Retorna [{data, codigos, caixa}]
    com codigos = chaves normalizadas de _sistema_lookup. max_downloads limita o nº
    de anexos baixados por varredura (evita pesar demais o background)."""
    out = []
    baixados = 0
    for box in boxes:
        try:
            data = graph_get(
                f"/users/{box}/mailFolders/sentitems/messages"
                f"?$top={top}&$orderby=sentDateTime desc"
                f"&$select=id,subject,toRecipients,sentDateTime,hasAttachments,body")
        except Exception:
            continue
        for m in data.get('value', []):
            tos = [(((t or {}).get('emailAddress') or {}).get('address') or '').lower()
                   for t in (m.get('toRecipients') or [])]
            if not any(LAB_DOM in t for t in tos):
                continue
            dt = (m.get('sentDateTime') or '')[:10]
            if not dt:
                continue
            body = (m.get('body') or {}).get('content', '')
            cods = _codigos_no_texto(((m.get('subject', '') or '') + ' ' + body), look)
            if not cods and m.get('hasAttachments') and parse_anexos and baixados < max_downloads:
                try:
                    metas = graph_get(f"/users/{box}/messages/{m['id']}/attachments"
                                      f"?$select=id,name,contentType,size").get('value', [])
                except Exception:
                    metas = []
                for meta in metas:
                    if baixados >= max_downloads:
                        break
                    nome = meta.get('name', ''); ct = (meta.get('contentType') or '')
                    low = nome.lower()
                    if not (low.endswith(('.pdf', '.xlsx', '.xlsm')) or 'pdf' in ct or 'spreadsheet' in ct):
                        continue  # pula imagens de assinatura
                    if (meta.get('size', 0) or 0) > max_anexo_mb * 1024 * 1024:
                        continue
                    try:
                        full = graph_get(f"/users/{box}/messages/{m['id']}/attachments/{meta['id']}")
                        baixados += 1
                        txt = _extrair_texto_anexo(nome, ct, full.get('contentBytes'))
                        cods += _codigos_no_texto(txt, look)
                    except Exception:
                        continue
            if cods:
                out.append({'data': dt, 'codigos': list(dict.fromkeys(cods)), 'caixa': box})
    return out


def _extrair_texto_anexo(nome, content_type, content_b64):
    """Texto de um anexo: PDF via pymupdf, xlsx via openpyxl. '' se imagem/erro/escaneado."""
    if not content_b64:
        return ''
    import base64, io
    low = (nome or '').lower(); ct = (content_type or '').lower()
    try:
        raw = base64.b64decode(content_b64)
    except Exception:
        return ''
    try:
        if low.endswith('.pdf') or 'pdf' in ct:
            import fitz
            doc = fitz.open(stream=raw, filetype='pdf')
            txt = ' '.join(p.get_text() for p in doc); doc.close()
            return txt
        if low.endswith(('.xlsx', '.xlsm')) or 'spreadsheet' in ct:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            buf = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    buf += [str(c) for c in row if c is not None]
            wb.close()
            return ' '.join(buf)
    except Exception:
        return ''
    return ''


def _codigos_no_texto(texto, look=None):
    """REVERSO: procura os códigos do INVENTÁRIO dentro do texto (normalizado, sem
    espaços). Robusto a códigos que começam com dígito (64U57) e a formatação com
    espaço/células. Casa por tipo+codigo (específico) ou codigo. Retorna [codigo...]."""
    if not texto:
        return []
    if look is None:
        look = _sistema_lookup()
    norm = re.sub(r'\s+', '', texto).upper()
    achados, vistos = [], set()
    for d in look.values():
        aid = d.get('id')
        if aid in vistos:
            continue
        cod, tp = _norm(d.get('codigo')), _norm(d.get('tipo'))
        if not cod:
            continue
        # tipo+codigo (ex.: EC91943A) é específico → seguro. Codigo isolado só se
        # for longo/distintivo (≥7), p/ não casar analito (FE2O3) nem pedaço de nº.
        hit = bool(tp) and (tp + cod) in norm
        if not hit and len(cod) >= 7:
            hit = cod in norm
        if hit:
            vistos.add(aid)
            achados.append(cod)   # chave normalizada (= chave de _sistema_lookup)
    return achados


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


def sincronizar_lab(apply=False, top=120, parse_anexos=True):
    """Lê os e-mails do lab (em TODAS as caixas dos técnicos + a oficial) e
    reconcilia. apply=False → só simula (preview). top = e-mails recentes por caixa.
    parse_anexos=False → varredura LEVE (só corpo+inbox, não baixa anexos)."""
    look = _sistema_lookup()
    boxes = _mailboxes()
    emails, fetch_erros = _fetch_lab_emails(boxes, top)
    # cronológico (antigo→novo) pelo timestamp COMPLETO — antes truncava a dia e
    # a ordem dentro do mesmo dia era aleatória (e-mail antigo podia vencer)
    emails = sorted(emails, key=lambda e: e.get('data_full') or e['data'])

    # Watermark: e-mail de status (remessa/recebimento) já processado numa varredura
    # anterior NÃO é reaplicado. Era isso que revertia a edição manual do técnico a
    # cada 3h — o mesmo e-mail antigo de remessa ("disponivel") entrava de novo.
    watermark = ''
    try:
        with get_db() as conn:
            r = conn.execute("SELECT valor FROM ms_sync_state WHERE chave='lab_watermark'").fetchone()
            watermark = ((row_to_dict(r) or {}).get('valor') or '') if r else ''
    except Exception:
        watermark = ''
    max_visto = watermark

    cat_count = {'remessa': 0, 'recebimento': 0, 'resultado': 0, 'pendentes': 0, 'ignorado': 0}
    fora = set()
    # último sinal de status por código (cronológico → o último vence)
    estado_final = {}   # codigo_sistema_key -> ('disponivel'|'devolvido', data)
    pendentes = None
    resultados = []
    _res_vistos = set()   # dedupe de RA (mesmo laudo em 2 caixas não duplica)
    resultado_por_id = {}  # amostrador id -> menor data de resultado (RA)

    for e in emails:
        cat = _classificar(e['from'], e['subject'])
        cat_count[cat or 'ignorado'] += 1
        if cat == 'resultado':
            _k = ((e['subject'] or '').strip().lower(), e['data'])
            if _k not in _res_vistos:
                _res_vistos.add(_k)
                resultados.append({'assunto': e['subject'], 'data': e['data'], 'caixa': e.get('caixa', '')})
            # data_resultado por amostrador: o código vem no NOME do laudo (PDF).
            # Só precisa LISTAR os anexos (leve) — não baixa o conteúdo.
            if e.get('anexos') and e.get('id'):
                try:
                    metas = graph_get(f"/users/{e['caixa']}/messages/{e['id']}/attachments"
                                      f"?$select=name").get('value', [])
                except Exception:
                    metas = []
                for meta in metas:
                    nome = meta.get('name', '')
                    if not nome.lower().endswith('.pdf'):
                        continue
                    cod = _norm(_codigo_do_anexo_ra(nome))
                    amos = look.get(cod) if cod else None
                    if not amos:
                        continue
                    aid, dt = amos['id'], e['data']
                    if dt and (aid not in resultado_por_id or dt < resultado_por_id[aid]):
                        resultado_por_id[aid] = dt
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
            full = e.get('data_full') or e['data']
            if full > max_visto:
                max_visto = full
            if watermark and full <= watermark:
                continue   # já processado em varredura anterior — não reaplica
            novo = 'disponivel' if cat == 'remessa' else 'devolvido'
            for c in no_sis:
                estado_final[c] = (novo, e['data'])

    # monta plano de mudanças (status atual != alvo)
    plano = []
    for c, (alvo, data) in estado_final.items():
        amos = look.get(c)
        if not amos or (amos.get('status') or '') == alvo:
            continue
        # Guarda de timestamp: e-mail mais antigo que a última edição do amostrador
        # NÃO rebaixa o status — a edição manual do técnico vence o sinal antigo.
        ult = str(amos.get('atualizado_em') or '')[:10]
        if ult and (data or '') < ult:
            continue
        plano.append({'id': amos['id'], 'codigo': amos.get('codigo'),
                      'de': amos.get('status'), 'para': alvo, 'fonte_data': data})

    # ── ENVIO ao lab (data REAL) — lê os e-mails ENVIADOS com a cadeia de custódia.
    #    Pega a MENOR data por amostrador (1º envio) e só preenche quem ainda NÃO
    #    tem data_envio_lab (não sobrescreve o que foi lançado à mão). ──
    envio_por_id = {}   # amostrador id -> menor data de envio detectada
    for se in _fetch_sent_to_lab(boxes, look, parse_anexos=parse_anexos):
        for c in se['codigos']:
            amos = look.get(c)
            if not amos:
                continue
            aid, dt = amos['id'], se['data']
            if dt and (aid not in envio_por_id or dt < envio_por_id[aid]):
                envio_por_id[aid] = dt
    envio_faltam = []   # ids sem data_envio_lab → candidatos a auto-preencher
    if envio_por_id:
        _ids = list(envio_por_id.keys())
        _ph = ','.join(['?'] * len(_ids))
        with get_db() as conn:
            envio_faltam = [row_to_dict(r)['id'] for r in conn.execute(
                f"SELECT id FROM amostradores WHERE id IN ({_ph}) "
                f"AND (data_envio_lab IS NULL OR data_envio_lab='')", _ids).fetchall()]
    auto_envio = len(envio_faltam)

    resultado_faltam = []   # ids sem data_resultado → candidatos a auto-preencher
    if resultado_por_id:
        _rids = list(resultado_por_id.keys())
        _rph = ','.join(['?'] * len(_rids))
        with get_db() as conn:
            resultado_faltam = [row_to_dict(r)['id'] for r in conn.execute(
                f"SELECT id FROM amostradores WHERE id IN ({_rph}) "
                f"AND (data_resultado IS NULL OR data_resultado='')", _rids).fetchall()]
    auto_resultado = len(resultado_faltam)

    aplicadas = 0
    if apply:
        with get_db() as conn:
            for p in plano:
                conn.execute("UPDATE amostradores SET status=?, atualizado_em=CURRENT_TIMESTAMP WHERE id=?",
                             (p['para'], p['id']))
                aplicadas += 1
            for rid in envio_faltam:   # auto-data o envio (só quem estava sem data)
                conn.execute("UPDATE amostradores SET data_envio_lab=?, atualizado_em=CURRENT_TIMESTAMP WHERE id=?",
                             (envio_por_id[rid], rid))
            for rid in resultado_faltam:   # auto-data o resultado (RA) — pelo nome do laudo
                conn.execute("UPDATE amostradores SET data_resultado=?, atualizado_em=CURRENT_TIMESTAMP WHERE id=?",
                             (resultado_por_id[rid], rid))
            # Reconciliação de ALTA CONFIANÇA: laboratório com resultado (RA) → concluído.
            # 'reservado' é status legítimo escolhido na UI e NÃO é mais apagado aqui
            # (fix 03/07/2026 — a job zerava a reserva do técnico a cada 3h).
            conn.execute("UPDATE amostradores SET status='concluido', atualizado_em=CURRENT_TIMESTAMP "
                         "WHERE COALESCE(arquivado,0)=0 AND status='laboratorio' "
                         "AND COALESCE(data_resultado,'') <> ''")
            if max_visto and max_visto != watermark:
                _kv_set(conn, 'lab_watermark', max_visto)
            if pendentes is not None:
                _kv_set(conn, 'lab_pendentes', json.dumps(pendentes, ensure_ascii=False))
            if resultados:
                _kv_set(conn, 'lab_resultados', json.dumps(resultados[:30], ensure_ascii=False))
            _kv_set(conn, 'lab_sync_result', json.dumps({
                'mailboxes_lidas': len(boxes),
                'aplicadas': aplicadas,
                'envio_auto_datados': auto_envio,
                'resultado_auto_datados': auto_resultado,
                'resultados_total': len(resultados),
                'por_categoria': cat_count,
                'fetch_erros': fetch_erros,
            }, ensure_ascii=False))

    return {
        'modo': 'APLICADO' if apply else 'PREVIEW (nada gravado)',
        'mailbox': MAILBOX,                 # legado
        'mailboxes': boxes,                 # todas as caixas varridas
        'mailboxes_lidas': len(boxes),
        'fetch_erros': fetch_erros,         # caixas que falharam (ex.: 403 por policy)
        'emails_por_categoria': cat_count,
        'pendentes_oficial': pendentes,
        'mudancas_de_status': plano if not apply else aplicadas,
        'total_mudancas': len(plano),
        'aplicadas': aplicadas,
        'envio_auto_datados': auto_envio,
        'envio_detectado': len(envio_por_id),
        'resultado_auto_datados': auto_resultado,
        'resultado_detectado': len(resultado_por_id),
        'codigos_fora_do_sistema': sorted(fora)[:30],
        'resultados_total': len(resultados),
        'resultados_recentes': resultados[:10],
    }


def preview(top=60):
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


def get_resultados_salvos():
    """Lê a última lista de resultados/laudos (RA) recebidos do lab (aba Vencimento)."""
    try:
        with get_db() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS ms_sync_state (chave TEXT PRIMARY KEY, valor TEXT, atualizado_em TEXT)")
            row = conn.execute("SELECT valor FROM ms_sync_state WHERE chave='lab_resultados'").fetchone()
        if row:
            d = row_to_dict(row)
            return json.loads(d.get('valor') or '[]')
    except Exception:
        pass
    return []


def get_sync_result_salvo():
    """Lê o resumo da última varredura (caixas lidas, resultados, erros + quando)."""
    try:
        with get_db() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS ms_sync_state (chave TEXT PRIMARY KEY, valor TEXT, atualizado_em TEXT)")
            row = conn.execute("SELECT valor, atualizado_em FROM ms_sync_state WHERE chave='lab_sync_result'").fetchone()
        if row:
            d = row_to_dict(row)
            out = json.loads(d.get('valor') or '{}')
            out['quando'] = d.get('atualizado_em')
            return out
    except Exception:
        pass
    return None
