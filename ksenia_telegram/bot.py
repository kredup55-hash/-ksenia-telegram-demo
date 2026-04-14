import numpy as np
import io
import wave

def humanize_audio(audio_bytes: bytes) -> bytes:
    """Лёгкая постобработка: микро-шум, компрессия под телефон, нормализация. Без librosa."""
    try:
        # 1. Декодируем MP3 в PCM через pydub (надёжнее для PaaS)
        from pydub import AudioSegment
        audio = AudioSegment.from_mp3(io.BytesIO(audio_bytes))
        samples = np.array(audio.get_array_of_samples()).astype(np.float32)
        sr = audio.frame_rate
        channels = audio.channels

        # 2. Микро-шум комнаты (0.3-0.5%)
        noise = np.random.normal(0, 0.003, samples.shape)
        samples = samples + noise

        # 3. Лёгкая компрессия (имитация телефонной линии)
        threshold = -20.0
        ratio = 2.0
        audio_db = 20 * np.log10(np.abs(samples) + 1e-6)
        mask = audio_db > threshold
        samples[mask] = np.sign(samples[mask]) * (10 ** ((threshold + (audio_db[mask] - threshold) / ratio) / 20.0))

        # 4. Нормализация громкости (~-16 LUFS)
        rms = np.sqrt(np.mean(samples ** 2))
        target_rms = 10 ** (-16 / 20)
        if rms > 0:
            samples = samples * (target_rms / rms)

        # 5. Защита от клиппинга
        samples = np.clip(samples, -0.99, 0.99).astype(np.int16)

        # 6. Экспорт обратно в MP3
        out = io.BytesIO()
        processed = audio._spawn(samples.tobytes())
        processed.export(out, format="mp3", bitrate="192k")
        return out.getvalue()
    except Exception as e:
        logger.error(f"Audio humanize error: {e}")
        return audio_bytes
