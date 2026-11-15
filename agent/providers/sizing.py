"""Pinned BPE sizing for Ollama's two-message, no-tools Qwen3.5 renderer."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from importlib.metadata import version

from tokenizers import Tokenizer

from agent.providers.profile import TOKENIZER_BYTES, TOKENIZER_SHA256, TOKENIZERS_VERSION


class QwenPromptSizer:
    """Use both raw and NFC BPE counts, plus 64 renderer/parser tokens.

    The full authoritative schema is already in C's system message. The format
    grammar is not another prompt. No character-to-token ratio is used. The
    bound must be compared with server prompt counts during capability probes.
    """

    def __init__(self, path):
        if version("tokenizers") != TOKENIZERS_VERSION:
            raise ValueError("The tokenizer runtime does not match the pinned profile.")
        path = Path(path)
        if path.stat().st_size != TOKENIZER_BYTES:
            raise ValueError("The configured tokenizer has an unexpected size.")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != TOKENIZER_SHA256:
            raise ValueError("The configured tokenizer does not match the pinned release.")
        self.definition = json.loads(raw)
        self.normalized = Tokenizer.from_str(raw.decode())
        plain = deepcopy(self.definition)
        plain["normalizer"] = None
        self.raw = Tokenizer.from_str(json.dumps(plain, ensure_ascii=False))
        self.sha256 = TOKENIZER_SHA256

    def verify_model(self, info):
        """Compare actual GGUF vocabulary IDs and every ordered merge rule."""
        vocab = {**self.definition["model"]["vocab"],
                 **{item["content"]: item["id"] for item in self.definition["added_tokens"]}}
        tokens = info.get("tokenizer.ggml.tokens", [])
        merges = [" ".join(item) if isinstance(item, list) else item
                  for item in self.definition["model"]["merges"]]
        if (info.get("tokenizer.ggml.model") != "gpt2"
                or info.get("tokenizer.ggml.pre") != "qwen35"
                or info.get("tokenizer.ggml.add_bos_token") is not False
                or any(index >= len(tokens) or tokens[index] != token for token, index in vocab.items())
                or info.get("tokenizer.ggml.merges") != merges):
            raise ValueError("The GGUF tokenizer does not match the configured BPE release.")

    def counts(self, messages):
        if (type(messages) is not list or len(messages) != 2
                or [item.get("role") for item in messages] != ["system", "user"]
                or any(set(item) != {"role", "content"} or type(item["content"]) is not str for item in messages)):
            raise ValueError("This sizer supports only the workflow's system/user text envelope.")
        rendered = "".join("<|im_start|>" + item["role"] + "\n" + item["content"].strip()
                           + "<|im_end|>\n" for item in messages) + "<|im_start|>assistant\n"
        return tuple(len(tokenizer.encode(rendered, add_special_tokens=False).ids)
                     for tokenizer in (self.raw, self.normalized))

    def __call__(self, messages, output_schema):
        if type(output_schema) is not dict:
            raise ValueError("A structured output schema is required.")
        return max(self.counts(messages)) + 64
