# MMLU
# from repo root (Everbot/Everbot)
New-Item -ItemType Directory -Force test/bench_mmlu/raw | Out-Null
$dst = (Resolve-Path test/bench_mmlu/raw).Path

# Option A: direct tarball (linked from the repo)
# If Invoke-WebRequest is blocked, download via a browser then extract manually.
Invoke-WebRequest "https://people.eecs.berkeley.edu/~hendrycks/data.tar" -OutFile mmlu_data.tar
tar -xf mmlu_data.tar -C $dst
Remove-Item mmlu_data.tar

# Option B: via HF mirror (same content). Download & extract subject folders so you have many CSVs under test/bench_mmlu/raw/**
# (HF UI download is easiest; the script path varies. The important part is that *.csv land anywhere under test/bench_mmlu/raw)

# HellaSwag
New-Item -ItemType Directory -Force test/bench_hellaswag/raw | Out-Null
git clone https://github.com/rowanz/hellaswag tmp_hella
# Correct path (note: tmp_hella, not tmp_hellaswag)
Copy-Item -Recurse tmp_hella\data\*.jsonl test/bench_hellaswag/raw -Force
Remove-Item -Recurse -Force tmp_hella

# GSM8K
New-Item -ItemType Directory -Force test/bench_gsm8k/raw | Out-Null
git clone https://github.com/openai/grade-school-math tmp_gsm8k
Copy-Item tmp_gsm8k/grade_school_math/data/*.jsonl test/bench_gsm8k/raw
Remove-Item -Recurse -Force tmp_gsm8k


winget install --id Ollama.Ollama -e
# Does ollama.exe exist at the usual per-user path?
Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"

# If True, run it directly (no PATH needed)
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" --version

& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" pull llama3
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" run llama3
/bye
$env:Path += ";$env:LOCALAPPDATA\Programs\Ollama"
ollama --version
setx PATH "$($env:PATH);$env:LOCALAPPDATA\Programs\Ollama"
setx EVERABOT_XDELTA3 "C:\full\path\to\xdelta3.exe"


$env:EVERABOT_LLM = "ollama"
$env:EVERABOT_OLLAMA_MODEL = "llama3"
# optional if you didn’t use the default port:
# $env:OLLAMA_HOST = "http://127.0.0.1:11434"

Invoke-WebRequest http://127.0.0.1:11434/api/tags | Select-Object -ExpandProperty Content
poetry install
poetry run everabot selfcheck   # should report blocked writes
poetry run everabot eval        # runs root test stubs

$env:EVERABOT_LLM = "ollama"
$env:EVERABOT_OLLAMA_MODEL = "llama3"
poetry run everabot eval
