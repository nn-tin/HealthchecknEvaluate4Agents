# TicketAgents Eval MVP

Internal healthcheck and multi-turn evaluation tool for four Dify agents.

## Setup

```powershell
cd TicketAgents
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill the Dify and Qwen credentials in `.env`. The application reads
`DIFY_BASE_URL`, the four agent-specific Dify API keys, `QWEN_BASE_URL`, and
`QWEN_BEARER` from that file.

## Run

```powershell
..\.venv\Scripts\python.exe run.py ui
..\.venv\Scripts\python.exe run.py healthcheck --agent all
..\.venv\Scripts\python.exe run.py eval --cases data/seeds/leave.yaml
```

Healthcheck and evaluation results are written to `data/runs/`.

## Tests

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
