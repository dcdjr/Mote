"""Shared model settings avoid context-size reloads between routing and answering."""
import os


def model_options():
    context = int(os.getenv('MOTE_CONTEXT_LENGTH', '8192'))
    if not 2048 <= context <= 131072:
        raise ValueError('MOTE_CONTEXT_LENGTH must be between 2048 and 131072.')
    # Explicit -1 requests Ollama's dynamic GPU offload, overriding CPU-only model defaults.
    options = {'num_ctx': context, 'num_gpu': -1}
    gpu = os.getenv('MOTE_GPU_LAYERS', 'auto').lower()
    if gpu != 'auto':
        layers = int(gpu)
        if layers < -1:
            raise ValueError('MOTE_GPU_LAYERS must be auto, -1, or a nonnegative integer.')
        options['num_gpu'] = layers
    return options


def keep_alive():
    return os.getenv('MOTE_KEEP_ALIVE', '30m')
