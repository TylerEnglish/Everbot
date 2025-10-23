# (optional) force 200 caps
$env:EVERABOT_MMLU_CAP='10'
$env:EVERABOT_GSM8K_CAP='10'
$env:EVERABOT_HELLASWAG_CAP='10'

# (if needed) ensure Ollama env is set
$env:EVERABOT_LLM='ollama'
$env:EVERABOT_OLLAMA_MODEL='llama3'

poetry run everabot eval


docker run --rm -it `
  -e EVERABOT_LLM=transformers `
  -e EVERABOT_TRANSFORMERS_MODEL=distilgpt2 `
  everabot:cpu llm selftest --prompt "Say exactly: OK"


docker run --rm -it `
  --cpus 4 --memory 8g `
  -e EVERABOT_LLM=transformers `
  -e EVERABOT_TRANSFORMERS_MODEL=distilgpt2 `
  everabot:cpu eval


docker run --rm -it `
  --cpus 4 --memory 8g `
  -v C:\models:/models `
  -v $PWD\snapshots:/workspace/snapshots `
  -e EVERABOT_LLM=llama_cpp `
  -e EVERABOT_LLAMA_PATH=/models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf `
  everabot:cpu llm selftest --prompt "Say exactly: OK"


docker run -d --name everabot `
  -v $PWD\snapshots:/workspace/snapshots `
  -e EVERABOT_LLM=transformers -e EVERABOT_TRANSFORMERS_MODEL=distilgpt2 `
  everabot:cpu

docker inspect --format='{{.State.Health.Status}}' everabot
# expect: healthy
