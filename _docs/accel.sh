# inside your Everbot env
poetry run pip install -U scikit-build-core pybind11 cmake ninja
poetry run pip install -v --no-build-isolation bot/accel/cpp


poetry run everabot opt build-accel --target chunk-cpp
# Install Rust toolchain:
# Option A (winget):
winget install Rustlang.Rustup
dir $env:USERPROFILE\.cargo\bin

$bin = "$env:USERPROFILE\.cargo\bin"
if (-not ($env:Path -split ';' | %{$_ -eq $bin} | Measure-Object -Sum).Sum) {
  $env:Path += ";$bin"
  [Environment]::SetEnvironmentVariable('Path', $env:Path, 'User')
}
rustup --version


# ensure rustup/cargo/rustc are on PATH (you already did)
rustc -V
cargo -V

# build & develop-install the wheel
poetry run python -m maturin develop --release -m bot/accel/rust/Cargo.toml

# quick import smoke test
poetry run python -c "import everabot_chunk_rust as m; print('ok', hasattr(m,'chunk_text_equiv'))"
poetry run pip install -v --no-build-isolation bot/accel/cpp
poetry run python -c "import everabot_chunk_cpp as m; print('ok', hasattr(m,'chunk_text_equiv'))"

$body = @{ doc_id="doc"; text="RAG speeds up retrieval by chunking documents into overlapping windows."
           chunk_tokens=5; overlap_tokens=2 } | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri http://127.0.0.1:8765/chunk -Body $body -ContentType 'application/json'

# C++
poetry run everabot opt ab-chunk --impl cpp  --store v1 --n 2000 --chunk-tokens 400 --overlap-tokens 60
# Rust
poetry run everabot opt ab-chunk --impl rust --store v1 --n 2000 --chunk-tokens 400 --overlap-tokens 60
# Go (service must be running)
poetry run everabot opt ab-chunk --impl go   --store v1 --n 2000 --chunk-tokens 400 --overlap-tokens 60

# Baseline only
poetry run everabot opt ab-chunk --n 2000 --accel none


# self-test (CPU-busy)
py-spy record -d 2 -f flamegraph -o test.svg -- python -c "import time; t=time.time()+2\nwhile time.time()<t: pass"

# profile learn/build via your CLI (SVGs land in snapshots/profiles)
poetry run everabot opt profile learn  "grade-school math" --duration 10
poetry run everabot opt profile build  "demo goal"         --duration 10
