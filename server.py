import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
import onnxruntime as ort
from transformers import AutoTokenizer
import numpy as np
import os

app = FastAPI()

model_dir = "multilingual-e5-small-onnx"
os.makedirs(model_dir, exist_ok=True)

print("Загрузка локального токенайзера...")
# Загружаем токенайзер из локальной папки модели
tokenizer = AutoTokenizer.from_pretrained(model_dir)

onnx_path = os.path.join(model_dir, "model.onnx")
opts = ort.SessionOptions()
import multiprocessing
cores = multiprocessing.cpu_count()
opts.intra_op_num_threads = cores
opts.inter_op_num_threads = 1
opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

print("Запуск ONNX Runtime сессии для intfloat/multilingual-e5-small...")
session = ort.InferenceSession(onnx_path, sess_options=opts, providers=['CPUExecutionProvider'])

expected_inputs = [i.name for i in session.get_inputs()]
print("Ожидаемые входы модели:", expected_inputs)

class EmbedRequest(BaseModel):
    texts: list[str]

def mean_pooling(model_output, attention_mask):
    token_embeddings = model_output[0]
    input_mask_expanded = np.expand_dims(attention_mask, axis=-1).astype(float)
    sum_embeddings = np.sum(token_embeddings * input_mask_expanded, axis=1)
    sum_mask = np.clip(input_mask_expanded.sum(axis=1), a_min=1e-9, a_max=None)
    return sum_embeddings / sum_mask

@app.post("/embed")
async def get_embeddings(req: EmbedRequest):
    encoded_input = tokenizer(
        req.texts,
        padding=True,
        truncation=True,
        max_length=512,
        return_tensors="np"
    )
    
    onnx_inputs = {}
    for name in expected_inputs:
        if name in encoded_input:
            onnx_inputs[name] = encoded_input[name].astype(np.int64)
        elif name == "token_type_ids":
            onnx_inputs[name] = np.zeros_like(encoded_input["input_ids"]).astype(np.int64)

    model_output = session.run(None, onnx_inputs)
    sentence_embeddings = mean_pooling(model_output, encoded_input["attention_mask"])
    
    # Нормализация
    norms = np.linalg.norm(sentence_embeddings, axis=1, keepdims=True)
    sentence_embeddings = sentence_embeddings / np.maximum(norms, 1e-9)
    
    return {"embeddings": sentence_embeddings.tolist()}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
