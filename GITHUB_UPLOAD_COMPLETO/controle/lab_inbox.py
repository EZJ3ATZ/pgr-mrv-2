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
import os
import re
import html
import json
import logging
from datetime import datetime
from .graph import graph_get
from .db import get_db, row_to_dict

log = logging.getLogger(__name__)

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
            "SELECT id, codigo, tipo, status, data_resultado, atualizado_em FROM amostradores "
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


_RA_RE = re.compile(r'RA\s*[:\-]?\s*(\d{6,})', re.I)


def _ra_do_assunto(assunto):
    """Número do RA no assunto: 'ENC: RA 81962593 - ...' → '81962593'.

    É a chave de dedupe da fila. Usar o assunto inteiro fazia o mesmo laudo
    reencaminhado contar como resultado novo.
    """
    m = _RA_RE.search(assunto or '')
    return m.group(1) if m else None


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


def _baixar_medicoes_com_resultado(conn):
    """Medição química 'aguardando_lab' → 'realizado' quando TODOS os
    amostradores usados nas planilhas químicas da demanda têm resultado (RA).
    Código digitado que não resolve para o inventário BLOQUEIA a baixa
    (conservador: melhor cobrar o código certo do que baixar sem resultado —
    por isso o número do amostrador na planilha não pode estar errado)."""
    meds = [row_to_dict(r) for r in conn.execute(
        "SELECT id, demanda_id, agente FROM medicoes WHERE status='aguardando_lab'"
    ).fetchall()]
    meds = [m for m in meds if m.get('demanda_id')]
    if not meds:
        return 0
    look = {}
    for r in conn.execute(
            "SELECT id, codigo, tipo, data_resultado FROM amostradores "
            "WHERE COALESCE(arquivado,0)=0").fetchall():
        d = row_to_dict(r)
        cod, tp = _norm(d.get('codigo')), _norm(d.get('tipo'))
        for k in {cod, tp + cod}:
            if k:
                look[k] = d
    baixadas = 0
    for did in {m['demanda_id'] for m in meds}:
        usados = [row_to_dict(r) for r in conn.execute(
            """SELECT cqa.id_amostrador, cqa.tipo_amostrador
               FROM coletas_quimico_amostr cqa
               JOIN coletas_quimico cq ON cq.id = cqa.coleta_id
               WHERE cq.demanda_id=? AND COALESCE(cqa.id_amostrador,'')<>''""",
            (did,)).fetchall()]
        if not usados:
            continue  # planilha sem código de amostrador → sem como casar RA (baixa manual)
        todos_com_resultado = True
        for u in usados:
            cod = _norm(u.get('id_amostrador'))
            tp = _norm(u.get('tipo_amostrador'))
            hit = look.get(tp + cod) or look.get(cod)
            if not hit and len(cod) >= 5:
                cands = {d['id']: d for k, d in look.items() if k.endswith(cod)}
                hit = next(iter(cands.values())) if len(cands) == 1 else None
            if not hit or not (hit.get('data_resultado') or ''):
                todos_com_resultado = False
                break
        if not todos_com_resultado:
            continue
        for m in [m for m in meds if m['demanda_id'] == did]:
            conn.execute(
                "UPDATE medicoes SET status='realizado' "
                "WHERE id=? AND status='aguardando_lab'", (m['id'],))
            conn.execute(
                "INSERT INTO eventos (tipo, descricao, ref_id, ref_tipo, criado_em) "
                "VALUES (?,?,?,?,CURRENT_TIMESTAMP)",
                ('medicao_baixada_lab',
                 f"resultado do laboratório chegou → medição química baixada "
                 f"(demanda #{did}, agente {m.get('agente') or '—'})",
                 m['id'], 'medicao'))
            baixadas += 1
        rest = row_to_dict(conn.execute(
            "SELECT COUNT(*) c FROM medicoes WHERE demanda_id=? AND status!='realizado'",
            (did,)).fetchone())['c']
        if rest == 0:
            conn.execute(
                "UPDATE demandas SET status='concluida', atualizado_em=CURRENT_TIMESTAMP "
                "WHERE id=? AND status!='concluida'", (did,))
    return baixadas


def _alertar_resultados_atrasados(conn, dias=None):
    """Amostrador no laboratório há mais de LAB_ATRASO_DIAS (env, default 15)
    sem resultado → evento 'lab_resultado_atrasado' no feed (1x por amostrador)."""
    limite = int(dias or os.environ.get('LAB_ATRASO_DIAS', '15'))
    hoje = datetime.now().date()
    rows = [row_to_dict(r) for r in conn.execute(
        "SELECT id, codigo, data_envio_lab FROM amostradores "
        "WHERE COALESCE(arquivado,0)=0 AND status='laboratorio' "
        "AND COALESCE(data_envio_lab,'')<>'' AND COALESCE(data_resultado,'')=''"
    ).fetchall()]
    novos = 0
    for r in rows:
        try:
            envio = datetime.strptime(str(r['data_envio_lab'])[:10], '%Y-%m-%d').date()
        except Exception:
            continue
        atraso = (hoje - envio).days
        if atraso <= limite:
            continue
        ja = conn.execute(
            "SELECT 1 FROM eventos WHERE tipo='lab_resultado_atrasado' "
            "AND ref_id=? AND ref_tipo='amostrador' LIMIT 1", (r['id'],)).fetchone()
        if ja:
            continue
        conn.execute(
            "INSERT INTO eventos (tipo, descricao, ref_id, ref_tipo, criado_em) "
            "VALUES (?,?,?,?,CURRENT_TIMESTAMP)",
            ('lab_resultado_atrasado',
             f"amostrador {r.get('codigo')}: {atraso} dias no laboratório sem resultado "
             f"(enviado em {str(r['data_envio_lab'])[:10]}, limite {limite}d)",
             r['id'], 'amostrador'))
        novos += 1
    return novos


_COLS_DATA_AMOSTRADOR = ('data_medicao', 'data_envio_lab', 'data_resultado',
                         'data_conclusao', 'cert_validade')


def normalizar_datas_vazias(conn=None):
    """Troca string vazia por NULL nas colunas de data de `amostradores`.

    `COUNT(col)` conta '' como valor: data_medicao reportava 364 preenchidas
    havendo 69 reais (295 eram ''). Qualquer painel que use COUNT(col) mede
    errado, e `''::date` estoura no Postgres. Semanticamente idêntico —
    '' e NULL já significam "sem data" em todo o código.
    """
    def _run(c):
        total = 0
        for col in _COLS_DATA_AMOSTRADOR:
            try:
                cur = c.execute(
                    f"UPDATE amostradores SET {col}=NULL WHERE {col}=''")
                total += getattr(cur, 'rowcount', 0) or 0
            except Exception as e:
                log.warning('[lab_inbox] normalizar %s falhou: %s', col, e)
        return total
    if conn is not None:
        return _run(conn)
    with get_db() as c:
        return _run(c)


def sincronizar_data_medicao_dos_laudos(conn=None):
    """Preenche `amostradores.data_medicao` vazia com a data de amostragem que o
    laudo já declara em `ra_laudos`.

    Retroativo: o `_upsert_ra_laudo` passou a propagar na hora de gravar, mas os
    laudos já lidos ficaram para trás — eram 73 dos 77, e sem essa data o ciclo
    completo (medição → envio → resultado) existia em 6 de 487.
    Nunca sobrescreve data já preenchida.
    """
    def _run(c):
        _ensure_ra_laudos(c)
        rows = [row_to_dict(r) for r in c.execute(
            "SELECT rl.amostrador_id AS aid, rl.data_amostragem AS dt "
            "FROM ra_laudos rl JOIN amostradores a ON a.id = rl.amostrador_id "
            "WHERE COALESCE(rl.data_amostragem,'') <> '' "
            "AND COALESCE(a.data_medicao,'') = ''").fetchall()]
        # Mesmo amostrador pode ter vários laudos: vale a amostragem MAIS ANTIGA
        # (é quando o tubo saiu a campo).
        melhor = {}
        for r in rows:
            iso = _iso_br(r.get('dt'))
            if not iso:
                continue
            aid = r.get('aid')
            if aid not in melhor or iso < melhor[aid]:
                melhor[aid] = iso
        n = 0
        for aid, iso in melhor.items():
            try:
                c.execute("UPDATE amostradores SET data_medicao=?, "
                          "atualizado_em=CURRENT_TIMESTAMP "
                          "WHERE id=? AND COALESCE(data_medicao,'')=''", (iso, aid))
                n += 1
            except Exception as e:
                log.warning('[lab_inbox] propagar data_medicao #%s falhou: %s', aid, e)
        return n
    if conn is not None:
        return _run(conn)
    with get_db() as c:
        return _run(c)


def _completar_casou_por_ra_laudos(resultados):
    """Preenche `casou` de itens gravados por versão anterior, olhando ra_laudos.

    Sem isto a fila só ficaria correta depois do próximo sync (que é quem
    relista os anexos) — e até lá continuaria cobrando vinculação de RA já lido.
    """
    faltam = [r for r in resultados if r.get('ra_num') and not r.get('casou')]
    if not faltam:
        return resultados
    try:
        with get_db() as conn:
            _ensure_ra_laudos(conn)
            for r in faltam:
                rows = conn.execute(
                    "SELECT amostrador_cod FROM ra_laudos WHERE ra_num=? OR ra_num LIKE ?",
                    (r['ra_num'], r['ra_num'] + '-%')).fetchall()
                cods = [row_to_dict(x).get('amostrador_cod') for x in rows]
                r['casou'] = sorted({c for c in cods if c})
    except Exception as e:
        log.warning('[lab_inbox] completar casou por ra_laudos falhou: %s', e)
    return resultados


def _revalidar_nao_cadastrados(resultados):
    """Reconfere `nao_cadastrados` da fila contra o inventário DE AGORA.

    A fila é um retrato gravado no sync. Quando o técnico cadastrava o amostrador
    que o laudo cita, o retrato continuava dizendo "não está no inventário" e a
    linha só sumia no sync seguinte (até 3h depois) — foi a queixa de 05/08/2026
    ("não sei como fazer esse trem sumir"). Aqui o código que JÁ existe sai dos
    faltantes; se aquele amostrador já tem resultado lançado, entra em `casou` e
    o RA sai da fila na hora.
    """
    pend = [r for r in resultados if r.get('nao_cadastrados')]
    if not pend:
        return resultados
    try:
        look = _sistema_lookup()
        for r in pend:
            faltam = []
            casou = list(r.get('casou') or [])
            for cod in r['nao_cadastrados']:
                amos = _match_amostrador(look, cod)
                if not amos:
                    faltam.append(cod)          # segue fora do inventário
                    continue
                # Cadastrado agora: só conta como resolvido se o resultado foi lançado;
                # senão a linha continua na fila, mas pedindo VINCULAR (não cadastrar).
                if str(amos.get('data_resultado') or '').strip() and amos.get('codigo'):
                    if amos['codigo'] not in casou:
                        casou.append(amos['codigo'])
            r['nao_cadastrados'] = faltam
            r['casou'] = sorted({c for c in casou if c})
    except Exception as e:
        log.warning('[lab_inbox] revalidar nao_cadastrados falhou: %s', e)
    return resultados


def registrar_ra_vinculado(aid, ra_num, assunto='', data_email=''):
    """Deixa rastro em `ra_laudos` de que ESTE RA foi vinculado à mão a ESTE amostrador.

    A vinculação manual não gravava nada além do amostrador, e a fila da tela só
    sabia casar RA↔amostrador pelo nome do anexo — então o RA vinculado à mão
    voltava como pendente em toda leitura. Não sobrescreve laudo já extraído do
    PDF (esse tem funcionário, método e resultados; este aqui é só o vínculo).
    """
    ra = str(ra_num or '').strip()
    if not aid or not ra:
        return False
    try:
        with get_db() as conn:
            _ensure_ra_laudos(conn)
            ja = conn.execute("SELECT 1 FROM ra_laudos WHERE amostrador_id=? AND ra_num=?",
                              (aid, ra)).fetchone()
            if ja:
                return True
            row = conn.execute("SELECT codigo FROM amostradores WHERE id=?", (aid,)).fetchone()
            cod = ((row_to_dict(row) or {}).get('codigo') or '') if row else ''
            _upsert_ra_laudo(conn, aid, cod, {'subject': assunto, 'data': data_email},
                             {'ra_num': ra})
        return True
    except Exception as e:
        log.warning('[lab_inbox] registrar RA vinculado falhou: %s', e)
        return False


def _classificar_acao_resultados(resultados):
    """Diz o que cada RA da fila realmente precisa. Muta a lista no lugar.

    A fila mostrava TODO e-mail de resultado com o botão "Vincular", inclusive
    os que o casamento automático já tinha resolvido — dava a impressão de que
    nada era lido sozinho. Pior: quando o código do laudo não está no
    inventário, o seletor livre deixava gravar o resultado em OUTRO amostrador,
    concluindo o tubo errado em silêncio.

    acao:
      resolvido  — casou por anexo, nada pendente (some da fila)
      cadastrar  — o laudo traz código que não existe no inventário
      manual     — nada extraído do nome do anexo (sem PDF, nome fora do padrão)
    """
    for r in resultados:
        casou = r.get('casou') or []
        faltam = r.get('nao_cadastrados') or []
        if faltam:
            r['acao'] = 'cadastrar'
        elif casou:
            r['acao'] = 'resolvido'
        else:
            r['acao'] = 'manual'
    return resultados


def _alertar_nunca_despachados(conn, dias=None):
    """Amostrador coletado que NUNCA teve o despacho ao laboratório registrado.

    Ponto cego que o `_alertar_resultados_atrasados` não cobre: ele exige
    `data_envio_lab` preenchida, então amostrador que saiu de campo e nunca foi
    despachado (ou cujo despacho não foi lançado) fica invisível — em
    30/07/2026 eram 7 dos 8 em `status='laboratorio'`, dois deles parados havia
    66 dias sem que ninguém fosse avisado.

    A data de envio não é carimbada na coleta de propósito (routes.py:1808, fix
    03/07/2026 — carimbar zerava a métrica coleta→lab); este alerta vigia o
    intervalo em vez de preencher a data.

    Idade conta de `data_medicao`; quando ela falta (registro antigo/importado),
    cai para `atualizado_em`. Evento `lab_nunca_despachado`, 1x por amostrador.
    """
    limite = int(dias or os.environ.get('LAB_SEM_ENVIO_DIAS', '7'))
    hoje = datetime.now().date()
    rows = [row_to_dict(r) for r in conn.execute(
        "SELECT id, codigo, data_medicao, atualizado_em FROM amostradores "
        "WHERE COALESCE(arquivado,0)=0 AND status='laboratorio' "
        "AND COALESCE(data_envio_lab,'')='' AND COALESCE(data_resultado,'')=''"
    ).fetchall()]
    novos = 0
    for r in rows:
        base, origem = None, ''
        for campo, rotulo in (('data_medicao', 'medido'), ('atualizado_em', 'sem data de medição, parado')):
            try:
                base = datetime.strptime(str(r[campo])[:10], '%Y-%m-%d').date()
                origem = rotulo
                break
            except (ValueError, TypeError):
                continue
        if base is None:
            continue
        idade = (hoje - base).days
        if idade <= limite:
            continue
        ja = conn.execute(
            "SELECT 1 FROM eventos WHERE tipo='lab_nunca_despachado' "
            "AND ref_id=? AND ref_tipo='amostrador' LIMIT 1", (r['id'],)).fetchone()
        if ja:
            continue
        conn.execute(
            "INSERT INTO eventos (tipo, descricao, ref_id, ref_tipo, criado_em) "
            "VALUES (?,?,?,?,CURRENT_TIMESTAMP)",
            ('lab_nunca_despachado',
             f"amostrador {r.get('codigo')}: {origem} há {idade} dias e o envio ao "
             f"laboratório nunca foi registrado (limite {limite}d)",
             r['id'], 'amostrador'))
        novos += 1
    return novos


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


def _savepoint(conn, fn):
    """Roda fn dentro de um SAVEPOINT. No Postgres, UM statement que falha
    aborta a transação INTEIRA (InFailedSqlTransaction): sem savepoint, os
    try/except daqui mascaravam o 1º erro, todos os statements seguintes
    falhavam junto e o rollback final desfazia até o que tinha dado certo
    (job rodava e não concluía nada). SQLite aceita a mesma sintaxe."""
    conn.execute("SAVEPOINT sp_lab")
    try:
        r = fn()
        conn.execute("RELEASE SAVEPOINT sp_lab")
        return r
    except Exception:
        try:
            conn.execute("ROLLBACK TO SAVEPOINT sp_lab")
            conn.execute("RELEASE SAVEPOINT sp_lab")
        except Exception:
            pass
        raise


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
            # Dedupe pelo NÚMERO DO RA, não pelo assunto: o mesmo laudo
            # reencaminhado ("ENC: RA 81962593") tem assunto diferente e entrava
            # como resultado novo — 8 linhas na fila para 5 RAs distintos.
            _ra = _ra_do_assunto(e['subject'])
            _k = _ra or ((e['subject'] or '').strip().lower(), e['data'])
            novo = _k not in _res_vistos
            if novo:
                _res_vistos.add(_k)
                resultados.append({'assunto': e['subject'], 'data': e['data'],
                                   'caixa': e.get('caixa', ''), 'ra_num': _ra,
                                   'casou': [], 'nao_cadastrados': []})
            item = next((x for x in resultados if x.get('ra_num') == _ra), None) if _ra else None
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
                    # match robusto + MULTI-código: o lab às vezes cola vários códigos
                    # num segmento só do nome (ex.: 'FV75A1X7P75B1'). Antes pegava só o
                    # 3º segmento como 1 código e o RA escapava.
                    achou = _amostradores_do_laudo(nome, look)
                    for amos in achou:
                        aid, dt = amos['id'], e['data']
                        if dt and (aid not in resultado_por_id or dt < resultado_por_id[aid]):
                            resultado_por_id[aid] = dt
                        if item and amos.get('codigo') not in item['casou']:
                            item['casou'].append(amos.get('codigo'))
                    # Código no nome do laudo que NÃO está no inventário: o lab
                    # mandou resultado de um tubo que nunca entrou no sistema.
                    # Não é "faltou vincular" — é cadastro ausente, e vincular a
                    # outro amostrador gravaria o resultado no tubo errado.
                    if item and not achou:
                        cod = _codigo_do_anexo_ra(nome)
                        if cod and cod not in item['nao_cadastrados']:
                            item['nao_cadastrados'].append(cod)
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
    medicoes_baixadas = 0
    alertas_atraso = 0
    alertas_sem_envio = 0
    datas_do_laudo = 0
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
            # Resultado chegou → baixa medições químicas 'aguardando_lab' da OS
            try:
                medicoes_baixadas = _baixar_medicoes_com_resultado(conn)
            except Exception as e:
                log.warning('[lab_inbox] baixa de medicoes por resultado falhou: %s', e)
            # Amostrador parado no lab além do limite → alerta no feed
            try:
                alertas_atraso = _alertar_resultados_atrasados(conn)
            except Exception as e:
                log.warning('[lab_inbox] alerta de atraso falhou: %s', e)
            # Coletado e nunca despachado → o alerta acima não vê (exige data de envio)
            try:
                alertas_sem_envio = _alertar_nunca_despachados(conn)
            except Exception as e:
                log.warning('[lab_inbox] alerta de nunca despachado falhou: %s', e)
            # Data da coleta que o laudo declara → amostrador (só onde falta).
            # Roda no sync porque é a ponta que faltava para medir prazo, e o
            # alerta acima conta idade a partir dela.
            try:
                datas_do_laudo = sincronizar_data_medicao_dos_laudos(conn)
                normalizar_datas_vazias(conn)
            except Exception as e:
                log.warning('[lab_inbox] sincronizar data_medicao falhou: %s', e)
            if max_visto and max_visto != watermark:
                _kv_set(conn, 'lab_watermark', max_visto)
            if pendentes is not None:
                _kv_set(conn, 'lab_pendentes', json.dumps(pendentes, ensure_ascii=False))
            if resultados:
                _classificar_acao_resultados(resultados)
                _kv_set(conn, 'lab_resultados', json.dumps(resultados[:30], ensure_ascii=False))
            _kv_set(conn, 'lab_sync_result', json.dumps({
                'mailboxes_lidas': len(boxes),
                'aplicadas': aplicadas,
                'envio_auto_datados': auto_envio,
                'resultado_auto_datados': auto_resultado,
                'medicoes_baixadas_lab': medicoes_baixadas,
                'alertas_atraso': alertas_atraso,
                'alertas_sem_envio': alertas_sem_envio,
                'datas_do_laudo': datas_do_laudo,
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
        'medicoes_baixadas_lab': medicoes_baixadas,
        'alertas_atraso': alertas_atraso,
        'alertas_sem_envio': alertas_sem_envio,
        'datas_do_laudo': datas_do_laudo,
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
    """Lê a última lista de resultados/laudos (RA) recebidos do lab (aba Vencimento).

    O que a fila PEDE (`acao`) é recalculado na leitura, não lido do retrato do
    sync: cadastro e vinculação feitos depois da varredura precisam tirar a linha
    da tela na hora. `ra_num` de versão antiga também é completado aqui.
    """
    try:
        with get_db() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS ms_sync_state (chave TEXT PRIMARY KEY, valor TEXT, atualizado_em TEXT)")
            row = conn.execute("SELECT valor FROM ms_sync_state WHERE chave='lab_resultados'").fetchone()
        if row:
            itens = json.loads(row_to_dict(row).get('valor') or '[]')
            if not isinstance(itens, list):
                return []
            for r in itens:
                if isinstance(r, dict) and not r.get('ra_num'):
                    r['ra_num'] = _ra_do_assunto(r.get('assunto'))
            # Dedupe por RA — a fila antiga conta o reencaminhado como novo.
            vistos, out = set(), []
            for r in itens:
                if not isinstance(r, dict):
                    continue
                k = r.get('ra_num') or (r.get('assunto'), r.get('data'))
                if k in vistos:
                    continue
                vistos.add(k)
                out.append(r)
            _completar_casou_por_ra_laudos(out)
            _revalidar_nao_cadastrados(out)
            # Reclassifica SEMPRE. Confiar no `acao` gravado no sync era o motivo de
            # a linha não sair da tela por ação do técnico: o retrato envelhece, e o
            # cadastro/vinculação feitos depois dele não eram levados em conta.
            _classificar_acao_resultados(out)
            return out
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


# ══════════════════════════════════════════════════════════════════════
#  EXTRATOR DE RA (conteúdo do PDF) + BACKFILL HISTÓRICO
#  ---------------------------------------------------------------------
#  O sync normal (sincronizar_lab) só olha os ~120 e-mails recentes de cada
#  caixa → RAs de amostradores parados há meses ficaram fora da janela e nunca
#  concluíram (bug dos "258 dias parados"). Aqui usamos $search por remetente
#  do lab (traz TODO o histórico, ~11 meses) + leitura do PDF do laudo para
#  extrair amostrador + funcionário + resultados e fechar o ciclo sozinho.
#  Validado contra 4 laudos reais (08/07/2026).
# ══════════════════════════════════════════════════════════════════════

def parse_ra_pdf(nome, texto):
    """Extrai campos estruturados de UM laudo de RA (UniScientific).
    O texto do PyMuPDF vem com rótulos e valores desalinhados, então usamos
    âncoras robustas em vez de 'rótulo: valor' sequencial. Retorna dict com
    amostrador, ra_num, funcionario, funcao, setor, tecnico, metodo, datas e
    resultados[] (agente/unidade/valor)."""
    t = texto or ""
    linhas = [l.strip() for l in t.splitlines() if l.strip()]
    d = {}

    # Nº do RA: "Relatório de Análise - Nº 81960959-1" ou dos 2 primeiros segmentos do nome
    m = re.search(r'Relat[oó]rio de An[aá]lise\s*-?\s*N[ºo°]?\s*([\d-]+)', t, re.I)
    partes = (nome or "").rsplit(".", 1)[0].split("-")
    d["ra_num"] = m.group(1) if m else ("-".join(partes[:2]) if len(partes) >= 2 else "")

    # Amostrador: 3º segmento do nome do arquivo, confirmado no corpo
    cod_nome = partes[2].strip() if len(partes) >= 3 else ""
    cods_corpo = re.findall(r'\b[A-Z]{2}\d{4,}[A-Z0-9]*\b', t)
    d["amostrador"] = cod_nome or (cods_corpo[0] if cods_corpo else "")

    # Função: "CALDEIREIRO (A)"
    m = re.search(r'Fun[cç][aã]o:\s*([A-ZÀ-Ú].*)', t, re.I)
    d["funcao"] = m.group(1).strip() if m else ""

    # Funcionário avaliado: linha em CAIXA ALTA imediatamente antes de "Função:"
    d["funcionario"] = ""
    for i, l in enumerate(linhas):
        if re.match(r'Fun[cç][aã]o:', l, re.I) and i > 0:
            cand = linhas[i - 1]
            if re.match(r'^[A-ZÀ-Ú][A-ZÀ-Ú\s]+$', cand) and len(cand) > 5:
                d["funcionario"] = cand
            break

    # Responsável pela amostragem (técnico): nome conhecido em CAIXA ALTA
    d["tecnico"] = ""
    for tecn in ("HELBERT", "WESLEY", "MATHEUS"):
        if tecn in t.upper():
            m = re.search(rf'({tecn}[A-ZÀ-Ú\s]+)', t.upper())
            if m:
                d["tecnico"] = m.group(1).strip()
                break

    # Setor: linha em CAIXA ALTA imediatamente antes do nome do técnico
    d["setor"] = ""
    if d["tecnico"]:
        primeiro = d["tecnico"].split()[0]
        for i, l in enumerate(linhas):
            if l.upper().startswith(primeiro) and i > 0:
                cand = linhas[i - 1]
                if re.match(r'^[A-ZÀ-Ú][A-ZÀ-Ú\s/]{2,}$', cand) and cand != d["funcionario"]:
                    d["setor"] = cand
                break

    # Método: linha logo após "MÉTODO"
    m = re.search(r'M[EÉ]TODO.*?\n\s*(NIOSH[^\n]+|OSHA[^\n]+|MDHS[^\n]+)', t, re.I | re.S)
    d["metodo"] = m.group(1).strip() if m else ""

    # Data da coleta: data perto de "amostragem" (não confundir com emissão/recebimento)
    d["data_amostragem"] = ""
    for i, l in enumerate(linhas):
        if re.match(r'^\d{2}/\d{2}/\d{4}$', l):
            viz = " ".join(linhas[max(0, i - 1):i + 3]).lower()
            if "amostragem" in viz:
                d["data_amostragem"] = l
                break
    m = re.search(r'Recebimento da Amostra:\s*(\d{2}/\d{2}/\d{4})', t, re.I)
    d["data_recebimento"] = m.group(1) if m else ""

    # Resultados: agente (linha anterior) + unidade + 1º valor numérico (MP 8h)
    resultados = []
    for i, l in enumerate(linhas):
        if l.startswith(("mg/m", "ppm", "f/cc")):
            agente = linhas[i - 1] if i > 0 else ""
            valor = ""
            for j in range(i + 1, min(i + 4, len(linhas))):
                if re.match(r'^[<>]?\s*[\d,]+$', linhas[j]) or linhas[j] == "-":
                    valor = linhas[j]
                    break
            if agente and not agente.startswith(("mg", "ppm", "MP", "Teto", "TWA", "STEL")):
                resultados.append({"agente": agente, "unidade": l, "resultado": valor})
    d["resultados"] = resultados
    return d


def _search_lab_emails(box, top=200):
    """RAs do lab em UMA caixa via $search (traz TODO o histórico — não fica preso
    à janela de e-mails recentes). Só assuntos que começam com 'RA ' e têm anexo."""
    try:
        data = graph_get(f"/users/{box}/messages"
                         f'?$search="from:{LAB_DOM}"&$top={top}'
                         f"&$select=id,subject,from,receivedDateTime,hasAttachments")
    except Exception:
        return []
    out = []
    for m in data.get('value', []):
        sub = (m.get('subject', '') or '')
        if sub.strip().upper().startswith('RA ') and m.get('hasAttachments'):
            out.append({'id': m['id'], 'subject': sub, 'caixa': box,
                        'data': (m.get('receivedDateTime') or '')[:10]})
    return out


def _match_amostrador(look, cod):
    """Casa um código (do nome do laudo) com o inventário: exato, tipo+código ou
    sufixo único. Devolve o dict do amostrador ou None."""
    cod = _norm(cod)
    if not cod:
        return None
    amos = look.get(cod)
    if amos:
        return amos
    if len(cod) >= 5:
        cands = {d['id']: d for k, d in look.items() if k.endswith(cod)}
        if len(cands) == 1:
            return next(iter(cands.values()))
    return None


def _amostradores_do_laudo(nome, look):
    """TODOS os amostradores do inventário citados no NOME do laudo (PDF). Na maioria
    o 3º segmento traz 1 código, mas o lab às vezes CONCATENA vários num segmento só
    (ex.: 'FV75A1X7P75B1' = FV75A1 + X7P75B1, ou vários numa OS grande) — o parser
    antigo pegava o 3º segmento inteiro como 1 código e não casava nenhum. Aqui, além
    do 3º segmento (match preciso), varremos o nome inteiro contra o inventário
    (reverso, via _codigos_no_texto) e pegamos os códigos colados. Dedup por id."""
    achados = {}
    cod = _norm(_codigo_do_anexo_ra(nome))
    a = _match_amostrador(look, cod) if cod else None
    if a:
        achados[a['id']] = a
    for c in _codigos_no_texto(nome or '', look):
        a = look.get(c)
        if a:
            achados[a['id']] = a
    return list(achados.values())


def _ensure_ra_laudos(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ra_laudos ("
        "amostrador_id INTEGER, amostrador_cod TEXT, ra_num TEXT, "
        "funcionario TEXT, funcao TEXT, setor TEXT, tecnico TEXT, metodo TEXT, "
        "data_amostragem TEXT, data_recebimento TEXT, resultados TEXT, "
        "assunto TEXT, data_email TEXT, criado_em TEXT)")


def _iso_br(data_br):
    """'30/06/2026' → '2026-06-30'. O PDF do laudo traz data no formato BR e as
    colunas de amostradores guardam ISO — misturar quebra todo cálculo de prazo."""
    m = re.match(r'^\s*(\d{2})/(\d{2})/(\d{4})\s*$', str(data_br or ''))
    if not m:
        return ''
    dia, mes, ano = m.groups()
    try:
        datetime(int(ano), int(mes), int(dia))     # rejeita 31/02
    except ValueError:
        return ''
    return f'{ano}-{mes}-{dia}'


def _upsert_ra_laudo(conn, aid, cod, email, d):
    """Grava o laudo extraído (DELETE+INSERT por amostrador+RA — DB-agnóstico).

    Também propaga a DATA DA AMOSTRAGEM do laudo para `amostradores.data_medicao`
    quando ela está vazia: o PDF traz essa data em 100% dos laudos lidos, mas 73
    dos 77 amostradores estavam sem ela, e sem a ponta inicial não há como medir
    coleta → envio → resultado (o ciclo completo existia em 6 de 487, 1,2%).
    Nunca sobrescreve data já preenchida — o que o técnico lançou vence.
    """
    ra = d.get('ra_num') or ''
    conn.execute("DELETE FROM ra_laudos WHERE amostrador_id=? AND ra_num=?", (aid, ra))
    conn.execute(
        "INSERT INTO ra_laudos (amostrador_id, amostrador_cod, ra_num, funcionario, "
        "funcao, setor, tecnico, metodo, data_amostragem, data_recebimento, "
        "resultados, assunto, data_email, criado_em) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
        (aid, cod, ra, d.get('funcionario', ''), d.get('funcao', ''), d.get('setor', ''),
         d.get('tecnico', ''), d.get('metodo', ''), d.get('data_amostragem', ''),
         d.get('data_recebimento', ''), json.dumps(d.get('resultados', []), ensure_ascii=False),
         email.get('subject', ''), email.get('data', '')))
    iso = _iso_br(d.get('data_amostragem'))
    if iso:
        conn.execute(
            "UPDATE amostradores SET data_medicao=?, atualizado_em=CURRENT_TIMESTAMP "
            "WHERE id=? AND COALESCE(data_medicao,'')=''", (iso, aid))


def backfill_ras(apply=False, top=200):
    """Varre TODO o histórico de RAs do lab (via $search), casa cada laudo pelo
    código do amostrador e — no apply — seta data_resultado, conclui o amostrador
    que ainda está no laboratório e guarda o laudo extraído (funcionário/resultados).
    Preview (apply=False) não grava nada: só relata o que casaria."""
    look = _sistema_lookup()
    boxes = _mailboxes()
    vistos_ra = set()
    datas = []
    plano = []      # (amostrador_id, data_resultado) — só quem está em 'laboratorio'
    laudos = []     # (amostrador_id, cod, email, parsed) — para gravar
    report = {'ras': 0, 'pdfs': 0, 'casaram': 0, 'concluiriam': 0,
              'ja_concluidos': 0, 'fora_do_lab': 0, 'sem_match': [], 'amostradores': []}

    for box in boxes:
        for e in _search_lab_emails(box, top):
            ra_key = e['subject'].strip().lower()
            if ra_key in vistos_ra:
                continue
            vistos_ra.add(ra_key)
            report['ras'] += 1
            if e['data']:
                datas.append(e['data'])
            try:
                metas = graph_get(f"/users/{box}/messages/{e['id']}/attachments"
                                  f"?$select=id,name,contentType,size").get('value', [])
            except Exception:
                continue
            for meta in metas:
                nome = meta.get('name', '')
                if not nome.lower().endswith('.pdf'):
                    continue
                report['pdfs'] += 1
                # multi-código: um PDF pode citar vários amostradores (colados no nome)
                amoslist = _amostradores_do_laudo(nome, look)
                if not amoslist:
                    report['sem_match'].append(_codigo_do_anexo_ra(nome) or nome[:40])
                    continue
                parsed = None   # baixa/parseia o PDF só 1x por anexo (reusa p/ cada amostrador)
                for amos in amoslist:
                    report['casaram'] += 1
                    st = (amos.get('status') or '').lower()
                    if st in ('concluido', 'devolvido'):
                        report['ja_concluidos'] += 1
                    elif st == 'laboratorio':
                        report['concluiriam'] += 1
                        report['amostradores'].append(amos.get('codigo'))
                        if apply:
                            plano.append((amos['id'], e['data']))
                    else:
                        report['fora_do_lab'] += 1
                    if apply:
                        if parsed is None:
                            try:
                                full = graph_get(f"/users/{box}/messages/{e['id']}/attachments/{meta['id']}")
                                txt = _extrair_texto_anexo(nome, meta.get('contentType', ''), full.get('contentBytes'))
                                parsed = parse_ra_pdf(nome, txt)
                            except Exception:
                                parsed = {'amostrador': amos.get('codigo'), 'ra_num': ''}
                        laudos.append((amos['id'], amos.get('codigo'), e, parsed))

    if datas:
        ds = sorted(datas)
        report['periodo'] = f"{ds[0]} .. {ds[-1]}"
    medicoes = 0
    if apply and (plano or laudos):
        erros = []
        with get_db() as conn:
            _ensure_ra_laudos(conn)
            for aid, dt in plano:
                try:
                    _savepoint(conn, lambda: conn.execute(
                        "UPDATE amostradores SET data_resultado=COALESCE(NULLIF(data_resultado,''),?), "
                        "status='concluido', atualizado_em=CURRENT_TIMESTAMP "
                        "WHERE id=? AND status='laboratorio'", (dt, aid)))
                except Exception as ex:
                    log.warning('[backfill_ras] concluir amostrador #%s falhou: %s', aid, ex)
                    erros.append(f'concluir amostrador #{aid}: {ex}')
            for aid, cod, e, d in laudos:
                try:
                    _savepoint(conn, lambda: _upsert_ra_laudo(conn, aid, cod, e, d))
                except Exception as ex:
                    log.warning('[backfill_ras] upsert laudo falhou (%s): %s', cod, ex)
                    erros.append(f'laudo {cod}: {ex}')
            try:
                medicoes = _savepoint(conn, lambda: _baixar_medicoes_com_resultado(conn))
            except Exception as ex:
                log.warning('[backfill_ras] baixa de medições falhou: %s', ex)
                erros.append(f'baixa de medições: {ex}')
            report['erros'] = erros[:20]
            try:
                _savepoint(conn, lambda: _kv_set(
                    conn, 'ra_backfill_result',
                    json.dumps({**report, 'medicoes_baixadas': medicoes}, ensure_ascii=False)))
            except Exception as ex:
                log.warning('[backfill_ras] gravar resultado falhou: %s', ex)

    report['modo'] = 'APLICADO' if apply else 'PREVIEW (nada gravado)'
    report['medicoes_baixadas'] = medicoes
    report['sem_match'] = sorted(set(report['sem_match']))[:40]
    report['amostradores'] = sorted(set(report['amostradores']))
    return report
