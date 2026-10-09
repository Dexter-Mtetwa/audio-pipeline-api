from faster_whisper import WhisperModel
from pyannote.audio import Pipeline
import torch
import soundfile as sf
import torchaudio.functional as AF

import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

CONTEXT_PADDING = float(os.environ.get("CONTEXT_PADDING", "0.75"))


_diarization_pipeline = None
_whisper_model = None


# This function initializes and returns the speaker diarization pipeline from the pyannote library. It uses a pre-trained model for speaker diarization and ensures that the model is loaded onto the CPU.
def get_diarization_pipeline():
    global _diarization_pipeline
    if _diarization_pipeline is None:
        _diarization_pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1")
        _diarization_pipeline.to(torch.device("cpu"))
    return _diarization_pipeline


# This function initializes and returns the Whisper model from the faster-whisper library. It uses a pre-trained "small" model for speech-to-text transcription and ensures that the model is loaded onto the CPU.
def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        _whisper_model = WhisperModel("small", device="cpu", compute_type="int8")
    return _whisper_model


logger.info(f"Running pipeline with CONTEXT_PADDING={CONTEXT_PADDING}")
# This function runs the audio processing pipeline, which includes speaker diarization and speech-to-text transcription using Whisper.
def run_pipeline(audio_path: str):
    # Initialize the diarization pipeline and Whisper model
    diarization_pipeline = get_diarization_pipeline()
    whisper_model = get_whisper_model()

    # Read the audio file using soundfile and convert it to a PyTorch tensor.
    audio_array, sample_rate = sf.read(audio_path)
    waveform = torch.tensor(audio_array, dtype=torch.float32).T
    mono_waveform = waveform.mean(dim=0)  # Convert to mono by averaging channels
    total_duration = mono_waveform.shape[0] / sample_rate  # Calculate total duration in seconds

    diarization = diarization_pipeline({"waveform": waveform, "sample_rate": sample_rate})
    result = diarization.speaker_diarization

    diarization_turns = []
    for turn, _, speaker in result.itertracks(yield_label=True):
        if turn.end - turn.start >= 0.4:
            diarization_turns.append((turn.start, turn.end, speaker))

    output = []
    # Process each diarization turn, adding context padding and transcribing the audio segment using Whisper.
    for turn_start, turn_end, speaker in diarization_turns:
        padded_start = max(0, turn_start - CONTEXT_PADDING)
        padded_end = min(total_duration, turn_end + CONTEXT_PADDING)

        start_sample = int(padded_start * sample_rate)
        end_sample = int(padded_end * sample_rate)
        audio_slice_tensor = mono_waveform[start_sample:end_sample]  # Extract the audio slice corresponding to the padded turn

        # Resample the audio slice to 16kHz if it's not already at that sample rate, as Whisper expects audio input at 16kHz.
        if sample_rate != 16000:
            audio_slice_tensor = AF.resample(audio_slice_tensor, orig_freq=sample_rate, new_freq=16000)

        # Convert the audio slice tensor to a NumPy array for Whisper processing.
        audio_slice = audio_slice_tensor.numpy()
        segments, _ = whisper_model.transcribe(audio_slice, beam_size=5, word_timestamps=True)

        kept_words = []
        # For each segment returned by Whisper, check if the word timestamps fall within the padded turn boundaries. If they do, keep the word for the final output.
        for seg in segments:
            for word in seg.words:
                absolute_start = padded_start + word.start
                absolute_end = padded_start + word.end
                if absolute_end >= turn_start and absolute_start <= turn_end:
                    kept_words.append(word.word)

        text = "".join(kept_words).strip()
        output.append({
            "start": turn_start,
            "end": turn_end,
            "speaker": speaker,
            "text": text,
        })

    return output, total_duration