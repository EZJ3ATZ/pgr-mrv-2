# -*- coding: utf-8 -*-
"""Extrator do Planner: parsing v1 × v4, escopo do filtro e captura de rede.

O extrator roda na máquina (Playwright dirigindo o Planner web), mas as funções de
parsing e filtro são puras — e é nelas que os defeitos moravam. O `playwright` é
importado dentro de `main()` justamente para estes testes rodarem no CI, que não
tem o browser.

O que estes testes travam:
1. `is_real_task` reconhece tarefa v4 que só tem `version` com "Task" — antes os
   dois ramos retornavam False e a tarefa sumia sem entrar em contador nenhum;
2. o filtro NÃO aceita tarefa sem dado de categoria por padrão. O plano "Entregas
   Técnicas" tem ~8000 tarefas e só ~143 com a label MEDIÇÕES: aceitar tudo virava
   export do plano inteiro, e o importador criava demanda de medição para
   treinamento, PPR/PCA e ergonomia. Só com `--sem-filtro-categoria` passa;
3. `should_capture` não baixa payload de login nem telemetria;
4. `build_assignees` é determinístico (vinha de um `set`);
5. `get_status` devolve rótulo em português, não o enum cru da API.
"""
import importlib.util
import os

import pytest

_EXTRATOR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'tools', 'extrator_planner', 'extrator.py')


@pytest.fixture(scope='module')
def ex():
    """Carrega o extrator pelo caminho (a pasta tools/ não é pacote)."""
    spec = importlib.util.spec_from_file_location('extrator_planner', _EXTRATOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _task_v4(ex, **extra):
    """Tarefa v4 crua: sem prazo, sem responsável e sem etiqueta."""
    t = {'id': 't-v4', 'displayName': 'ACME LTDA - OS 12345',
         'planId': ex.PLAN_ID, 'version': 'PlannerTaskVersion.1'}
    t.update(extra)
    return t


# ── 1. tarefa v4 não pode sumir ───────────────────────────────────────
def test_tarefa_v4_so_com_version_e_reconhecida(ex):
    assert ex.is_real_task(_task_v4(ex)) is True


def test_bucket_v4_nao_e_confundido_com_tarefa(ex):
    bucket = {'id': 'b1', 'displayName': 'A fazer', 'planId': ex.PLAN_ID,
              'version': 'PlannerBucketVersion.1'}
    assert ex.is_real_task(bucket) is False
    assert ex.is_bucket(bucket) is True


def test_tarefa_v4_chega_em_extract_items(ex):
    tasks, buckets = ex.extract_items_from_json({'value': [_task_v4(ex)]})
    assert len(tasks) == 1 and not buckets


def test_tarefa_v1_continua_reconhecida(ex):
    v1 = {'id': 't-v1', 'title': 'X', 'planId': ex.PLAN_ID, 'percentComplete': 50}
    assert ex.is_real_task(v1) is True


# ── 2. escopo do filtro: label MEDIÇÕES ───────────────────────────────
def _com_responsavel(ex, t):
    t['userAssignments'] = [{'user': {'id': ex.HELBERT_ID}}]
    return t


def test_sem_dado_de_categoria_a_tarefa_e_descartada(ex):
    t = _com_responsavel(ex, _task_v4(ex))
    filtradas, _ = ex.filter_tasks({'t-v4': t}, {}, aceitar_sem_categoria=False)
    assert filtradas == []


def test_flag_explicita_libera_o_plano_inteiro(ex):
    t = _com_responsavel(ex, _task_v4(ex))
    filtradas, _ = ex.filter_tasks({'t-v4': t}, {}, aceitar_sem_categoria=True)
    assert len(filtradas) == 1


def test_tarefa_com_a_label_medicoes_passa(ex):
    t = _com_responsavel(ex, _task_v4(ex))
    t['appliedCategories'] = {ex.MEDICOES_CAT: True}
    filtradas, _ = ex.filter_tasks({'t-v4': t}, {}, aceitar_sem_categoria=False)
    assert len(filtradas) == 1


def test_tarefa_com_outra_label_e_descartada(ex):
    t = _com_responsavel(ex, _task_v4(ex))
    t['appliedCategories'] = {'00000003000000000000000000000000': True}   # outra label
    filtradas, _ = ex.filter_tasks({'t-v4': t}, {}, aceitar_sem_categoria=False)
    assert filtradas == []


# ── 3. captura de rede ────────────────────────────────────────────────
@pytest.mark.parametrize('url', [
    'https://login.microsoftonline.com/common/oauth2/v2.0/token',
    'https://browser.pipe.aria.microsoft.com/Collector/3.0/',
    'https://substrate.office.com/api/v2/presence',
])
def test_nao_captura_login_nem_telemetria(ex, url):
    assert ex.should_capture(url) is False


@pytest.mark.parametrize('url', [
    'https://planner.cloud.microsoft/taskapi/v4/plans/X/tasks',
    'https://graph.microsoft.com/v1.0/planner/plans/X/tasks',
])
def test_captura_endpoint_de_tarefa(ex, url):
    assert ex.should_capture(url) is True


# ── 4. determinismo ───────────────────────────────────────────────────
def test_ordem_dos_responsaveis_nao_muda(ex):
    a = {'userAssignments': [{'user': {'id': ex.HELBERT_ID}},
                             {'user': {'id': ex.WESLEY_ID}}]}
    b = {'userAssignments': [{'user': {'id': ex.WESLEY_ID}},
                             {'user': {'id': ex.HELBERT_ID}}]}
    assert ex.build_assignees(a) == ex.build_assignees(b) == 'Helbert, Wesley'


def test_uuid_v4_sem_dashes_e_normalizado(ex):
    sem_dash = ex.HELBERT_ID.replace('-', '')
    t = {'userAssignments': [{'id': sem_dash}]}
    assert ex.build_assignees(t) == 'Helbert'


# ── 5. status legível ─────────────────────────────────────────────────
@pytest.mark.parametrize('bruto,esperado', [
    ('completed', 'Concluida'),
    ('inProgress', 'Em andamento'),
    ('deferred', 'Adiada'),
    ('waitingOnOthers', 'Aguardando terceiro'),
])
def test_status_v4_sai_em_portugues(ex, bruto, esperado):
    assert ex.get_status({'status': bruto}) == esperado


def test_status_v1_por_percentual(ex):
    assert ex.get_status({'percentComplete': 100}) == 'Concluida'
    assert ex.get_status({'percentComplete': 50}) == 'Em andamento'
    assert ex.get_status({'percentComplete': 0}) == 'Nao iniciada'
