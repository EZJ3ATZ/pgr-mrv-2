# -*- coding: utf-8 -*-
"""Resultado analítico do laboratório como DADO, não como parágrafo de .docx.

Até 05/08/2026 a concentração de um agente químico só existia dentro do documento
gerado: `medicoes` tem status, `coletas_quimico_amostr` tem vazão/volume, e o valor
lido do laudo (RA) morria em `ra_laudos.resultados`, coluna que ninguém lia.

Aqui cada resultado vira uma linha por (amostrador, agente, **fonte**):

  fonte='pdf'       — o laudo do laboratório, lido do PDF
  fonte='digitado'  — o que o técnico lançou ao gerar o laudo

As duas convivem de propósito. Quando divergem, **nenhuma vence**: abre-se uma
divergência na Camada de Consistência para uma pessoa decidir (decisão do Matheus).
"""
import logging
import re

from .db import get_db, row_to_dict

log = logging.getLogger(__name__)

# Diferença relativa aceita entre o PDF e o digitado. Abaixo disso é arredondamento
# de transcrição; acima, alguém digitou outro número.
TOLERANCIA = 0.005


def _ph():
    # _PGCursor.execute() converte ? → %s sozinho — usar sempre ?, como em consistencia.py
    return '?'


def norm_cod(cod):
    return re.sub(r'\s+', '', str(cod or '')).upper()


def norm_agente(agente):
    """Chave do agente: sem acento não, só caixa e espaço — 'Tolueno ' == 'TOLUENO'."""
    return re.sub(r'\s+', ' ', str(agente or '')).strip().upper()


def separar_valor(concentracao):
    """'<0,007 ppm' → ('<0,007', 'ppm', 0.007, True); '47,28085 ppm' → (..., False).

    O laudo escreve o não detectado como '<' + limite de quantificação. Guardamos o
    texto como veio (é o que vai no documento) e o número só para comparar.
    """
    txt = str(concentracao or '').strip()
    if not txt:
        return '', '', None, False
    m = re.match(r'^\s*([<>]?\s*[\d.,]+)\s*(.*)$', txt)
    if not m:
        return txt, '', None, False
    valor_txt = re.sub(r'\s+', '', m.group(1))
    unidade = m.group(2).strip()
    nao_det = valor_txt.startswith('<')
    bruto = valor_txt.lstrip('<>')
    # 1.234,56 → 1234.56 ; 0,007 → 0.007
    bruto = bruto.replace('.', '') if (',' in bruto and '.' in bruto) else bruto
    try:
        num = float(bruto.replace(',', '.'))
    except ValueError:
        num = None
    return valor_txt, unidade, num, nao_det


def divergem(a, b):
    """(divergem?, motivo). `a` e `b` são linhas de resultados_lab (dict-like)."""
    na, nb = a.get('valor_num'), b.get('valor_num')
    if na is None or nb is None:
        return False, ''            # sem número dos dois lados não há comparação
    if bool(a.get('nao_detectado')) != bool(b.get('nao_detectado')):
        return True, ('um lado diz NÃO DETECTADO (<) e o outro traz valor medido — '
                      'muda a conclusão do laudo')
    ref = max(abs(na), abs(nb)) or 1.0
    if abs(na - nb) / ref > TOLERANCIA:
        return True, f'valores diferentes: {a.get("valor_txt")} × {b.get("valor_txt")}'
    return False, ''


def _buscar(conn, cod, agente_key, fonte):
    ph = _ph()
    r = conn.execute(
        f'SELECT * FROM resultados_lab WHERE amostrador_cod={ph} AND agente_key={ph} '
        f'AND fonte={ph}', (cod, agente_key, fonte)).fetchone()
    return row_to_dict(r) if r else None


def gravar(dados, fonte, origem='', conn=None):
    """Grava UM resultado. `dados` usa as chaves do laudo lido (dadosExtraidos).

    DELETE+INSERT em vez de UPSERT porque o app roda em SQLite (testes/local) e
    Postgres (produção) — mesmo motivo do `_upsert_ra_laudo`. Devolve dict com
    `gravado`, `fonte` e, quando houver, `divergencia`.
    """
    cod = norm_cod(dados.get('filtroNumero') or dados.get('amostrador'))
    if not cod or fonte not in ('pdf', 'digitado'):
        return {'gravado': False, 'motivo': 'sem código de amostrador' if not cod
                else f'fonte inválida: {fonte}'}
    agente = (dados.get('agente') or '').strip()
    agente_key = norm_agente(agente)
    valor_txt, unidade, valor_num, nao_det = separar_valor(dados.get('concentracao'))
    if not valor_txt:
        return {'gravado': False, 'motivo': 'laudo sem concentração'}

    def _run(c):
        ph = _ph()
        amos = c.execute(f'SELECT id FROM amostradores WHERE UPPER(codigo)={ph}',
                         (cod,)).fetchone()
        aid = (row_to_dict(amos) or {}).get('id') if amos else None
        c.execute(f'DELETE FROM resultados_lab WHERE amostrador_cod={ph} AND '
                  f'agente_key={ph} AND fonte={ph}', (cod, agente_key, fonte))
        c.execute(
            f'INSERT INTO resultados_lab (amostrador_cod, amostrador_id, ra_num, agente, '
            f'agente_key, unidade, valor_txt, valor_num, nao_detectado, fracao, lt_nr15, '
            f'lt_twa, lt_stel, trabalhador, data_analise, fonte, origem) '
            f'VALUES ({",".join([ph]*17)})',
            (cod, aid, dados.get('ra_num') or '', agente, agente_key,
             unidade or dados.get('unidade') or '', valor_txt, valor_num,
             1 if nao_det else 0, dados.get('fracao') or '', dados.get('ltNR15') or '',
             dados.get('ltTWA') or '', dados.get('ltSTEL') or '',
             dados.get('trabalhador') or '', dados.get('dataAnalise') or '',
             fonte, origem))
        atual = _buscar(c, cod, agente_key, fonte)
        outra = _buscar(c, cod, agente_key,
                        'digitado' if fonte == 'pdf' else 'pdf')
        return aid, atual, outra

    if conn is not None:
        aid, atual, outra = _run(conn)
    else:
        with get_db() as c:
            aid, atual, outra = _run(c)

    saida = {'gravado': True, 'fonte': fonte, 'amostrador_cod': cod,
             'amostrador_id': aid, 'agente': agente, 'valor': valor_txt}
    if outra and atual:
        diverge, motivo = divergem(atual, outra)
        if diverge:
            saida['divergencia'] = motivo
            _abrir_divergencia(cod, aid, agente, atual, outra, motivo)
    return saida


def _abrir_divergencia(cod, aid, agente, atual, outra, motivo):
    """Registra na Camada de Consistência — a tela de Consistência já lista por tipo.

    A chave de deduplicação é o PREFIXO da descrição (tubo + agente), não
    `entidade_id`: `gravar` é DELETE+INSERT, então o id da linha muda a cada
    regravação e o dedupe de `salvar_divergencias` (que compara entidade_id com
    '=') abriria uma divergência nova a cada vez — o técnico gerando o laudo duas
    vezes já enchia a tela. Existindo, ATUALIZA os números: a divergência tem de
    mostrar os valores de agora, não os da primeira vez.
    """
    pdf = atual if atual.get('fonte') == 'pdf' else outra
    dig = outra if atual.get('fonte') == 'pdf' else atual
    prefixo = f'Amostrador "{cod}" · {agente or "agente não identificado"}:'
    descricao = (f'{prefixo} laudo do laboratório {pdf.get("valor_txt")} '
                 f'{pdf.get("unidade") or ""} × lançado {dig.get("valor_txt")} '
                 f'{dig.get("unidade") or ""} — {motivo}. '
                 f'Nenhum dos dois foi sobrescrito.')
    descricao = re.sub(r'\s+', ' ', descricao).strip()
    ph = _ph()
    try:
        with get_db() as conn:
            ja = conn.execute(
                f"SELECT id FROM divergencias WHERE tipo='resultado_lab_divergente' "
                f"AND status='aberta' AND descricao LIKE {ph}", (prefixo + '%',)).fetchone()
            if ja:
                conn.execute(
                    f'UPDATE divergencias SET descricao={ph}, detectado_em=CURRENT_TIMESTAMP '
                    f'WHERE id={ph}', (descricao, row_to_dict(ja)['id']))
            else:
                conn.execute(
                    f'INSERT INTO divergencias (tipo, severidade, entidade_tipo, '
                    f'entidade_id, descricao) VALUES ({ph},{ph},{ph},{ph},{ph})',
                    ('resultado_lab_divergente', 'alto',
                     'amostrador' if aid else 'resultado_lab',
                     aid or atual.get('id'), descricao))
    except Exception as e:
        log.warning('[resultado_lab] abrir divergência falhou (%s): %s', cod, e)


def gravar_muitos(lista, fonte, origem=''):
    """Grava vários resultados; devolve {gravados, ignorados, divergencias:[...]}"""
    out = {'gravados': 0, 'ignorados': 0, 'divergencias': []}
    for d in lista or []:
        try:
            r = gravar(d, fonte, origem)
        except Exception as e:
            log.warning('[resultado_lab] gravar falhou: %s', e)
            out['ignorados'] += 1
            continue
        if r.get('gravado'):
            out['gravados'] += 1
            if r.get('divergencia'):
                out['divergencias'].append(
                    f'{r["amostrador_cod"]} · {r.get("agente") or "—"}: {r["divergencia"]}')
        else:
            out['ignorados'] += 1
    return out


def listar(amostrador=None, ra_num=None, limite=200):
    """Resultados gravados, mais recentes primeiro. Filtra por tubo ou por RA."""
    ph = _ph()
    where, params = [], []
    if amostrador:
        where.append(f'amostrador_cod={ph}')
        params.append(norm_cod(amostrador))
    if ra_num:
        where.append(f'ra_num={ph}')
        params.append(str(ra_num))
    sql = 'SELECT * FROM resultados_lab'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += f' ORDER BY atualizado_em DESC, id DESC LIMIT {int(limite)}'
    with get_db() as conn:
        return [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def por_amostrador(cod):
    """O que existe para um tubo, separado por fonte + se as duas divergem."""
    linhas = listar(amostrador=cod, limite=50)
    pdf = [l for l in linhas if l.get('fonte') == 'pdf']
    dig = [l for l in linhas if l.get('fonte') == 'digitado']
    saida = {'amostrador': norm_cod(cod), 'pdf': pdf, 'digitado': dig, 'divergentes': []}
    for p in pdf:
        for d in dig:
            if p.get('agente_key') == d.get('agente_key'):
                diverge, motivo = divergem(p, d)
                if diverge:
                    saida['divergentes'].append({'agente': p.get('agente'),
                                                 'motivo': motivo})
    return saida
