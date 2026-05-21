# -*- coding: utf-8 -*-
"""Importadores das planilhas atuais para o banco."""
import io
import re
import unicodedata
from datetime import datetime, date

import openpyxl

from .db import get_db, upsert_empresa


def _norm(s):
    if s is None: return ''
    return unicodedata.normalize('NFD', str(s)).encode('ascii', 'ignore').decode('ascii').upper().strip()


def _str(v):
    if v is None: return ''
    if isinstance(v, (datetime, date)):
        try: return v.strftime('%Y-%m-%d')
        except: return str(v)
    return str(v).strip()


def _to_int(v, default=0):
    try:
        s = _str(v).split(',')[0].split('.')[0]
        if not s: return default
        # FALTA 1, FALTA 2 -> 0 (sem dado de quantidade)
        if not s.lstrip('-').isdigit(): return default
        return int(s)
    except Exception:
        return default


def importar_amostradores(file_bytes):
    """Importa planilha CONTROLE AMOSTRADORES 2026.
    Espera abas '2026', '2025' ou similar com colunas:
    Status | TIPO DE AMOSTRADOR | CODIGO | DATA DE ENTRADA | Empresa | Avaliador | Data da medicao
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    inserted = 0
    updated = 0
    errors = []

    for sheet_name in wb.sheetnames:
        if _norm(sheet_name) in ('DADOS',):  # aba de referencia
            continue
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        if not rows: continue

        # Detectar cabecalho (primeira linha com STATUS)
        header_idx = None
        for i, row in enumerate(rows):
            txt = ' '.join(_norm(c) for c in row if c)
            if 'STATUS' in txt and 'AMOSTRADOR' in txt:
                header_idx = i
                break
        if header_idx is None:
            continue

        header_norm = [_norm(c) for c in rows[header_idx]]

        def col_idx(*kws):
            for j, h in enumerate(header_norm):
                for kw in kws:
                    if kw in h:
                        return j
            return None

        c_status   = col_idx('STATUS')
        c_tipo     = col_idx('TIPO DE AMOSTRADOR', 'TIPO')
        c_codigo   = col_idx('CODIGO')
        c_entrada  = col_idx('DATA DE ENTRADA')
        c_empresa  = col_idx('EMPRESA')
        c_avaliad  = col_idx('AVALIADOR')
        c_medicao  = col_idx('DATA DA MEDICAO')

        for row in rows[header_idx + 1:]:
            codigo = _str(row[c_codigo]) if c_codigo is not None else ''
            tipo   = _str(row[c_tipo])   if c_tipo   is not None else ''
            if not codigo or not tipo:
                continue
            status   = _str(row[c_status])   if c_status   is not None else 'Estoque'
            entrada  = _str(row[c_entrada])  if c_entrada  is not None else ''
            empresa  = _str(row[c_empresa])  if c_empresa  is not None else ''
            avaliad  = _str(row[c_avaliad])  if c_avaliad  is not None else ''
            medicao  = _str(row[c_medicao])  if c_medicao  is not None else ''
            empresa_id = upsert_empresa('', empresa) if empresa and empresa != '-' else None

            with get_db() as conn:
                existing = conn.execute(
                    'SELECT id FROM amostradores WHERE codigo = ? AND tipo = ?',
                    (codigo, tipo)
                ).fetchone()
                if existing:
                    conn.execute("""
                        UPDATE amostradores SET status=?, data_entrada=?, empresa_id=?,
                            avaliador=?, data_medicao=?, atualizado_em=CURRENT_TIMESTAMP
                        WHERE id=?""",
                        (status, entrada, empresa_id, avaliad, medicao, existing['id']))
                    updated += 1
                else:
                    try:
                        conn.execute("""
                            INSERT INTO amostradores
                                (codigo, tipo, status, data_entrada, empresa_id, avaliador, data_medicao)
                            VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (codigo, tipo, status, entrada, empresa_id, avaliad, medicao))
                        inserted += 1
                    except Exception as e:
                        errors.append(f'Linha codigo={codigo}: {e}')

    return {'inserted': inserted, 'updated': updated, 'errors': errors}


def importar_demandas_planner(file_bytes):
    """Importa formato 'Demandas_Medicoes' extraido do Microsoft Planner.
    Colunas: Nome da Empresa (OS, NOME) | Data de Criacao | Prazo | Responsaveis
    Atualiza demandas existentes (match por numero_os) ou cria novas.
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    inseridas = 0
    atualizadas = 0
    erros = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        if not rows: continue

        # Achar cabecalho
        header_idx = None
        for i, row in enumerate(rows):
            txt = ' '.join(_norm(c) for c in row if c)
            if 'EMPRESA' in txt and ('CRIACAO' in txt or 'PREVISAO' in txt or 'RESPONSAVEIS' in txt):
                header_idx = i; break
        if header_idx is None: continue

        header_norm = [_norm(c) for c in rows[header_idx]]
        def col_idx(*kws):
            for j, h in enumerate(header_norm):
                for kw in kws:
                    if kw in h:
                        return j
            return None

        c_emp = col_idx('NOME DA EMPRESA', 'EMPRESA')
        c_cri = col_idx('DATA DE CRIACAO', 'CRIACAO')
        c_pra = col_idx('PREVISAO', 'PRAZO', 'CONCLUSAO')
        c_res = col_idx('RESPONSAVEIS', 'RESPONSAVEL')

        for row in rows[header_idx + 1:]:
            if not row or not row[c_emp]: continue
            raw = _str(row[c_emp])
            # Parse "OS, NOME EMPRESA"
            os_num, nome = '', raw
            if ',' in raw:
                parts = raw.split(',', 1)
                if parts[0].strip().isdigit():
                    os_num = parts[0].strip()
                    nome = parts[1].strip()
            data_cri = _str(row[c_cri]) if c_cri is not None else ''
            prazo    = _str(row[c_pra]) if c_pra is not None else ''
            resp     = _str(row[c_res]) if c_res is not None else ''
            if not nome: continue

            empresa_id = upsert_empresa('', nome, contato=resp)
            if not empresa_id: continue

            with get_db() as conn:
                # Match por OS
                existing = None
                if os_num:
                    existing = conn.execute(
                        'SELECT id FROM demandas WHERE numero_os = ?',
                        (os_num,)).fetchone()
                if existing:
                    conn.execute("""
                        UPDATE demandas
                        SET empresa_id=?, prazo=?, observacao=COALESCE(observacao,'') || ' | Responsavel: ' || ?,
                            criado_em=COALESCE(NULLIF(?, ''), criado_em)
                        WHERE id=?""",
                        (empresa_id, prazo, resp, _parse_data(data_cri), existing['id']))
                    atualizadas += 1
                else:
                    conn.execute("""
                        INSERT INTO demandas
                            (numero_os, empresa_id, prazo, status, observacao, criado_em)
                        VALUES (?, ?, ?, 'pendente', ?, ?)""",
                        (os_num, empresa_id, prazo,
                         f'Responsavel: {resp}',
                         _parse_data(data_cri) or None))
                    inseridas += 1

    return {'demandas_inseridas': inseridas, 'demandas_atualizadas': atualizadas, 'errors': erros}


def _parse_data(s):
    """Converte 'dd/mm/aaaa' para 'aaaa-mm-dd HH:MM:SS' (ISO SQLite). Mantem se ja for ISO."""
    if not s: return ''
    s = s.strip()
    if not s: return ''
    # dd/mm/aaaa
    if len(s) == 10 and s[2] == '/' and s[5] == '/':
        try:
            d, m, y = s.split('/')
            return f'{y}-{m.zfill(2)}-{d.zfill(2)} 12:00:00'
        except: return ''
    return s


def _is_finalizada(cell):
    """Detecta se a celula tem fundo VERDE (= demanda finalizada).
    Considera verde:
      - Theme 9 (accent6) qualquer tint - padrao do template "Verde - Accent 6"
      - Theme 6 (accent3) qualquer tint - verde escuro
      - RGB direto com componente G dominante (G alto, R e B baixos)
    """
    try:
        fill = cell.fill
        if not fill or fill.patternType != 'solid':
            return False
        fg = fill.fgColor
        if fg.type == 'theme' and fg.theme in (6, 9):
            return True
        if fg.type == 'rgb' and fg.rgb:
            rgb = str(fg.rgb)
            if len(rgb) == 8: rgb = rgb[2:]  # remove alpha
            if len(rgb) == 6:
                r = int(rgb[0:2], 16); g = int(rgb[2:4], 16); b = int(rgb[4:6], 16)
                # Verde: G alto, R e B menores
                if g > 150 and g > r + 30 and g > b + 30:
                    return True
    except Exception:
        pass
    return False


def importar_medicoes(file_bytes):
    """Importa planilha Controle de Medicoes - Helbert e Wesley.
    Cada linha eh um agente de uma OS. Agrupa por (OS, empresa) em demandas
    e cria 1 medicao por agente.

    DETECCAO DE COR: Se a celula da UNIDADE estiver pintada de VERDE,
    a medicao eh marcada como 'realizado' (demanda ja foi finalizada).
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    inserted_demandas = 0
    inserted_medicoes = 0
    finalizadas_por_cor = 0
    errors = []

    for sheet_name in wb.sheetnames:
        if _norm(sheet_name) in ('DADOS',):
            continue
        ws = wb[sheet_name]
        # Pegar rows como CELULAS (nao values_only) para ler formato
        all_cells = list(ws.iter_rows(values_only=False))
        if not all_cells: continue

        # Detectar cabecalho
        header_idx = None
        for i, cells in enumerate(all_cells):
            txt = ' '.join(_norm(c.value) for c in cells if c.value is not None)
            if 'UNIDADE' in txt and 'AGENTE' in txt:
                header_idx = i
                break
        if header_idx is None:
            continue

        header_norm = [_norm(c.value) for c in all_cells[header_idx]]
        def col_idx(*kws):
            for j, h in enumerate(header_norm):
                for kw in kws:
                    if kw in h:
                        return j
            return None

        c_unid    = col_idx('UNIDADE')
        c_os      = col_idx('NUMERO DA O.S', 'NUMERO DA OS', 'NUMERO O.S', 'NUMERO OS')
        c_resp    = col_idx('RESPONSAVEL')
        c_agente  = col_idx('AGENTE')
        c_amostr  = col_idx('AMOSTRADOR')
        c_pontos  = col_idx('QUANTIDADE DE PONTOS', 'QTD PONTOS')
        c_aval    = col_idx('PONTOS AVALIADOS')
        c_laudar  = col_idx('LAUDAR')
        c_obs     = col_idx('OBSERVAC')

        # Cache de demandas por (empresa_id, os)
        demanda_cache = {}

        for cells in all_cells[header_idx + 1:]:
            def cv(idx):
                if idx is None or idx >= len(cells): return ''
                return _str(cells[idx].value)
            unidade = cv(c_unid)
            os_num  = cv(c_os)
            agente  = cv(c_agente)
            if not (unidade or os_num) or not agente:
                continue
            resp    = cv(c_resp)
            amostr  = cv(c_amostr)
            pontos  = _to_int(cells[c_pontos].value if c_pontos is not None and c_pontos < len(cells) else None, 1)
            aval    = _to_int(cells[c_aval].value if c_aval is not None and c_aval < len(cells) else None, 0)
            laudar  = cv(c_laudar)
            obs     = cv(c_obs)

            # DETECCAO DE COR VERDE = finalizada
            # Checa varias celulas da linha (unidade, agente, amostrador, pontos)
            verde = False
            for ci in (c_unid, c_agente, c_amostr, c_pontos):
                if ci is not None and ci < len(cells) and _is_finalizada(cells[ci]):
                    verde = True
                    break

            # Parse responsavel: nome (tel) email
            contato = resp.split('(')[0].strip() if resp else ''
            tel = ''
            tm = re.search(r'\((\d{2})\)\s*([\d\-\s]+)', resp)
            if tm: tel = f'({tm.group(1)}) {tm.group(2).strip()}'
            email = ''
            em = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', resp)
            if em: email = em.group()

            empresa_id = upsert_empresa('', unidade,
                contato=contato, telefone=tel, email=email)
            if not empresa_id:
                continue

            key = (empresa_id, os_num)
            if key not in demanda_cache:
                with get_db() as conn:
                    existing = conn.execute(
                        'SELECT id FROM demandas WHERE empresa_id=? AND numero_os=?',
                        (empresa_id, os_num)).fetchone()
                    if existing:
                        demanda_cache[key] = existing['id']
                    else:
                        cur = conn.execute(
                            'INSERT INTO demandas (numero_os, empresa_id, status, observacao) '
                            'VALUES (?, ?, ?, ?)',
                            (os_num, empresa_id, 'pendente', obs))
                        demanda_cache[key] = cur.lastrowid
                        inserted_demandas += 1

            # Insere medicao
            # Status: verde (planilha) > aval==pontos > parcial > pendente
            if verde:
                status_med = 'realizado'
                qtd_feita = pontos if pontos > 0 else 1
                finalizadas_por_cor += 1
            elif aval >= pontos and pontos > 0:
                status_med = 'realizado'
                qtd_feita = aval
            elif aval > 0:
                status_med = 'parcial'
                qtd_feita = aval
            else:
                status_med = 'pendente'
                qtd_feita = 0

            tipo_amostr = ''
            am = re.search(r'\b([A-Z]{2,5})\b', amostr.upper()) if amostr else None
            if am: tipo_amostr = am.group(1)

            with get_db() as conn:
                conn.execute("""
                    INSERT INTO medicoes
                        (demanda_id, agente, tipo_amostrador, qtd_pontos_prevista,
                         qtd_pontos_feita, necessita_laudo, status, observacao)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (demanda_cache[key], agente, tipo_amostr, pontos, qtd_feita,
                     laudar[:1] if laudar else '', status_med, obs))
                inserted_medicoes += 1

    # Atualizar status das demandas: se TODAS medicoes realizadas -> concluida
    with get_db() as conn:
        conn.execute("""
            UPDATE demandas SET status='concluida'
            WHERE id IN (
                SELECT d.id FROM demandas d
                WHERE NOT EXISTS (
                    SELECT 1 FROM medicoes m
                    WHERE m.demanda_id = d.id AND m.status != 'realizado'
                )
                AND EXISTS (SELECT 1 FROM medicoes m WHERE m.demanda_id = d.id)
            )
        """)

    return {
        'demandas_inseridas': inserted_demandas,
        'medicoes_inseridas': inserted_medicoes,
        'finalizadas_por_cor_verde': finalizadas_por_cor,
        'errors': errors
    }
