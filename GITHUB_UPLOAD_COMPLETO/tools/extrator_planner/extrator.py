# -*- coding: utf-8 -*-
"""
Extrator de Tarefas - Microsoft Planner
Plano: Entregas Tecnicas
Filtro: Helbert + Matheus + Wesley + flag MEDICOES
Saida: Demandas_Medicoes_Completo.xlsx

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
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from playwright.async_api import async_playwright, Page, BrowserContext

# CONFIGURACOES =========================================================
PLAN_ID       = "JOHzljvSKkmfSsQ7SekCnWUAA8cz"
TENANT_ID     = "953ea640-963d-4af5-844c-03f21ebc048f"
IDB_NAME      = None

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

# JAVASCRIPT HELPERS =====================================================
JS_READ_IDB = """
async () => {
    return new Promise((resolve, reject) => {
        const dbs = indexedDB.databases
            ? indexedDB.databases()
            : Promise.resolve([]);

        dbs.then(list => {
            const plannerDb = list.find(d => d.name && d.name.startsWith('PlannerV2_1_'));
            if (!plannerDb) return reject('IDB nao encontrado');
            const req = indexedDB.open(plannerDb.name);
            req.onsuccess = () => {
                const db = req.result;
                const stores = Array.from(db.objectStoreNames);

                const readStore = (storeName) => new Promise((res, rej) => {
                    const tx = db.transaction(storeName, 'readonly');
                    const store = tx.objectStore(storeName);
                    const all = store.getAll();
                    all.onsuccess = () => res(all.result);
                    all.onerror   = () => rej(all.error);
                });

                Promise.all([
                    readStore('task'),
                    readStore('bucket'),
                ]).then(([tasks, buckets]) => {
                    resolve({ tasks, buckets, dbName: plannerDb.name });
                }).catch(reject);
            };
            req.onerror = () => reject(req.error);
        }).catch(reject);
    });
}
"""

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
        // filtra timestamps (ex: "14/05 09:32")
        if (/^\\d{1,2}\\/\\d{1,2}\\s+\\d{1,2}:\\d{2}$/.test(t)) continue;
        // filtra "Hoje", "Ontem", separadores de data
        if (/^(Hoje|Ontem|Yesterday|Today)$/i.test(t)) continue;
        // filtra nomes de autor curtos (ate 40 chars, sem espaco duplo)
        if (t.length <= 50 && /^[A-Za-zA-u\\s\\.]+$/.test(t) && !t.includes('  ')) continue;
        texts.push(t);
    }
    return texts.join('\\n---\\n');
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
    percentComplete = task.get("percentComplete", 0)
    if percentComplete == 100:
        return "Concluida"
    completedDateTime = task.get("completedDateTime")
    if completedDateTime:
        return "Concluida"
    if percentComplete == 50:
        return "Em andamento"
    if percentComplete == 0:
        return "Nao iniciada"
    return f"{percentComplete}%"


def build_checklist(task: dict):
    checklist = task.get("checklist", {})
    if not checklist:
        return "", ""
    items = list(checklist.values()) if isinstance(checklist, dict) else checklist
    linhas = []
    done = 0
    for item in items:
        title = item.get("title", "")
        checked = item.get("isChecked", False)
        marker = "OK" if checked else "[ ]"
        linhas.append(f"{marker} {title}")
        if checked:
            done += 1
    return "\n".join(linhas), f"{done}/{len(items)}"


def build_assignees(task: dict) -> str:
    assignments = task.get("assignments", {})
    if not assignments:
        return ""
    names = []
    for uid in assignments.keys():
        names.append(ASSIGNEE_NAMES.get(uid, uid[:8] + "..."))
    return ", ".join(names)


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


def filter_tasks(tasks: list, buckets: list):
    bucket_map = {b["id"]: b.get("name", "") for b in buckets}

    filtered = []
    for t in tasks:
        if t.get("planId") != PLAN_ID:
            continue
        assignments = t.get("assignments", {})
        if not any(uid in ASSIGNEES for uid in assignments.keys()):
            continue
        pv = t.get("planView", {})
        cat_ids = pv.get("appliedCategoryIds", []) if pv else []
        applied = t.get("appliedCategories", {})

        has_medicoes = (
            MEDICOES_CAT in cat_ids
            or (isinstance(applied, dict) and applied.get(MEDICOES_CAT, False))
        )
        if not has_medicoes:
            continue

        filtered.append(t)

    print(f"OK: Tarefas filtradas: {len(filtered)} de {len(tasks)} no plano")
    return filtered, bucket_map


# COMENTARIOS VIA PLAYWRIGHT =============================================
async def collect_comments(page: Page, tasks_with_chat: list) -> dict:
    """Navega para cada tarefa e extrai os comentarios."""
    comments = {}
    total = len(tasks_with_chat)

    for i, task in enumerate(tasks_with_chat, 1):
        task_id = task.get("id") or task.get("taskId", "")
        if not task_id:
            continue

        url = TASK_URL.format(plan_id=PLAN_ID, task_id=task_id, tid=TENANT_ID)
        print(f"  [{i}/{total}] Abrindo tarefa {task_id[:12]}...")

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_selector('[class*="fui-Chat"]', timeout=25000)
                await asyncio.sleep(3)
            except Exception:
                print(f"    AVISO: Chat nao carregou para {task_id[:12]}")
                comments[task_id] = ""
                continue

            result = await page.evaluate(JS_EXTRACT_COMMENTS)
            comments[task_id] = result or ""
            count = len([c for c in (result or "").split("---") if c.strip()])
            print(f"    OK: {count} comentario(s) coletado(s)")

        except Exception as e:
            print(f"    ERRO: {e}")
            comments[task_id] = ""

    return comments


# GERACAO DO EXCEL =======================================================
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
            "Sim" if comentarios.strip() else ("Sim (vazio)" if task.get("hasActiveUpdates") or task.get("hasDescription") else "Nao"),
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
    print("=" * 60)

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

        print(f"\n1. Navegando para o plano...")
        await page.goto(PLANNER_URL, wait_until="domcontentloaded", timeout=60000)

        print("   Aguardando carregamento / login (max 120s)...")
        try:
            await page.wait_for_url("**/webui/plan/**", timeout=120000)
        except Exception:
            pass

        print("   Aguardando dados do IndexedDB...")
        await asyncio.sleep(8)

        print("\n2. Lendo tarefas do IndexedDB...")
        try:
            idb_data = await page.evaluate(JS_READ_IDB)
        except Exception as e:
            print(f"   ERRO ao ler IDB: {e}")
            await context.close()
            return

        tasks_raw  = idb_data.get("tasks", [])
        buckets    = idb_data.get("buckets", [])
        db_name    = idb_data.get("dbName", "")
        print(f"   Banco: {db_name}")
        print(f"   Total de tarefas no IDB: {len(tasks_raw)}")

        print("\n3. Filtrando tarefas...")
        filtered, bucket_map = filter_tasks(tasks_raw, buckets)

        if not filtered:
            print("   ERRO: Nenhuma tarefa encontrada. Verifique os IDs.")
            await context.close()
            return

        tasks_with_chat = [t for t in filtered if t.get("hasActiveUpdates") or t.get("hasChat")]
        print(f"\n4. Tarefas com comentarios: {len(tasks_with_chat)}")

        comments = {}
        if tasks_with_chat:
            print("   Iniciando coleta de comentarios...")
            comments = await collect_comments(page, tasks_with_chat)
        else:
            print("   Nenhuma tarefa com comentarios.")

        print(f"\n5. Gerando Excel...")
        generate_excel(filtered, bucket_map, comments, OUTPUT_FILE)

        await context.close()
        print("\nConcluido!")


if __name__ == "__main__":
    asyncio.run(main())
