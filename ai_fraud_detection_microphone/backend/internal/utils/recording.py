"""Bounded streaming upload and audio conversion for dashboard replay."""
from pathlib import Path

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_AUDIO_SECONDS = 3600
REPLAY_RATE = 16000


class RecordingError(ValueError):
    """User-readable validation failure, without decoder internals."""


def prepare_recording(stream, size, folder):
    """Decode untrusted container bytes to bounded mono PCM WAV, without shell tools."""
    if not 0 < size <= MAX_UPLOAD_BYTES:
        raise RecordingError('Ukuran rekaman harus 1 byte hingga 50 MB')
    import av
    import soundfile as sf
    folder = Path(folder)
    original = folder / 'input.audio'
    normalized = folder / 'replay.wav'
    with original.open('wb') as output:
        remaining = size
        while remaining:
            block = stream.read(min(1024 * 1024, remaining))
            if not block:
                raise RecordingError('Upload tidak lengkap. Unggah ulang rekaman.')
            output.write(block)
            remaining -= len(block)
    count = 0
    try:
        with av.open(str(original)) as container:
            if not container.streams.audio:
                raise RecordingError('File tidak memiliki track audio')
            resampler = av.AudioResampler(format='fltp', layout='mono', rate=REPLAY_RATE)
            with sf.SoundFile(normalized, mode='w', samplerate=REPLAY_RATE, channels=1, subtype='PCM_16') as output:
                def write(frames):
                    nonlocal count
                    for frame in frames:
                        count += frame.samples
                        if count > MAX_AUDIO_SECONDS * REPLAY_RATE:
                            raise RecordingError('Durasi rekaman maksimal 60 menit')
                        output.write(frame.to_ndarray().reshape(-1))
                for frame in container.decode(audio=0):
                    frame.pts = None
                    write(resampler.resample(frame))
                write(resampler.resample(None))
        if not count:
            raise RecordingError('Rekaman tidak berisi sampel audio')
    except RecordingError:
        raise
    except Exception as exc:
        raise RecordingError('Rekaman tidak dapat dibaca. Gunakan WAV, MP3, M4A, FLAC, OGG, atau WebM yang valid.') from exc
    finally:
        original.unlink(missing_ok=True)
    return {'path':str(normalized),'duration_seconds':count / REPLAY_RATE,'sample_rate':REPLAY_RATE,'channels':1,'size_bytes':size}
