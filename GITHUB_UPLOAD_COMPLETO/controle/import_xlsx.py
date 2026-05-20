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


def importar_medicoes(file_bytes):
    """Importa planilha Controle de Medicoes - Helbert e Wesley.
    Cada linha eh um agente de uma OS. Agrupa por (OS, empresa) em demandas
    e cria 1 medicao por agente.
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    inserted_demandas = 0
    inserted_medicoes = 0
    errors = []

    for sheet_name in wb.sheetnames:
        if _norm(sheet_name) in ('DADOS',):
            continue
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        if not rows: continue

        # Detectar cabecalho (primeira linha com UNIDADE/AGENTE)
        header_idx = None
        for i, row in enumerate(rows):
            txt = ' '.join(_norm(c) for c in row if c)
            if 'UNIDADE' in txt and 'AGENTE' in txt:
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

        for row in rows[header_idx + 1:]:
            unidade = _str(row[c_unid])   if c_unid   is not None else ''
            os_num  = _str(row[c_os])     if c_os     is not None else ''
            agente  = _str(row[c_agente]) if c_agente is not None else ''
            if not (unidade or os_num) or not agente:
                continue
            resp    = _str(row[c_resp])   if c_resp   is not None else ''
            amostr  = _str(row[c_amostr]) if c_amostr is not None else ''
            pontos  = _to_int(row[c_pontos] if c_pontos is not None else None, 1)
            aval    = _to_int(row[c_aval]   if c_aval   is not None else None, 0)
            laudar  = _str(row[c_laudar]) if c_laudar is not None else ''
            obs     = _str(row[c_obs])    if c_obs    is not None else ''

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
            status_med = 'realizado' if aval >= pontos and pontos > 0 else 'pendente'
            tipo_amostr = ''
            am = re.search(r'\b([A-Z]{2,5})\b', amostr.upper()) if amostr else None
            if am: tipo_amostr = am.group(1)

            with get_db() as conn:
                conn.execute("""
                    INSERT INTO medicoes
                        (demanda_id, agente, tipo_amostrador, qtd_pontos_prevista,
                         qtd_pontos_feita, necessita_laudo, status, observacao)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (demanda_cache[key], agente, tipo_amostr, pontos, aval,
                     laudar[:1] if laudar else '', status_med, obs))
                inserted_medicoes += 1

    return {
        'demandas_inseridas': inserted_demandas,
        'medicoes_inseridas': inserted_medicoes,
        'errors': errors
    }
