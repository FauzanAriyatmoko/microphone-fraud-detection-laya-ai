"""Whisper inference adapter. Loaded once, called only by the STT worker."""
import time
import math
from dataclasses import dataclass,field
from backend.internal.utils.audio import resample,Utterance

@dataclass
class Transcript:
    utterance: Utterance
    text: str
    quality: dict
    asr_ms: float
    queue_ms: float
    queued_at: float=field(default_factory=time.monotonic)

class SpeechToText:
    def __init__(self,config):
        from faster_whisper import WhisperModel
        self.config=config
        self.model=WhisperModel(config['model_size'],device=config['device'],compute_type=config['compute_type'])

    def transcribe(self,utterance):
        start=time.monotonic();queue_ms=(start-utterance.queued_at)*1000
        audio=resample(utterance.samples,utterance.sample_rate,16000)
        segments,info=self.model.transcribe(audio,language=self.config['language'],beam_size=self.config['beam_size'],
             vad_filter=True,vad_parameters={'min_silence_duration_ms':400},condition_on_previous_text=False,
             no_speech_threshold=.6,log_prob_threshold=-1.0,compression_ratio_threshold=2.4)
        accepted=[];qualities=[];rejected=0
        for segment in segments:
            text=segment.text.strip()
            if not text:continue
            valid=all(math.isfinite(float(v)) for v in [segment.no_speech_prob,segment.avg_logprob,segment.compression_ratio]) and segment.no_speech_prob<.6 and segment.avg_logprob>=-1.0 and segment.compression_ratio<=2.4
            if not valid:rejected+=1;continue
            accepted.append(text)
            qualities.append({'avg_logprob':float(segment.avg_logprob),'no_speech_prob':float(segment.no_speech_prob)})
        return Transcript(utterance,' '.join(accepted),{
            'accepted_segments':len(accepted),'rejected_segments':rejected,
            'no_speech':not accepted,'partial_rejection':rejected>0,
            'mean_logprob':sum(x['avg_logprob'] for x in qualities)/len(qualities) if qualities else None,
            'max_no_speech_prob':max((x['no_speech_prob'] for x in qualities),default=None),
            'vad_speech_seconds':float(info.duration_after_vad)},(time.monotonic()-start)*1000,queue_ms,time.monotonic())
