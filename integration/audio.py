"""Windows Core Audio master endpoint volume, imported only on Windows."""
from contextlib import contextmanager


@contextmanager
def endpoint_volume():
    from comtypes import CoInitialize, CoUninitialize
    from pycaw.pycaw import AudioUtilities
    CoInitialize()
    try:
        endpoint = AudioUtilities.GetSpeakers().EndpointVolume
        yield endpoint
    finally:
        CoUninitialize()


def change_volume(name, arguments):
    with endpoint_volume() as endpoint:
        if name == 'set_volume':
            level = arguments['level_percent'] / 100
            endpoint.SetMasterVolumeLevelScalar(level, None)
            if level > 0:
                endpoint.SetMute(False, None)
        else:
            operation = arguments['operation']
            if operation in ('mute', 'unmute'):
                endpoint.SetMute(operation == 'mute', None)
                return 'Muted system audio.' if operation == 'mute' else 'Unmuted system audio.'
            delta = 0.05 if operation == 'increase' else -0.05
            level = max(0.0, min(1.0, endpoint.GetMasterVolumeLevelScalar() + delta))
            endpoint.SetMasterVolumeLevelScalar(level, None)
            if operation == 'increase':
                endpoint.SetMute(False, None)
        return f'Set system volume to {round(level * 100)}%.'
