
# 1) Download a small CPU-friendly GGUF (TinyLlama Q4_K_M) into snapshots\models
$code = @'
from huggingface_hub import snapshot_download
p = snapshot_download(
    repo_id="TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF",
    allow_patterns=["*Q4_K_M.gguf"],
    local_dir="snapshots/models"  # symlinks setting is deprecated/ignored by HF Hub
)
print("Saved to:", p)
'@
$code | poetry run python -

# 2) Point Everabot at the local GGUF (pick the first *Q4_K_M.gguf we just downloaded)
$env:EVERABOT_LLM = "llama_cpp"
$gguf = Get-ChildItem -Recurse -File snapshots\models -Filter "*Q4_K_M.gguf" |
        Select-Object -First 1 -ExpandProperty FullName
if (-not $gguf) { throw "GGUF not found yet. Re-run step 1 and wait for it to finish." }
$env:EVERABOT_LLAMA_PATH = $gguf
Write-Host "LLM = $env:EVERABOT_LLM"
Write-Host "GGUF = $env:EVERABOT_LLAMA_PATH"

# 3) Quick sanity: import works and prints version
poetry run python -c "import llama_cpp, os, sys; print('llama-cpp-python', getattr(llama_cpp,'__version__','?'), 'OK'); print('GGUF ->', os.environ.get('EVERABOT_LLAMA_PATH'))"

# 4) Everabot flow
poetry run everabot rag init
poetry run everabot learn "problem_solving"
poetry run everabot snapshot latest
poetry run everabot eval
