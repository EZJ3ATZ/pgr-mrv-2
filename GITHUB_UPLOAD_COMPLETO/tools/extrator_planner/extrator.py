# -*- coding: utf-8 -*-
"""
Extrator de Tarefas - Microsoft Planner
Plano: Entregas Tecnicas
Filtro: Helbert + Matheus + Wesley + flag MEDICOES
Saida: Demandas_Medicoes_Completo.xlsx

Estrategia: intercepta respostas de rede do Planner (mais confiavel que IDB).
Fallback: leitura direta do IndexedDB.

Dependencias:
    pip install playwright openpyxl
    playwright install chromium

Uso:
    py extrator.py
"""

import json
import re
import asyncio
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from playwright.async_api import async_playwright, Page, BrowserContext, Response

# CONFIGURACOES =========================================================
PLAN_ID       = "JOHzljvSKkmfSsQ7SekCnWUAA8cz"
TENANT_ID     = "953ea640-963d-4af5-844c-03f21ebc048f"

HELBERT_ID    = "d7c006ff-d86b-4cf6-9f67-bbaa019f036e"
MATHEUS_ID    = "ba8e6795-6926-4c1f-ae38-aef4df737070"
WESLEY_ID     = "a4256cbb-7af4-4f9e-9c22-d47ee21966b5"
MEDICOES_CAT  = "00000009000000000000000000000000"

PLANNER_URL   = f"https://planner.cloud.microsoft/webui/plan/{PLAN_ID}/view/board"
TASK_URL      = "https://planner.cloud.microsoft/webui/plan/{plan_id}/view/board/task/{task_id}?tid={tid}"

OUTPUT_FILE   = "Demandas_Medicoes_Completo.xlsx"
PROFILE_DIR   = "./playwright_profile"

ASSIGNEES     = {HELBERT_ID, MATHEUS_ID, WESLEY_ID}
ASSIGNEE_NAMES = {
    HELBERT_ID: "Helbert",
    MATHEUS_ID: "Matheus",
    WESLEY_ID:  "Wesley",
}

# Patterns de URL que contem dados de tarefas
TASK_URL_PATTERNS = [
    '/tasks',
    '/task',
    f'plans/{PLAN_ID}',
    '/planner/plans',
    '/buckets',
    'GetAllPlans',
    'GetTasksForPlan',
    'GetBucketsForPlan',
]

JS_EXTRACT_COMMENTS = """
() => {
    const container = document.querySelector('[class*="fui-Chat"]');
    if (!container) return null;

    const walker = document.createTreeWalker(
        container,
        NodeFilter.SHOW_TEXT,
        null
    );

    const texts = [];
    let node;
    while ((node = walker.nextNode())) {
        const t = node.textContent.trim();
        if (!t || t.length < 3) continue;
        if (/^\\d{1,2}\\/\\d{1,2}\\s+\\d{1,2}:\\d{2}$/.test(t)) continue;
        if (/^(Hoje|Ontem|Yesterday|Today)$/i.test(t)) continue;
        if (t.length <= 50 && /^[A-Za-zA-u\\s\\.]+$/.test(t) && !t.includes('  ')) continue;
        texts.push(t);
    }
    return texts.join('\\n---\\n');
}
"""

# IDB fallback (mantido como backup)
JS_READ_IDB = """
async () => {
    return new Promise((resolve) => {
        const dbs = indexedDB.databases ? indexedDB.databases() : Promise.resolve([]);
        dbs.then(list => {
            const plannerDb = list.find(d => d.name && (
                d.name.startsWith('PlannerV2_') ||
                d.name.startsWith('PlannerV3_') ||
                d.name.toLowerCase().includes('planner')
            ));
            if (!plannerDb) return resolve({ tasks: [], buckets: [], dbName: null, allDbs: list.map(d=>d.name) });
            const req = indexedDB.open(plannerDb.name);
            req.onsuccess = () => {
                const db = req.result;
                const allStores = Array.from(db.objectStoreNames);
                const readStore = (name) => new Promise(res => {
                    if (!allStores.includes(name)) return res([]);
                    try {
                        const tx = db.transaction(name, 'readonly');
                        const all = tx.objectStore(name).getAll();
                        all.onsuccess = () => res(all.result || []);
                        all.onerror = () => res([]);
                    } catch(e) { res([]); }
                });
                const tName = allStores.find(s => s==='task'||s==='tasks'||s.toLowerCase()==='task') || 'task';
                const bName = allStores.find(s => s==='bucket'||s==='buckets'||s.toLowerCase()==='bucket') || 'bucket';
                Promise.all([readStore(tName), readStore(bName)]).then(([tasks, buckets]) => {
                    resolve({ tasks, buckets, dbName: plannerDb.name, allStores, allDbs: list.map(d=>d.name) });
                });
            };
            req.onerror = () => resolve({ tasks: [], buckets: [], dbName: plannerDb.name, allDbs: list.map(d=>d.name), error: String(req.error) });
        }).catch(e => resolve({ tasks: [], buckets: [], dbName: null, error: String(e) }));
    });
}
"""


# FUNCOES AUXILIARES =====================================================
def parse_date(val) -> str:
    if not val:
        return ""
    if isinstance(val, dict):
        val = val.get("date") or val.get("dateTime") or ""
    if not val:
        return ""
    try:
        s = str(val).replace("Z", "").split("T")[0]
        parts = s.split("-")
        if len(parts) == 3:
            return f"{parts[2]}/{parts[1]}/{parts[0]}"
    except Exception:
        pass
    return str(val)


def extract_os(nome: str) -> str:
    m = re.match(r'^(\d{5,10})', nome.strip())
    return m.group(1) if m else ""


def extract_cnpj(texto: str) -> str:
    if not texto:
        return ""
    m = re.search(r'\d{2}[\.\s]?\d{3}[\.\s]?\d{3}[\.\s\/]?\d{4}[\.\s\-]?\d{2}', texto)
    return m.group(0).strip() if m else ""


def get_status(task: dict) -> str:
    pct = task.get("percentComplete", 0)
    if pct == 100 or task.get("completedDateTime"):
        return "Concluida"
    if pct == 50:
        return "Em andamento"
    if pct == 0:
        return "Nao iniciada"
    return f"{pct}%"


def build_checklist(task: dict):
    checklist = task.get("checklist", {})
    if not checklist:
        return "", ""
    items = list(checklist.values()) if isinstance(checklist, dict) else checklist
    linhas, done = [], 0
    for item in items:
        title = item.get("title", "")
        checked = item.get("isChecked", False)
        linhas.append(f"{'OK' if checked else '[ ]'} {title}")
        if checked:
            done += 1
    return "\n".join(linhas), f"{done}/{len(items)}"


def build_assignees(task: dict) -> str:
    assignments = task.get("assignments", {})
    if not assignments:
        return ""
    return ", ".join(ASSIGNEE_NAMES.get(uid, uid[:8] + "...") for uid in assignments.keys())


def build_labels(task: dict) -> str:
    cats = task.get("appliedCategories", {})
    if not cats:
        pv = task.get("planView", {})
        cats = pv.get("appliedCategoryIds", []) if pv else []
        if isinstance(cats, list):
            return ", ".join(cats) if cats else ""
    if isinstance(cats, dict):
        return ", ".join(k for k, v in cats.items() if v)
    return str(cats)


def merge_tasks(raw_list: list) -> dict:
    """Merge task data by id — later entries overwrite earlier (more complete data wins)."""
    merged = {}
    for t in raw_list:
        tid = t.get("id") or t.get("taskId") or t.get("@odata.id", "")
        if not tid:
            continue
        if tid in merged:
            # Merge: update with non-null values
            for k, v in t.items():
                if v is not None and v != "" and v != {} and v != []:
                    merged[tid][k] = v
        else:
            merged[tid] = dict(t)
    return merged


def filter_tasks(task_map: dict, bucket_map: dict):
    print(f"   Total de tarefas capturadas: {len(task_map)}")
    plan_ids = set(t.get("planId", "") for t in task_map.values())
    print(f"   Plan IDs encontrados: {plan_ids}")

    filtered = []
    sem_plano = sem_resp = sem_cat = 0

    for t in task_map.values():
        if t.get("planId") != PLAN_ID:
            sem_plano += 1
            continue
        assignments = t.get("assignments", {})
        if not any(uid in ASSIGNEES for uid in assignments.keys()):
            sem_resp += 1
            continue
        # Verifica categoria MEDICOES
        pv = t.get("planView", {})
        cat_ids = pv.get("appliedCategoryIds", []) if pv else []
        applied = t.get("appliedCategories", {})
        has_cat = (
            MEDICOES_CAT in cat_ids
            or (isinstance(applied, dict) and applied.get(MEDICOES_CAT, False))
        )
        if not has_cat:
            sem_cat += 1
            continue
        filtered.append(t)

    print(f"   Excluidas: {sem_plano} sem plano | {sem_resp} sem responsavel | {sem_cat} sem cat MEDICOES")
    print(f"   Filtradas: {len(filtered)} tarefas")

    if not filtered and task_map:
        # Mostrar amostra das tarefas do plano certo para debug
        plano = [t for t in task_map.values() if t.get("planId") == PLAN_ID]
        if plano:
            t0 = plano[0]
            print(f"\n   AMOSTRA (plano correto, mas sem filtro):")
            print(f"     titulo: {t0.get('title','')[:60]}")
            print(f"     assignments: {list(t0.get('assignments',{}).keys())}")
            pv = t0.get("planView", {})
            print(f"     appliedCategoryIds: {pv.get('appliedCategoryIds',[]) if pv else []}")
            print(f"     appliedCategories: {t0.get('appliedCategories',{})}")
        else:
            print(f"\n   NENHUMA tarefa com planId={PLAN_ID}")

    return filtered, bucket_map


# INTERCEPTACAO DE REDE ==================================================
def should_capture(url: str) -> bool:
    url_lower = url.lower()
    if 'planner' not in url_lower and 'tasks.office' not in url_lower and 'graph.microsoft' not in url_lower:
        return False
    return any(p.lower() in url_lower for p in TASK_URL_PATTERNS)


def extract_items_from_json(data) -> tuple:
    """Extrai tasks e buckets de um payload JSON. Retorna (tasks, buckets)."""
    tasks, buckets = [], []
    if isinstance(data, dict):
        # OData collection: {"value": [...]}
        value = data.get("value", [])
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                if "bucketId" in item or "percentComplete" in item or "planId" in item:
                    tasks.append(item)
                elif "planId" in item and "name" in item and "orderHint" in item:
                    buckets.append(item)
                elif "orderHint" in item and "name" in item:
                    buckets.append(item)
        # Direct task object
        if "bucketId" in data or "percentComplete" in data:
            tasks.append(data)
        # Direct bucket
        if "orderHint" in data and "name" in data and "bucketId" not in data and "percentComplete" not in data:
            buckets.append(data)
        # Nested structures: {"tasks": [...], "buckets": [...]}
        for key in ("tasks", "task"):
            if key in data and isinstance(data[key], list):
                tasks.extend(t for t in data[key] if isinstance(t, dict))
        for key in ("buckets", "bucket"):
            if key in data and isinstance(data[key], list):
                buckets.extend(b for b in data[key] if isinstance(b, dict))
    elif isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            if "bucketId" in item or "percentComplete" in item:
                tasks.append(item)
            elif "orderHint" in item and "name" in item:
                buckets.append(item)
    return tasks, buckets


# COMENTARIOS ============================================================
async def collect_comments(page: Page, tasks_with_chat: list) -> dict:
    comments = {}
    total = len(tasks_with_chat)
    for i, task in enumerate(tasks_with_chat, 1):
        task_id = task.get("id") or task.get("taskId", "")
        if not task_id:
            continue
        url = TASK_URL.format(plan_id=PLAN_ID, task_id=task_id, tid=TENANT_ID)
        print(f"  [{i}/{total}] Tarefa {task_id[:12]}...")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_selector('[class*="fui-Chat"]', timeout=25000)
                await asyncio.sleep(3)
            except Exception:
                print(f"    AVISO: Chat nao carregou")
                comments[task_id] = ""
                continue
            result = await page.evaluate(JS_EXTRACT_COMMENTS)
            comments[task_id] = result or ""
            count = len([c for c in (result or "").split("---") if c.strip()])
            print(f"    {count} comentario(s)")
        except Exception as e:
            print(f"    ERRO: {e}")
            comments[task_id] = ""
    return comments


# EXCEL ==================================================================
def generate_excel(tasks: list, bucket_map: dict, comments: dict, output: str):
    wb = Workbook()
    ws = wb.active
    ws.title = "Demandas Medicoes"

    headers = [
        "Nome da Tarefa", "OS", "Data de Criacao", "Data de Prazo",
        "Data de Conclusao", "Responsavel(is)", "Status", "Progresso (%)",
        "Checklist", "Checklist Progresso", "Grupo (Bucket)",
        "Etiquetas", "Descricao", "CNPJ", "Tem Comentarios", "Comentarios"
    ]

    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(bold=True, color="FFFFFF", size=11)

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    ws.row_dimensions[1].height = 30
    col_widths = [50, 12, 14, 14, 14, 25, 15, 12, 40, 16, 25, 20, 60, 20, 16, 80]
    for col, width in enumerate(col_widths, 1):
        ws.column_dimensions[ws.cell(1, col).column_letter].width = width

    alt_fill = PatternFill("solid", fgColor="D6E4F0")

    for row_idx, task in enumerate(tasks, 2):
        task_id = task.get("id") or task.get("taskId", "")
        nome = task.get("title", "")
        descricao = task.get("description", "") or task.get("notes", {}).get("value", "") or ""
        checklist_txt, checklist_prog = build_checklist(task)
        comentarios = comments.get(task_id, "")

        row_data = [
            nome,
            extract_os(nome),
            parse_date(task.get("createdDateTime")),
            parse_date(task.get("dueDateTime")),
            parse_date(task.get("completedDateTime")),
            build_assignees(task),
            get_status(task),
            task.get("percentComplete", 0),
            checklist_txt,
            checklist_prog,
            bucket_map.get(task.get("bucketId", ""), ""),
            build_labels(task),
            descricao,
            extract_cnpj(descricao),
            "Sim" if comentarios.strip() else "Nao",
            comentarios,
        ]

        fill = alt_fill if row_idx % 2 == 0 else None
        for col_idx, value in enumerate(row_data, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if fill:
                cell.fill = fill

    ws.freeze_panes = "A2"
    wb.save(output)
    print(f"\nOK: Excel salvo: {output} ({len(tasks)} linhas)")


# MAIN ==================================================================
async def main():
    print("=" * 60)
    print("  EXTRATOR PLANNER - Entregas Tecnicas / MEDICOES")
    print("  Estrategia: interceptacao de rede")
    print("=" * 60)

    # Buffers para dados interceptados
    tasks_raw:   list = []
    buckets_raw: list = []
    captured_urls: list = []

    profile_path = Path(PROFILE_DIR).resolve()
    profile_path.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        context: BrowserContext = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            headless=False,
            args=["--start-maximized"],
            no_viewport=True,
        )

        page = context.pages[0] if context.pages else await context.new_page()

        # Registra interceptador ANTES de navegar
        async def on_response(resp: Response):
            url = resp.url
            if not should_capture(url):
                return
            try:
                ct = resp.headers.get("content-type", "")
                if "json" not in ct:
                    return
                body = await resp.body()
                if not body:
                    return
                data = json.loads(body)
                t_new, b_new = extract_items_from_json(data)
                if t_new or b_new:
                    tasks_raw.extend(t_new)
                    buckets_raw.extend(b_new)
                    captured_urls.append(url)
                    print(f"   [NET] +{len(t_new)} tarefas, +{len(b_new)} buckets | {url[-80:]}")
            except Exception:
                pass

        page.on("response", lambda r: asyncio.ensure_future(on_response(r)))

        print(f"\n1. Abrindo o Planner...")
        try:
            await page.goto(PLANNER_URL, wait_until="commit", timeout=30000)
        except Exception:
            pass  # Redirect para login eh normal

        print("\n" + "=" * 60)
        print("  JANELA ABERTA.")
        print("")
        print("  Se pedir LOGIN: faca login na conta Microsoft.")
        print("  Aguarde o Planner mostrar as tarefas no quadro.")
        print("  IMPORTANTE: clique no plano 'Entregas Tecnicas'")
        print("  e aguarde TODOS os cartoes aparecerem.")
        print("")
        print("  Os dados serao capturados automaticamente da rede.")
        print("  Quando as tarefas estiverem visiveis, volte aqui.")
        print("=" * 60)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, input, "\n  >> ENTER quando as tarefas estiverem visiveis: ")

        # Aguarda requisicoes pendentes terminarem
        print("   Aguardando ultimas requisicoes de rede (5s)...")
        await asyncio.sleep(5)

        print(f"\n2. Dados capturados via rede:")
        print(f"   URLs interceptadas: {len(captured_urls)}")
        print(f"   Tarefas brutas: {len(tasks_raw)}")
        print(f"   Buckets brutos: {len(buckets_raw)}")

        # Fallback para IDB se nenhuma tarefa capturada
        if not tasks_raw:
            print("\n   AVISO: Nenhuma tarefa capturada via rede. Tentando IDB...")
            try:
                idb = await page.evaluate(JS_READ_IDB)
                tasks_raw.extend(idb.get("tasks", []))
                buckets_raw.extend(idb.get("buckets", []))
                print(f"   IDB: {len(idb.get('tasks',[]))} tarefas, banco={idb.get('dbName')}")
                print(f"   IDB stores: {idb.get('allStores', [])}")
            except Exception as e:
                print(f"   ERRO IDB: {e}")

        # Monta mapa de buckets
        bucket_map: dict = {}
        for b in buckets_raw:
            bid = b.get("id") or b.get("bucketId", "")
            bname = b.get("name", "")
            if bid and bname:
                bucket_map[bid] = bname

        # Deduplica e merge tarefas
        task_map = merge_tasks(tasks_raw)

        print(f"\n3. Filtrando tarefas...")
        filtered, bucket_map = filter_tasks(task_map, bucket_map)

        if not filtered:
            print("\n   ERRO: Nenhuma tarefa encontrada apos filtro.")
            print("   Dicas:")
            print("   - Certifique-se de que o plano 'Entregas Tecnicas' carregou")
            print("   - Role a pagina para baixo para carregar todos os cartoes")
            print("   - Aguarde o Planner sincronizar completamente")
            print(f"\n   Total de tasks (sem filtro): {len(task_map)}")
            if task_map:
                sample = next(iter(task_map.values()))
                print(f"   Amostra: {sample.get('title','')[:50]}, planId={sample.get('planId','')}")
            await context.close()
            return

        tasks_with_chat = [t for t in filtered if t.get("hasActiveUpdates") or t.get("hasChat")]
        print(f"\n4. Tarefas com comentarios: {len(tasks_with_chat)}")

        comments = {}
        if tasks_with_chat:
            print("   Coletando comentarios...")
            comments = await collect_comments(page, tasks_with_chat)

        print(f"\n5. Gerando Excel...")
        generate_excel(filtered, bucket_map, comments, OUTPUT_FILE)

        await context.close()
        print("\nConcluido!")


if __name__ == "__main__":
    asyncio.run(main())
