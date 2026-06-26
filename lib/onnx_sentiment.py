import gc
from pathlib import Path
from typing import Optional
import json

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

_SENTIMENT_PIPELINE = None
_MODEL_DIR: Optional[Path] = None


def load_onnx_pipeline(model_dir: Path):
    global _SENTIMENT_PIPELINE, _MODEL_DIR
    if _SENTIMENT_PIPELINE is None or _MODEL_DIR != model_dir:
        _SENTIMENT_PIPELINE = _OnnxPipeline(model_dir)
        _MODEL_DIR = model_dir
    return _SENTIMENT_PIPELINE


class _OnnxPipeline:
    def __init__(self, model_dir: Path):
        tokenizer_file = model_dir / "tokenizer.json"
        if not tokenizer_file.exists():
            raise FileNotFoundError(f"tokenizer.json not found in {model_dir}")
        self.tokenizer = Tokenizer.from_file(str(tokenizer_file))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()

        onnx_file = next(model_dir.glob("*.onnx"), None)
        if onnx_file is None:
            raise FileNotFoundError(f"No .onnx file found in {model_dir}")

        opts = ort.SessionOptions()
        opts.enable_cpu_mem_arena = False
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1

        self.session = ort.InferenceSession(str(onnx_file), sess_options=opts, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.attention_mask_name = self.session.get_inputs()[1].name if len(self.session.get_inputs()) > 0 else None

        with open(model_dir / "config.json") as f:
            config = json.load(f)
        self.id2label = config.get("id2label", {})
        self.label_ids = sorted(self.id2label.keys())

    def __call__(self, texts, **kwargs):
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        encoded = self.tokenizer.encode_batch(texts)
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()

        input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)

        feed = {self.input_name: input_ids}
        if self.attention_mask_name:
            feed[self.attention_mask_name] = attention_mask

        logits = self.session.run(None, feed)[0]
        del feed, input_ids, attention_mask, encoded

        exp = np.exp(logits - logits.max(axis=-1, keepdims=True))
        probs = exp / exp.sum(axis=-1, keepdims=True)
        del logits, exp

        results = []
        for i in range(len(texts)):
            scores = probs[i].tolist()
            top = int(np.argmax(scores))
            results.append([
                {"label": self.id2label[self.label_ids[top]], "score": round(scores[top], 4)}
            ])
        return results
