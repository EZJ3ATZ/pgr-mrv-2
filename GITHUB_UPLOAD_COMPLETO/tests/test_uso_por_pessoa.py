# -*- coding: utf-8 -*-
"""Prova o painel "Quem esta usando" e o casamento de nome com o cadastro.

O `eventos.usuario` sempre foi texto livre e divergia do cadastro
('matheus costa' x 'Matheus Vinicius Costa', 'Kelly Firmino' x 'Kelly Elissama
Firmino'), entao nunca serviu de chave. O backfill casa em 3 regras e SO aceita
resultado unico — na duvida deixa NULL. Este teste garante isso, incluindo o
caso ambiguo, que e o perigoso.

    py tests\\test_uso_por_pessoa.py
"""
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

os.environ.pop('DATABASE_URL', None)
os.environ['CONTROLE_DATA_DIR'] = tempfile.mkdtemp(prefix='uso_test_')

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import controle.db as db                      # noqa: E402
from controle import perf                     # noqa: E402

ok, falhas = 0, []


def checa(nome, cond, det=''):
    global ok
    if cond:
        ok += 1
        print(f'  OK    {nome}')
    else:
        falhas.append(f'{nome} :: {det}')
        print(f'  FALHA {nome} :: {det}')


db.init_db()
perf.garantir_tabela()

# ── cadastro de teste ──
PESSOAS = [
    ('Matheus Vinícius Costa', 'eng19@x.com', 'admin'),
    ('Wesley Vieira Rodrigues', 'eng7@x.com', 'tecnico'),
    ('Kelly Elissama Firmino', 'eng29@x.com', 'tecnico'),
    ('Luiz Fernando Tavares', 'coord@x.com', 'tecnico'),
    ('Ana Paula Souza', 'ana@x.com', 'tecnico'),      # par ambiguo com a de baixo
    ('Ana Paula Silva', 'ana2@x.com', 'tecnico'),
]
ids = {}
with db.get_db() as conn:
    conn.execute('DELETE FROM eventos')
    # o init_db semeia o admin 'Matheus Costa'; sem limpar, 'matheus costa'
    # casaria EXATO com ele (o que e certo) e nao testaria a regra 1o+ultimo.
    # O teste controla o cadastro inteiro.
    conn.execute('DELETE FROM usuarios')
    for nome, email, role in PESSOAS:
        conn.execute("INSERT INTO usuarios (nome,email,senha_hash,role,ativo) "
                     "VALUES (?,?,'x',?,1)", (nome, email, role))
    for r in conn.execute('SELECT id, nome FROM usuarios').fetchall():
        ids[dict(r)['nome']] = dict(r)['id']

print('== 1) casamento de nome com o cadastro ==')
CASOS = [
    ('Wesley Vieira Rodrigues', 'Wesley Vieira Rodrigues'),   # igual
    ('Luiz Fernando',           'Luiz Fernando Tavares'),      # prefixo
    ('Kelly Firmino',           'Kelly Elissama Firmino'),     # 1o + ultimo
    ('matheus costa',           'Matheus Vinícius Costa'),     # 1o + ultimo, minusculo
]
with db.get_db() as conn:
    for bruto, _ in CASOS:
        conn.execute("INSERT INTO eventos (tipo,descricao,usuario) VALUES ('login','t',?)",
                     (bruto,))
    # ambiguo de proposito: 'Ana Souza'? nao — 'Ana Paula' casa nas DUAS por prefixo
    conn.execute("INSERT INTO eventos (tipo,descricao,usuario) VALUES ('login','t','Ana Paula')")
    # nome que nao existe no cadastro
    conn.execute("INSERT INTO eventos (tipo,descricao,usuario) VALUES ('login','t','Zoroastro')")

with db.get_db() as conn:
    db._casar_eventos_com_usuarios(conn)

with db.get_db() as conn:
    for bruto, esperado in CASOS:
        r = conn.execute('SELECT usuario_id FROM eventos WHERE usuario=?', (bruto,)).fetchone()
        got = dict(r)['usuario_id'] if r else None
        checa(f"'{bruto}' -> {esperado}", got == ids[esperado], f'id={got}, esperado={ids[esperado]}')

    r = conn.execute("SELECT usuario_id FROM eventos WHERE usuario='Ana Paula'").fetchone()
    checa('nome AMBIGUO fica NULL (nao adivinha)', dict(r)['usuario_id'] is None,
          f"{dict(r)['usuario_id']}")
    r = conn.execute("SELECT usuario_id FROM eventos WHERE usuario='Zoroastro'").fetchone()
    checa('nome fora do cadastro fica NULL', dict(r)['usuario_id'] is None,
          f"{dict(r)['usuario_id']}")

print('\n== 2) backfill e idempotente ==')
with db.get_db() as conn:
    antes = conn.execute('SELECT COUNT(*) AS c FROM eventos '
                         'WHERE usuario_id IS NOT NULL').fetchone()['c']
    db._casar_eventos_com_usuarios(conn)
    depois = conn.execute('SELECT COUNT(*) AS c FROM eventos '
                          'WHERE usuario_id IS NOT NULL').fetchone()['c']
checa('rodar 2x nao muda nada', antes == depois, f'{antes} -> {depois}')

print('\n== 3) o painel agrega por pessoa ==')
wes = ids['Wesley Vieira Rodrigues']
with db.get_db() as conn:
    for i, (rota, ms, st) in enumerate([('a', 100, 200), ('b', 300, 200),
                                        ('c', 900, 500), ('a', 50, 200)]):
        conn.execute('INSERT INTO perf_log (rota,metodo,path,status,duracao_ms,'
                     'ms_banco,consultas,bytes_resp,usuario,usuario_id,criado_em) '
                     "VALUES (?,'GET','/x',?,?,10,2,100,'eng7@x.com',?,"
                     "datetime('now'))", (rota, st, ms, wes))
    # linha antiga, SEM usuario_id — tem de casar pelo e-mail
    conn.execute('INSERT INTO perf_log (rota,metodo,path,status,duracao_ms,'
                 'ms_banco,consultas,bytes_resp,usuario,criado_em) '
                 "VALUES ('d','GET','/x',200,20,5,1,50,'eng7@x.com',datetime('now'))")
    # acao (nao-login) para contar em "acoes"
    conn.execute("INSERT INTO eventos (tipo,descricao,usuario,usuario_id) "
                 "VALUES ('amostrador_atualizado','t','Wesley Vieira Rodrigues',?)", (wes,))

lista = perf.uso_por_pessoa(30)
por_nome = {p['nome']: p for p in lista}
w = por_nome.get('Wesley Vieira Rodrigues') or {}
checa('conta as 5 requisicoes (4 com id + 1 antiga por e-mail)',
      w.get('requisicoes') == 5, f"{w.get('requisicoes')}")
checa('conta 4 telas distintas', w.get('telas') == 4, f"{w.get('telas')}")
checa('conta 1 erro 500', w.get('erros') == 1, f"{w.get('erros')}")
checa('conta 1 login e 1 acao', w.get('logins') == 1 and w.get('acoes') == 1,
      f"logins={w.get('logins')} acoes={w.get('acoes')}")
checa('traz o ultimo login', bool(w.get('ultimo_login')), f"{w.get('ultimo_login')}")
checa('media de tempo calculada no banco', (w.get('media_ms') or 0) > 0, f"{w.get('media_ms')}")
checa('pior tempo e 900ms', w.get('pior_ms') == 900, f"{w.get('pior_ms')}")

print('\n== 4) "ultima atividade" e a VERDADE, nao a data de login ==')
# O caso do Helbert em prod: login antigo (14/07), acao recente (10/08), zero
# requisicao medida. A 1a versao mostrava 14/07 sob o rotulo de atividade —
# errava 27 dias. Tem de mostrar a ACAO.
alvo = ids['Ana Paula Souza']      # sem historico nenhum ate aqui
with db.get_db() as conn:
    conn.execute("INSERT INTO eventos (tipo,descricao,usuario,usuario_id,criado_em) "
                 "VALUES ('login','t','Ana Paula Souza',?,'2026-07-14 17:40:18+00')",
                 (alvo,))
    conn.execute("INSERT INTO eventos (tipo,descricao,usuario,usuario_id,criado_em) "
                 "VALUES ('pgr_gerado','t','Ana Paula Souza',?,'2026-08-10 19:10:10+00')",
                 (alvo,))
k = {p['nome']: p for p in perf.uso_por_pessoa(3650)}.get('Ana Paula Souza') or {}
checa('usa a ACAO recente, nao o login antigo',
      str(k.get('ultima_atividade') or '').startswith('2026-08-10'),
      f"{k.get('ultima_atividade')} (login={k.get('ultimo_login')})")
checa('diz de onde veio a marca', k.get('atividade_fonte') == 'acao',
      f"{k.get('atividade_fonte')}")
checa('nao inventa requisicao para quem nao tem', (k.get('requisicoes') or 0) == 0,
      f"{k.get('requisicoes')}")

# quem TEM requisicao medida mais nova que a acao: a fonte passa a ser a tela
w2 = {p['nome']: p for p in perf.uso_por_pessoa(3650)}.get('Wesley Vieira Rodrigues') or {}
checa('quem abriu tela agora tem fonte=requisicao',
      w2.get('atividade_fonte') == 'requisicao', f"{w2.get('atividade_fonte')}")

print('\n== 5) quem nunca usou aparece, no fim ==')
nomes = [p['nome'] for p in lista]
ana = por_nome.get('Ana Paula Silva') or {}
checa('todo o cadastro aparece', len(lista) >= len(PESSOAS), f'{len(lista)}')
checa('quem nunca usou tem 0 requisicoes', (ana.get('requisicoes') or 0) == 0,
      f"{ana.get('requisicoes')}")
checa('quem usou vem antes de quem nunca usou',
      nomes.index('Wesley Vieira Rodrigues') < nomes.index('Ana Paula Silva'),
      f'{nomes}')

print(f'\n==== {ok} OK, {len(falhas)} falha(s) ====')
for f in falhas:
    print('  !!', f)
sys.exit(1 if falhas else 0)
