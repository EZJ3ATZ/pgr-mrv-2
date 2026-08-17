# -*- coding: utf-8 -*-
"""
Microsoft Graph API — cliente autenticado (App-only / Client Credentials).

Variáveis de ambiente obrigatórias (.env / Railway):
    AZURE_CLIENT_ID
    AZURE_CLIENT_SECRET
    AZURE_TENANT_ID

Uso:
    from controle.graph import graph_get, graph_post, graph_ok

    dados = graph_get('/groups?$filter=...')
    graph_ok()   → True se credenciais configuradas e token válido
"""

import os
import time
import logging
import urllib.request
import urllib.parse
import json

log = logging.getLogger(__name__)

# ── Credenciais (lidas do ambiente) ───────────────────────────────────
CLIENT_ID     = os.environ.get('AZURE_CLIENT_ID',     '')
CLIENT_SECRET = os.environ.get('AZURE_CLIENT_SECRET', '')
TENANT_ID     = os.environ.get('AZURE_TENANT_ID',     '')

GRAPH_BASE    = 'https://graph.microsoft.com/v1.0'
TOKEN_URL     = f'https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token'
SCOPE         = 'https://graph.microsoft.com/.default'

# ── Cache de token em memória ─────────────────────────────────────────
_token_cache = {
    'access_token': '',
    'expires_at':   0,   # unix timestamp
}


def _configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET and TENANT_ID)


def _get_token() -> str:
    """Retorna token válido (renova automaticamente se expirado)."""
    if not _configured():
        raise RuntimeError(
            'AZURE_CLIENT_ID / AZURE_CLIENT_SECRET / AZURE_TENANT_ID não configurados. '
            'Adicione as variáveis de ambiente no Railway.'
        )

    now = time.time()
    # Renova 60s antes de expirar
    if _token_cache['access_token'] and _token_cache['expires_at'] > now + 60:
        return _token_cache['access_token']

    log.info('[graph] renovando token Microsoft Graph...')

    body = urllib.parse.urlencode({
        'grant_type':    'client_credentials',
        'client_id':     CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'scope':         SCOPE,
    }).encode()

    req = urllib.request.Request(TOKEN_URL, data=body, method='POST',
                                  headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
    except Exception as e:
        raise RuntimeError(f'Erro ao obter token Microsoft: {e}')

    if 'error' in resp:
        raise RuntimeError(f'Erro Azure AD: {resp.get("error_description", resp["error"])}')

    _token_cache['access_token'] = resp['access_token']
    _token_cache['expires_at']   = now + int(resp.get('expires_in', 3600))
    log.info('[graph] token renovado, expira em %ds', resp.get('expires_in', 3600))
    return _token_cache['access_token']


def _headers() -> dict:
    return {
        'Authorization': f'Bearer {_get_token()}',
        'Content-Type':  'application/json',
        'Accept':        'application/json',
    }


def graph_ok() -> bool:
    """Verifica se credenciais estão configuradas e token pode ser obtido."""
    if not _configured():
        return False
    try:
        _get_token()
        return True
    except Exception:
        return False


def graph_get(path: str, params: dict = None) -> dict:
    """GET na Graph API. path pode ser relativo (/groups) ou URL completa."""
    url = path if path.startswith('https://') else GRAPH_BASE + path
    if params:
        url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)

    # Codifica espaços e aspas simples na query string (Python 3.14 é mais estrito)
    if '?' in url:
        base_url, qs = url.split('?', 1)
        url = base_url + '?' + qs.replace(' ', '%20').replace("'", '%27')

    req = urllib.request.Request(url, headers=_headers(), method='GET')
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'replace')
        raise RuntimeError(f'Graph GET {path} → {e.code}: {body[:300]}')


def graph_post(path: str, payload: dict) -> dict:
    """POST na Graph API."""
    url = path if path.startswith('https://') else GRAPH_BASE + path
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=_headers(), method='POST')
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode('utf-8', 'replace')
        raise RuntimeError(f'Graph POST {path} → {e.code}: {body_txt[:300]}')


def graph_paginate(path: str, max_pages: int = 20) -> list:
    """Percorre todas as páginas de uma listagem Graph (paginação @odata.nextLink)."""
    results = []
    url = path if path.startswith('https://') else GRAPH_BASE + path
    pages = 0
    while url and pages < max_pages:
        data = graph_get(url)
        results.extend(data.get('value', []))
        url = data.get('@odata.nextLink', '')
        pages += 1
    return results


# ── Helpers de alto nível ─────────────────────────────────────────────

def get_teams_groups() -> list:
    """Lista todos os grupos Microsoft 365 com Planner (inclui grupos sem Teams)."""
    # Busca TODOS os grupos M365 — o filtro Teams excluia grupos Planner sem Teams habilitado
    return graph_paginate(
        '/groups?$select=id,displayName,description,mail'
        '&$top=100'
    )


def get_plans_for_group(group_id: str) -> list:
    """Lista planos do Planner de um grupo."""
    data = graph_get(f'/groups/{group_id}/planner/plans')
    return data.get('value', [])


def get_plan_buckets(plan_id: str) -> list:
    """Lista buckets de um plano."""
    data = graph_get(f'/planner/plans/{plan_id}/buckets')
    return data.get('value', [])


def get_plan_tasks(plan_id: str) -> list:
    """Lista todas as tarefas de um plano com paginação."""
    return graph_paginate(f'/planner/plans/{plan_id}/tasks')


def get_plan_details(plan_id: str) -> dict:
    """Busca detalhes do plano (categoryDescriptions — mapeamento de labels)."""
    try:
        return graph_get(f'/planner/plans/{plan_id}/details')
    except Exception as e:
        log.warning('[graph] get_plan_details %s: %s', plan_id, e)
        return {}


def get_plan_category_map(plan_id: str) -> dict:
    """
    Retorna mapa {categoryN: 'nome'} dos labels do plano.
    Ex: {'category1': 'Medições', 'category2': 'Urgente'}
    """
    details = get_plan_details(plan_id)
    return details.get('categoryDescriptions', {})


def get_task_details(task_id: str) -> dict:
    """Busca detalhes de uma tarefa (descrição, checklist, referencias)."""
    try:
        return graph_get(f'/planner/tasks/{task_id}/details')
    except Exception as e:
        log.warning('[graph] get_task_details %s: %s', task_id, e)
        return {}


def get_user(user_id: str) -> dict:
    """Busca dados de um usuário pelo ID."""
    try:
        return graph_get(f'/users/{user_id}?$select=id,displayName,mail,userPrincipalName')
    except Exception as e:
        log.warning('[graph] get_user %s: %s', user_id, e)
        return {}


def list_org_users() -> list:
    """Lista todos os usuários da organização."""
    return graph_paginate('/users?$select=id,displayName,mail,userPrincipalName,jobTitle,department')


# ── Planner: CRIAÇÃO de tarefas (handoff CRM → OS de medição) ──────────
# O plano "Entregas Técnicas" do grupo "Ocupacional" é o mesmo que o sync
# de 15 min lê para gerar demandas. Criar a task certa aqui = a demanda
# cai sozinha no pipeline (não precisa escrever no banco do portal).

PLAN_ENTREGAS_TECNICAS = 'JOHzljvSKkmfSsQ7SekCnWUAA8cz'
GRUPO_OCUPACIONAL      = '4c80214b-6801-414a-9fc7-27feff0b3de6'

# Grupo M365 "Ergonomia" — tem plano PRÓPRIO ("Ergonomia - Grupo Ocupacional",
# tasks com nº de OS no título). O handoff de ergonomia cria a task LÁ.
GRUPO_ERGONOMIA = '0fe08d77-fc05-468b-8741-d6de211878f1'

# Bucket de ENTRADA de novas demandas de engenharia no plano Entregas Técnicas.
# (A task de medição nasce sem bucket de propósito; a de engenharia nasce aqui —
# é bucket de entrada, não de conclusão, então é seguro. Fallback do id abaixo;
# get_bucket_id_by_name redescobre por nome se a equipe recriar o bucket.)
BUCKET_ENG_NOVAS_DEMANDAS = 'xtiwN7av_kqMhLZO2ACiMmUAJZ38'


def criar_planner_task(plan_id: str, title: str,
                       applied_categories: dict = None,
                       bucket_id: str = None,
                       assignments: dict = None) -> dict:
    """
    Cria uma tarefa no Planner e retorna o objeto criado (com id/@odata.etag).

    applied_categories: {'category10': True} aplica o label no ATO da criação
        (labels podem ir no POST /planner/tasks, sem PATCH separado).
    bucket_id: opcional; se omitido a task nasce sem bucket (não corre risco de
        cair num bucket de "concluído" e ser marcada como feita por engano).
    """
    payload = {'planId': plan_id, 'title': (title or 'Sem título')[:255]}
    if bucket_id:
        payload['bucketId'] = bucket_id
    if applied_categories:
        payload['appliedCategories'] = applied_categories
    if assignments:
        payload['assignments'] = assignments
    return graph_post('/planner/tasks', payload)


def set_task_description(task_id: str, description: str) -> bool:
    """
    Grava a descrição (campo "Notas") de uma tarefa do Planner.
    O PATCH de /details exige o header If-Match com o @odata.etag atual —
    por isso faz um GET antes para pegar o etag.
    """
    details = graph_get(f'/planner/tasks/{task_id}/details')
    etag = details.get('@odata.etag')
    if not etag:
        return False
    url = f'{GRAPH_BASE}/planner/tasks/{task_id}/details'
    body = json.dumps({'description': description, 'previewType': 'description'}).encode()
    headers = _headers()
    headers['If-Match'] = etag
    req = urllib.request.Request(url, data=body, headers=headers, method='PATCH')
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
            return True
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode('utf-8', 'replace')
        raise RuntimeError(f'Graph PATCH details {task_id} → {e.code}: {body_txt[:300]}')


def get_medicoes_category_id(plan_id: str) -> str:
    """
    Descobre dinamicamente o categoryN cujo nome contém "MEDIÇÕES" no plano.
    Assim o label não fica hardcoded — se a equipe mover o label, continua
    funcionando. Fallback 'category10' (convenção atual do plano).
    """
    try:
        cat_map = get_plan_category_map(plan_id) or {}
        import unicodedata

        def _n(s):
            return unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
        for cid, nome in cat_map.items():
            if 'medic' in _n(nome):
                return cid
    except Exception as e:
        log.warning('[graph] get_medicoes_category_id %s: %s', plan_id, e)
    return 'category10'


def _norm_label(s):
    import unicodedata
    return unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower().strip()


def get_category_ids_by_names(plan_id, nomes):
    """
    Resolve uma lista de nomes de label (ex.: ['PGR/PCMSO','TREINAMENTO','PCMSO'])
    para {categoryN: True}, casando por nome normalizado (sem acento, minúsculo).
    Prioriza match EXATO — assim 'PCMSO' cai em 'PCMSO' (category7), não em
    'PGR/PCMSO' (category6). Nomes não achados são ignorados. Devolve {} se nada casa.
    """
    out = {}
    try:
        cat_map = get_plan_category_map(plan_id) or {}
        norm = {cid: _norm_label(v) for cid, v in cat_map.items() if v}
        for nome in nomes or []:
            alvo = _norm_label(nome)
            if not alvo:
                continue
            hit = next((cid for cid, cn in norm.items() if cn == alvo), None)          # exato
            if not hit:
                hit = next((cid for cid, cn in norm.items() if alvo in cn), None)      # label contém alvo ('pgr' → 'pgr/pcmso')
            if not hit:
                hit = next((cid for cid, cn in norm.items() if cn in alvo), None)      # alvo contém label ('treinamento nr-35' → 'treinamento')
            if hit:
                out[hit] = True
    except Exception as e:
        log.warning('[graph] get_category_ids_by_names %s: %s', plan_id, e)
    return out


_plan_title_cache = {}


def get_plan_id_by_title(group_id, titulo_contains):
    """Descobre o plano de um grupo cujo título contém `titulo_contains`
    (normalizado). Cacheado por processo. None se não achar."""
    key = (group_id, _norm_label(titulo_contains))
    if key in _plan_title_cache:
        return _plan_title_cache[key]
    try:
        for p in get_plans_for_group(group_id):
            if key[1] in _norm_label(p.get('title', '')):
                _plan_title_cache[key] = p['id']
                return p['id']
    except Exception as e:
        log.warning('[graph] get_plan_id_by_title %s: %s', group_id, e)
    return None


def get_bucket_id_by_name(plan_id, nome_contains, fallback=None):
    """Descobre o id de um bucket cujo nome contém `nome_contains` (normalizado).
    Fallback para um id fixo se não achar (bucket renomeado/recriado)."""
    try:
        alvo = _norm_label(nome_contains)
        for b in get_plan_buckets(plan_id):
            if alvo in _norm_label(b.get('name', '')):
                return b['id']
    except Exception as e:
        log.warning('[graph] get_bucket_id_by_name %s: %s', plan_id, e)
    return fallback


# ── SharePoint / OneDrive ────────────────────────────────────────────

def upload_to_sharepoint(site_id: str, drive_id: str, folder_path: str,
                          filename: str, content: bytes) -> dict:
    """Faz upload de arquivo para SharePoint/OneDrive."""
    url = f'{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/root:/{folder_path}/{filename}:/content'
    req = urllib.request.Request(url, data=content, headers={
        'Authorization': f'Bearer {_get_token()}',
        'Content-Type': 'application/octet-stream',
    }, method='PUT')
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def list_sharepoint_files(site_id: str, drive_id: str, folder_path: str = '') -> list:
    """Lista arquivos de uma pasta no SharePoint."""
    path = f'/sites/{site_id}/drives/{drive_id}/root'
    if folder_path:
        path += f':/{folder_path}:'
    path += '/children'
    return graph_paginate(path)


# ── Outlook ──────────────────────────────────────────────────────────

def list_emails(user_id: str, top: int = 50, folder: str = 'inbox') -> list:
    """Lista e-mails de um usuário (requer Mail.Read delegated ou Mail.Read.All app)."""
    return graph_paginate(
        f'/users/{user_id}/mailFolders/{folder}/messages'
        f'?$top={top}&$select=id,subject,from,receivedDateTime,bodyPreview,hasAttachments'
        f'&$orderby=receivedDateTime desc'
    )


# ── Groups / Planner Conversations ──────────────────────────────────

def get_group_thread_posts(group_id: str, thread_id: str) -> list:
    """
    Busca posts (comentários) de uma thread de conversação de um grupo.
    Usado para extrair número de OS e para SINCRONIZAR os comentários da tarefa.
    Requer permissão: Group.Read.All ou GroupMember.Read.All

    Traz `id` e `from` além do corpo: sem o autor não dá para saber quem
    registrou a tratativa, e sem o id não há como deduplicar no re-sync.
    """
    try:
        data = graph_get(
            f'/groups/{group_id}/threads/{thread_id}/posts'
            f'?$select=id,body,createdDateTime,from'
        )
        return data.get('value', [])
    except Exception as e:
        log.warning('[graph] get_group_thread_posts %s/%s: %s', group_id, thread_id, e)
        return []
