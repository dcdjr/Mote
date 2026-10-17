"""Read Ollama residency/capabilities; optionally preload configured models."""
import json
import os
import urllib.request
from ai.runtime import keep_alive, model_options


def inspect_models(*, warmup=False):
    host = os.getenv('MOTE_OLLAMA_HOST', 'http://localhost:11434').rstrip('/')
    timeout = float(os.getenv('MOTE_TIMEOUT', '60'))
    def request(path, payload=None):
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(host + path, data=data, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    names = list(dict.fromkeys([os.getenv('MOTE_ROUTER_MODEL', 'gemma4:e2b'), os.getenv('MOTE_MODEL', 'gemma4:e2b')]))
    if warmup:
        for name in names:
            request('/api/chat', {'model': name, 'messages': [], 'stream': False,
                                  'options': model_options(), 'keep_alive': keep_alive()})
    running = request('/api/ps').get('models', [])
    result = []
    for name in names:
        info = request('/api/show', {'model': name})
        loaded = next((item for item in running if item.get('name') in (name, name + ':latest')
                       or item.get('model') in (name, name + ':latest')), None)
        item = {'model': name, 'capabilities': info.get('capabilities', []), 'loaded': loaded is not None}
        if loaded:
            size, vram = loaded.get('size', 0), loaded.get('size_vram', 0)
            item.update(size_bytes=size, vram_bytes=vram,
                        gpu_percent=round(100 * vram / size, 1) if size else None)
        result.append(item)
    return {'endpoint': host, 'options': model_options(), 'keep_alive': keep_alive(), 'models': result,
            'note': 'GPU residency is reported by Ollama; 100% requires a supported GPU and sufficient memory. On Apple Silicon memory is unified.'}
