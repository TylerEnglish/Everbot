pip install -U py-spy

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
# & "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" run llama3
# /bye
$env:Path += ";$env:LOCALAPPDATA\Programs\Ollama"
ollama --version
setx PATH "$($env:PATH);$env:LOCALAPPDATA\Programs\Ollama"
$Env:EVERABOT_XDELTA3 = "C:\Tools\xdelta3\xdelta3.exe"
setx EVERABOT_XDELTA3 "C:\Tools\xdelta3\xdelta3.exe"

# (optional) force 200 caps
$env:EVERABOT_MMLU_CAP='10'
$env:EVERABOT_GSM8K_CAP='10'
$env:EVERABOT_HELLASWAG_CAP='10'

# if needed ensure Ollama env is set
$env:EVERABOT_LLM='ollama'
$env:EVERABOT_OLLAMA_MODEL='llama3'
# optional if you didn’t use the default port:
# $env:OLLAMA_HOST = "http://127.0.0.1:11434"

Invoke-WebRequest http://127.0.0.1:11434/api/tags | Select-Object -ExpandProperty Content
poetry lock
poetry install --with llamacpp
poetry run python -c "import llama_cpp, sys; print('llama-cpp-python', getattr(llama_cpp,'__version__','imported'), 'ok on', sys.version)"
poetry update
docker build -t everabot .
$env:EVERABOT_LLM = "ollama"
$env:EVERABOT_OLLAMA_MODEL = "llama3"
poetry run everabot selfcheck
poetry run everabot eval
poetry run everabot learn web-dev
# ingest the seed corpus (ACE, SICA, RAG, VCDIFF) into snapshots/rag/v1
poetry run everabot rag init

# see counts
poetry run everabot rag stats --store v1

# smoke test a query
poetry run everabot rag query "How does ACE avoid context collapse?" --store v1

# remove any half-made copy
Remove-Item -Recurse -Force snapshots\rag\v1_copy -ErrorAction SilentlyContinue

# copy the whole tree
Copy-Item -Recurse -Force snapshots\rag\v1 snapshots\rag\v1_copy

# sanity check
Get-Item snapshots\rag\v1_copy\docs.jsonl

# prove bitwise + top-k equivalence
poetry run everabot rag prove-equivalence v1 v1_copy
poetry run everabot rag delta-roundtrip snapshots/rag/v1 snapshots/rag/v1_copy --out snapshots/rag/deltas/v1_to_v1copy
