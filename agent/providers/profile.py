"""Pinned text-only Qwen profile, independent of visitor-controlled input."""
from dataclasses import asdict, dataclass
import hashlib
import json

TOKENIZER_REVISION = "c202236235762e1c871ad0ccb60c8ee5ba337b9a"
TOKENIZER_URL = f"https://huggingface.co/Qwen/Qwen3.5-9B/resolve/{TOKENIZER_REVISION}/tokenizer.json"
TOKENIZER_SHA256 = "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42"
TOKENIZER_BYTES = 12807982
TOKENIZERS_VERSION = "0.23.3"
SIZING_VERSION = "qwen35-bpe-envelope-v1"
GRAMMAR_VERSION = "ollama-grammar-projection-v1"


@dataclass(frozen=True)
class OllamaProfile:
    profile_id: str = "local-ollama-v2"
    model_name: str = "qwen3.5:9b-q4_K_M"
    model_digest: str = "56671c2ab9385f9cfcb404638e32cd62d88e3501d44822208363c010179a3c90"
    server_version: str = "0.40.16"
    context_tokens: int = 16384
    plan_output_tokens: int = 2048
    answer_output_tokens: int = 2048
    safety_tokens: int = 512
    temperature: int = 0
    presence_penalty: int = 0
    think: bool = False
    keep_alive: str = "30m"

    def fingerprint(self):
        data = {**asdict(self), "tokenizer_sha256": TOKENIZER_SHA256,
                "tokenizers_version": TOKENIZERS_VERSION,
                "sizing_version": SIZING_VERSION, "grammar_version": GRAMMAR_VERSION}
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
