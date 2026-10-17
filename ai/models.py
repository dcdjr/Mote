"""Installed answer-model eligibility, based on runtime metadata, never file size."""
import math
import os
import re
from threading import Event

from ollama import Client
from PySide6.QtCore import QThread, Signal

MAX_PARAMETERS = 6_000_000_000


def parameter_count(info):
    metadata = info.get('model_info') or info.get('modelinfo') or {}
    count = metadata.get('general.parameter_count')
    if 'general.parameter_count' in metadata:
        return int(count) if type(count) in (int, float) and math.isfinite(count) and count > 0 and int(count) == count else None
    text = (info.get('details') or {}).get('parameter_size', '')
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*([BM])\s*', text, re.IGNORECASE)
    if match:
        return int(float(match[1]) * (1e9 if match[2].upper() == 'B' else 1e6))
    return None


def eligibility(name, info):
    # E4B's effective/active count must never be mistaken for its total count.
    if name.split(':')[0].split('/')[-1] == 'gemma4' and name.split(':')[-1].startswith('e4b'):
        return False, 'Exceeds 6B total parameters (Gemma 4 E4B is about 8B total).'
    if info.get('remote_model') or info.get('remote_host') or name.endswith(':cloud'):
        return False, 'Cloud model; select a locally installed model.'
    if 'vision' not in info.get('capabilities', []):
        return False, 'Installed runtime does not advertise vision.'
    count = parameter_count(info)
    if count is None:
        return False, 'Total parameter count is missing or ambiguous.'
    if count > MAX_PARAMETERS:
        return False, f'{count / 1e9:.1f}B total parameters exceeds the 6B limit.'
    return True, f'Vision · {count / 1e9:.1f}B total parameters'


def discover_models(client, cancel=None):
    response = client.list()
    response = response if isinstance(response, dict) else response.model_dump()
    rows = []
    for model in response.get('models', []):
        if cancel and cancel.is_set():
            break
        name = model.get('model') or model.get('name')
        if not isinstance(name, str):
            continue
        try:
            info = client.show(name)
            info = info if isinstance(info, dict) else info.model_dump()
            allowed, detail = eligibility(name, info)
            rows.append({'name': name, 'eligible': allowed, 'detail': detail})
        except Exception as error:  # noqa: BLE001 - metadata failures exclude, never approve, a candidate
            rows.append({'name': name, 'eligible': False, 'detail': f'Could not inspect model: {error}'})
    return rows


class ModelScan(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cancel = Event()

    def run(self):
        try:
            client = Client(host=os.getenv('MOTE_OLLAMA_HOST', 'http://localhost:11434'), timeout=5)
            rows = discover_models(client, self.cancel)
            if not self.cancel.is_set():
                self.ready.emit(rows)
        except Exception:  # noqa: BLE001 - report network/metadata errors across the worker boundary
            self.failed.emit('Cannot reach Ollama. Start/install Ollama, then click Refresh.')
