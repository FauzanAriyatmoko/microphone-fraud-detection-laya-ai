"""Audio conversion, deterministic endpointing, capture and bounded replay sources."""
import math
import queue
import time
import threading
import uuid
from collections import deque
from dataclasses import dataclass,field
import numpy as np

@dataclass
class Frame:
    sequence: int
    samples: np.ndarray
    ended_at: float
    gap: bool=False
    queued_at: float=field(default_factory=time.monotonic)

@dataclass
class Utterance:
    utterance_id: str
    samples: np.ndarray
    sample_rate: int
    started_at: float
    ended_at: float
    queued_at: float
    gap: bool=False
    continuation_id: str | None=None
    endpoint_reason: str='silence'
    last_speech_at: float | None=None


def mono_float(samples):
    data=np.asarray(samples)
    if np.issubdtype(data.dtype,np.unsignedinteger):
        midpoint=(np.iinfo(data.dtype).max+1)/2
        data=(data.astype(np.float32)-midpoint)/midpoint
    elif np.issubdtype(data.dtype,np.signedinteger):
        scale=float(max(abs(np.iinfo(data.dtype).min),np.iinfo(data.dtype).max))
        data=data.astype(np.float32)/scale
    else:data=data.astype(np.float32)
    if data.ndim==2:data=data.mean(axis=1)
    if data.ndim!=1 or not np.isfinite(data).all():raise ValueError('Invalid audio samples')
    return np.clip(data,-1,1)


def resample(samples,source_rate,target_rate=16000):
    from scipy.signal import resample_poly
    data=mono_float(samples)
    if source_rate==target_rate:return data
    divisor=math.gcd(int(source_rate),int(target_rate))
    return resample_poly(data,target_rate//divisor,source_rate//divisor).astype(np.float32)


def device_info(cfg):
    import sounddevice as sd
    devices=sd.query_devices()
    if cfg.get('device_name'):
        matches=[i for i,d in enumerate(devices) if cfg['device_name'].casefold() in d['name'].casefold()
                 and d['max_input_channels']>=cfg['channels']
                 and (not cfg.get('host_api') or cfg['host_api'].casefold() in sd.query_hostapis(d['hostapi'])['name'].casefold())]
        if len(matches)!=1:raise ValueError(f'Microphone name matches {len(matches)} devices; select unique name/host_api')
        index=matches[0]
    else:index=cfg.get('device_index')
    info=sd.query_devices(index,'input')
    sd.check_input_settings(device=index,channels=cfg['channels'],samplerate=cfg['sample_rate'],dtype='float32')
    return index,dict(info)

class Segmenter:
    def __init__(self,cfg):
        self.cfg=cfg;self.rate=cfg['sample_rate']
        self.pre=deque(maxlen=math.ceil(cfg['pre_buffer_duration']/cfg['block_duration']))
        self.previous_sequence=None;self.pending_gap=False
        self.reset()

    def reset(self):
        self.buffer=[];self.count=0;self.voiced=0;self.silence=0;self.active=False
        self.started_at=None;self.ended_at=None;self.chain=None;self.last_speech_at=None

    def _emit(self,reason):
        audio=np.concatenate(self.buffer)
        utterance=Utterance(str(uuid.uuid4()),audio,self.rate,self.started_at,self.ended_at,
                            time.monotonic(),self.pending_gap,self.chain,reason,self.last_speech_at)
        self.pending_gap=False
        return utterance

    def feed(self,frame):
        events=[]
        if frame.gap or (self.previous_sequence is not None and frame.sequence!=self.previous_sequence+1):
            self.reset();self.pre.clear();self.pending_gap=True
        self.previous_sequence=frame.sequence
        data=mono_float(frame.samples);n=len(data)
        if n==0:return events
        loud=float(np.sqrt(np.mean(data**2)))>self.cfg['silence_threshold']
        if not self.active:
            if not loud:
                self.pre.append(data);return events
            self.active=True
            self.buffer=list(self.pre);self.pre.clear()
            self.count=sum(len(x) for x in self.buffer)
            self.started_at=frame.ended_at-(self.count+n)/self.rate
            self.chain=str(uuid.uuid4())
        # Enforce max duration including quiet gaps and prebuffer, at sample precision.
        offset=0;limit=int(self.cfg['max_speech_duration']*self.rate)
        while offset<n:
            take=min(n-offset,limit-self.count)
            part=data[offset:offset+take];self.buffer.append(part);self.count+=take
            self.voiced+=take if loud else 0
            self.silence=0 if loud else self.silence+take
            offset+=take;self.ended_at=frame.ended_at-(n-offset)/self.rate
            if loud:self.last_speech_at=self.ended_at
            if self.count>=limit:
                if self.voiced>=self.cfg['min_speech_duration']*self.rate:events.append(self._emit('max_duration'))
                chain=self.chain;end=self.ended_at
                overlap=int(self.cfg.get('continuation_overlap',.2)*self.rate)
                tail=np.concatenate(self.buffer)[-overlap:] if overlap else np.empty(0,dtype=np.float32)
                self.reset();self.active=True;self.chain=chain;self.buffer=[tail] if overlap else []
                self.count=len(tail);self.started_at=end-self.count/self.rate;self.ended_at=end
            if self.silence>=self.cfg['silence_timeout']*self.rate:
                if self.voiced>=self.cfg['min_speech_duration']*self.rate:events.append(self._emit('silence'))
                self.reset()
                if offset<n:self.pre.append(data[offset:])
                break
        return events

    def flush(self):
        result=[]
        if self.active and self.voiced>=self.cfg['min_speech_duration']*self.rate:result=[self._emit('end_of_stream')]
        self.reset();return result

class MicrophoneSource:
    def __init__(self,cfg,output,stop):
        self.cfg=cfg;self.output=output;self.stop=stop
        self.dropped_frames=0;self.overflows=0;self.rms=0.;self.sequence=0
        self.device=None;self.ready=threading.Event()

    def run(self):
        import sounddevice as sd
        index,self.device=device_info(self.cfg)
        def callback(data,frames,timing,status):
            gap=bool(status)
            if gap:self.overflows+=1
            frame=Frame(self.sequence,data.copy(),time.time(),gap)
            self.sequence+=1
            try:self.output.put_nowait(frame)
            except queue.Full:self.dropped_frames+=1
        with sd.InputStream(device=index,samplerate=self.cfg['sample_rate'],channels=self.cfg['channels'],
                            dtype='float32',blocksize=int(self.cfg['block_duration']*self.cfg['sample_rate']),callback=callback) as stream:
            self.ready.set()
            while not self.stop.wait(.1):
                if not stream.active:raise RuntimeError('Microphone stream stopped')

class ReplaySource:
    def __init__(self,path,cfg,output,stop):
        self.path=path;self.cfg=cfg;self.output=output;self.stop=stop
        self.dropped_frames=0;self.overflows=0;self.rms=0.;self.ready=threading.Event()

    def run(self):
        # Stream WAV blocks from disk; do not load an unbounded recording into RAM.
        import soundfile as sf
        with sf.SoundFile(self.path) as audio:
            self.ready.set()
            rate=audio.samplerate
            if rate!=self.cfg['sample_rate']:
                raise ValueError('Replay sample rate must match microphone config; use --sample-rate for this file')
            block=int(rate*self.cfg['block_duration']);origin=time.time();count=0
            for sequence,data in enumerate(audio.blocks(blocksize=block,dtype='float32',always_2d=True)):
                if self.stop.is_set():break
                count+=len(data);self.rms=float(np.sqrt(np.mean(data**2)))
                frame=Frame(sequence,data,origin+count/rate)
                while not self.stop.is_set():
                    try:self.output.put(frame,timeout=.1);break
                    except queue.Full:pass
