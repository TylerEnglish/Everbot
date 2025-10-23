# (optional) force 200 caps
$env:EVERABOT_MMLU_CAP='10'
$env:EVERABOT_GSM8K_CAP='10'
$env:EVERABOT_HELLASWAG_CAP='10'

# (if needed) ensure Ollama env is set
$env:EVERABOT_LLM='ollama'
$env:EVERABOT_OLLAMA_MODEL='llama3'

poetry run everabot eval


# 1) Apply the C++ token-aware patch and rebuild
poetry run pip uninstall -y everabot_chunk_cpp
poetry run pip install -v --no-build-isolation bot/accel/cpp

# 2) Re-check A/B (bigger input)
poetry run everabot opt ab-chunk --store v1 --n 20000 --chunk-tokens 400 --overlap-tokens 60

# 3) (Optional) Unpin py-spy and update
poetry update py-spy

# 4) Finish Rust toolchain + build Rust accel
rustup toolchain install stable-x86_64-pc-windows-msvc
rustup default stable-x86_64-pc-windows-msvc
# new PowerShell…
rustc --version
poetry run python -m pip install -U maturin
poetry run python -m maturin develop --release -m bot/accel/rust/pyproject.toml
