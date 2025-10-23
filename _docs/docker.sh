docker rm -f everabot 2>$null
# from your project root
docker build --progress=plain -t everabot:cpu .

# quick smoke test: just show CLI help
docker run --rm --cpus 2 --memory 4g everabot:cpu --help
# quick LLM self-tests
docker run --rm -it `
  --cpus 2 --memory 4g `
  -e EVERABOT_LLM=transformers `
  -e EVERABOT_TRANSFORMERS_MODEL=sshleifer/tiny-gpt2 `
  everabot:cpu llm selftest --prompt "Say exactly: OK"

# adjust host path to where your .gguf lives
docker run --rm -it `
  --cpus 4 --memory 8g `
  -v $PWD\snapshots:/workspace/snapshots `
  -v C:\models:/models `
  -e EVERABOT_LLM=llama_cpp `
  -e EVERABOT_LLAMA_PATH=/models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf `
  everabot:cpu llm selftest --prompt "Say exactly: OK"

docker run -d --name everabot `
  --cpus 2 --memory 4g `
  everabot:cpu eval

docker inspect --format='{{.State.Health.Status}}' everabot
# expect: "healthy" once the HEALTHCHECK runs
