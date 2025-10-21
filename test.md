# (optional) force 200 caps
$env:EVERABOT_MMLU_CAP='10'
$env:EVERABOT_GSM8K_CAP='10'
$env:EVERABOT_HELLASWAG_CAP='10'

# (if needed) ensure Ollama env is set
$env:EVERABOT_LLM='ollama'
$env:EVERABOT_OLLAMA_MODEL='llama3'

poetry run everabot eval
