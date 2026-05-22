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


def graph_patch(path: str, payload: dict) -> dict:
    """PATCH na Graph API."""
    url = path if path.startswith('https://') else GRAPH_BASE + path
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=_headers(), method='PATCH')
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            content = r.read()
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode('utf-8', 'replace')
        raise RuntimeError(f'Graph PATCH {path} → {e.code}: {body_txt[:300]}')


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
    """Lista todos os grupos Microsoft 365 que têm Teams (e portanto Planner)."""
    return graph_paginate(
        "/groups?$filter=resourceProvisioningOptions/Any(x:x eq 'Team')"
        "&$select=id,displayName,description,mail"
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
