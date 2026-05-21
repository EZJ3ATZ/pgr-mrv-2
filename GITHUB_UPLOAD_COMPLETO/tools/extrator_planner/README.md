# Extrator Planner — Demandas Medições

Script Python que extrai as tarefas do plano **Entregas Técnicas** do Microsoft Planner, filtra
pelas tarefas atribuídas a **Helbert / Matheus / Wesley** com a flag **MEDIÇÕES** e gera um
arquivo Excel completo para importar no sistema PGR-MRV.

## Instalação (1ª vez)

```powershell
cd C:\Users\mathe\Downloads\GITHUB_UPLOAD_COMPLETO\tools\extrator_planner

py -m pip install playwright openpyxl
py -m playwright install chromium
```

## Como usar

```powershell
py extrator.py
```

1. Abre janela do Chromium → faça login Microsoft (só na 1ª vez, fica salvo em `playwright_profile/`)
2. Aguarda 8s o IndexedDB do Planner carregar
3. Filtra tarefas (Helbert/Matheus/Wesley + MEDIÇÕES)
4. Para cada tarefa com comentários, abre e extrai
5. Gera `Demandas_Medicoes_Completo.xlsx` (16 colunas)

## Saída

Arquivo `Demandas_Medicoes_Completo.xlsx` com colunas:

| # | Coluna |
|---|--------|
| 1 | Nome da Tarefa |
| 2 | OS |
| 3 | Data de Criação |
| 4 | Data de Prazo |
| 5 | Data de Conclusão |
| 6 | Responsável(is) |
| 7 | Status |
| 8 | Progresso (%) |
| 9 | Checklist |
| 10 | Checklist Progresso |
| 11 | Grupo (Bucket) |
| 12 | Etiquetas |
| 13 | Descrição |
| 14 | CNPJ |
| 15 | Tem Comentários |
| 16 | Comentários |

## Próximo passo

Suba o XLSX gerado em https://gerador-de-pgr-mrv-production.up.railway.app/ →
aba **Controle de Medições → Importar → Importar do Planner**.

## Configuração

Caso mude o time ou o plano, edite no topo de `extrator.py`:

```python
PLAN_ID       = "JOHzljvSKkmfSsQ7SekCnWUAA8cz"
HELBERT_ID    = "d7c006ff-d86b-4cf6-9f67-bbaa019f036e"
MATHEUS_ID    = "ba8e6795-6926-4c1f-ae38-aef4df737070"
WESLEY_ID     = "a4256cbb-7af4-4f9e-9c22-d47ee21966b5"
MEDICOES_CAT  = "00000009000000000000000000000000"
```
