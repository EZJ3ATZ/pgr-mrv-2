# -*- coding: utf-8 -*-
"""Prova as travas anti-ruido do alerta SEM enviar e-mail nenhum.

O modo de falha conhecido da casa e ruido: o aviso de atribuicao do Portal CS
gerou 444 alertas repetidos. Entao o que este teste garante e justamente o que
NAO deve acontecer: segundo e-mail para o mesmo problema, alerta sem linha de
base, e envio acima do teto.

    py tests\\test_alerta.py
"""
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

os.environ.pop('DATABASE_URL', None)          # SQLite temporario
os.environ['CONTROLE_DATA_DIR'] = tempfile.mkdtemp(prefix='alerta_test_')
os.environ['ALERTA_EMAIL'] = '0'              # nunca envia de verdade
os.environ['ALERTA_TETO_DIA'] = '2'

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import controle.db as db                       # noqa: E402
from controle import alerta, perf              # noqa: E402

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
alerta.garantir_tabela()

# ── espiao no envio: conta sem mandar ──
enviados = []
alerta._enviar = lambda assunto, corpo: (enviados.append((assunto, corpo)), (True, None))[1]


def linhas_5xx(n):
    with db.get_db() as conn:
        for i in range(n):
            conn.execute("INSERT INTO perf_log (rota,metodo,path,status,duracao_ms,"
                         "ms_banco,consultas,bytes_resp,usuario,criado_em) "
                         "VALUES ('x','GET','/x',500,10,1,1,10,'t@x',datetime('now'))")


print('== 1) sem nada acontecendo, nao alerta ==')
r = alerta.verificar()
checa('nenhum alerta de quebrou', not r['quebrou'], f"{r['quebrou']}")
checa('nenhum e-mail', not enviados, f'{len(enviados)}')

print('\n== 2) erro 5xx em serie: alerta UMA vez ==')
linhas_5xx(6)
r1 = alerta.verificar()
checa('detectou os 6 erros', any('erro' in t.lower() for t in r1['quebrou']),
      f"{r1['quebrou']}")
checa('mandou 1 e-mail', len(enviados) == 1, f'{len(enviados)}')

print('\n== 3) MESMO problema continuando: NAO manda de novo ==')
linhas_5xx(6)
r2 = alerta.verificar()
checa('nao gerou novo alerta (mudanca de estado)', not r2['quebrou'], f"{r2['quebrou']}")
checa('continua com 1 e-mail só', len(enviados) == 1, f'{len(enviados)}')
checa('mas segue listado como aberto',
      any(a['chave'] == 'erro_5xx' for a in r2['abertos']), f"{r2['abertos']}")

print('\n== 4) problema resolvido e reaberto: volta a avisar ==')
with db.get_db() as conn:
    conn.execute('DELETE FROM perf_log WHERE status >= 500')
r3 = alerta.verificar()
checa('fechou o alerta quando o problema saiu',
      not any(a['chave'] == 'erro_5xx' for a in r3['abertos']), f"{r3['abertos']}")
linhas_5xx(6)
r4 = alerta.verificar()
checa('reabriu e avisou de novo', len(enviados) == 2, f'{len(enviados)}')

print('\n== 5) teto diario suprime e avisa que suprimiu ==')
# ja foram 2 avisos hoje; o teto e 2 → o proximo alerta novo tem de ser suprimido
with db.get_db() as conn:
    conn.execute("INSERT INTO eventos (tipo,descricao,criado_em) "
                 "VALUES ('sync_planner','t',datetime('now','-9 hours'))")
antes = len(enviados)
r5 = alerta.verificar()
checa('alerta novo foi suprimido pelo teto', r5['suprimidos'] >= 1,
      f"suprimidos={r5['suprimidos']}")
checa('mandou o aviso de supressao (1 e-mail, nao N)',
      len(enviados) == antes + 1, f'{len(enviados) - antes}')

print('\n== 6) digest sai com NOVO e AINDA ABERTO separados ==')
enviados.clear()
r6 = alerta.verificar(forcar_digest=True)
corpo = r6.get('corpo_digest') or ''
checa('digest foi montado', r6['digest'] and corpo, f'{bool(corpo)}')
checa('digest separa o que segue aberto', 'AINDA ABERTO' in corpo, corpo[:200])
checa('digest traz o resumo de uso', 'USO (7 dias)' in corpo, corpo[-200:])
checa('digest diz como desligar', 'ALERTA_EMAIL=0' in corpo, corpo[-120:])

print('\n== 7) interruptor de envio ==')
os.environ['ALERTA_EMAIL'] = '0'
checa('ALERTA_EMAIL=0 desliga o envio', not alerta._envio_ligado())
os.environ['ALERTA_EMAIL'] = '1'
checa('sem a variavel, envio ligado (default)', alerta._envio_ligado())

print('\n== 8) sem linha de base, nao inventa "piorou" ==')
r8 = alerta.verificar()
checa('nenhum alerta de piorou com 1 dia de dado', not r8['piorou'], f"{r8['piorou']}")

print(f'\n==== {ok} OK, {len(falhas)} falha(s) ====')
for f in falhas:
    print('  !!', f)
sys.exit(1 if falhas else 0)
