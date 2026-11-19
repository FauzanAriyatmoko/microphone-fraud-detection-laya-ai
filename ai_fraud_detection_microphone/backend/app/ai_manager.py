"""Ordered worker pipeline: capture → segment → STT → Laya → audit/outbox."""
import logging
import math
import queue
import threading
import time
import uuid
from collections import deque
from backend.internal.utils.audio import MicrophoneSource,ReplaySource,Segmenter,device_info
from backend.internal.utils.health import HostHealth,queue_health
from backend.internal.utils.logger import AuditLogger,public_text,timestamp
from backend.internal.client.mqtt_client import MQTTPublisher,LocalPublisher

LOG=logging.getLogger('manager')

class AIManager:
    def __init__(self,cfg,*,replay=None,dry_run=False,stt=None,engine=None,publisher=None,duration=None):
        self.cfg=cfg;self.replay=replay;self.dry_run=dry_run;self.stt=stt;self.engine=engine
        self.publisher=publisher;self.duration=duration
        runtime=cfg['runtime'];mic=cfg['microphone']
        self.frames=queue.Queue(maxsize=max(1,math.ceil(runtime['audio_queue_seconds']/mic['block_duration'])))
        self.utterances=queue.Queue(maxsize=runtime['utterance_queue_size'])
        self.texts=queue.Queue(maxsize=runtime['text_queue_size'])
        self.stop_source=threading.Event();self.abort=threading.Event()
        self.done={key:threading.Event() for key in ['source','segment','stt','laya']}
        self.failures=queue.Queue();self.threads=[];self.started=time.monotonic()
        self.session=str(uuid.uuid4());self.last_utterance_end=None;self.history=deque(maxlen=runtime['context_utterances'])
        self.reset_requested=threading.Event();self.stats_lock=threading.Lock();self.health=HostHealth();self.active_inference={'stt':None,'laya':None}
        self.counters={'utterances':0,'transcripts':0,'decisions':0,'audio_gaps':0,'stt_errors':0,'laya_errors':0,
                       'asr_ms':0.,'laya_ms':0.,'queue_ms':0.,'end_to_decision_ms':None}
        self.audit=AuditLogger(cfg['storage']['log_dir'],cfg['storage']['retention_days'],
                               decision_csv_enabled=cfg['storage']['decision_csv_enabled'])
        source_type=ReplaySource if replay else MicrophoneSource
        args=(replay,mic,self.frames,self.stop_source) if replay else (mic,self.frames,self.stop_source)
        self.source=source_type(*args)

    def request_stop(self):self.stop_source.set()
    def reset_session(self):self.reset_requested.set()

    def event(self,kind,**fields):
        result={'schema_version':'1.0','event_id':str(uuid.uuid4()),'timestamp':timestamp(),'kind':kind,
                'counter_id':self.cfg['app']['counter_id'],'session_id':self.session,'utterance_id':None,
                'model_version':self.cfg['model_version'],'policy_version':self.cfg['policy_version']}
        result.update(fields)
        return result

    def _increment(self,**fields):
        with self.stats_lock:
            for k,v in fields.items():self.counters[k]+=v

    def _put(self,destination,item):
        while not self.abort.is_set():
            try:destination.put(item,timeout=.1);return True
            except queue.Full:pass
        return False

    def _items(self,source,upstream):
        while not self.abort.is_set():
            try:yield source.get(timeout=.1)
            except queue.Empty:
                if self.done[upstream].is_set():return

    def _guard(self,name,target):
        try:target()
        except Exception as exc:
            LOG.error('worker_failed worker=%s type=%s',name,type(exc).__name__)
            self.failures.put((name,type(exc).__name__));self.abort.set();self.stop_source.set()
        finally:self.done[name].set()

    def _segment(self):
        segmenter=Segmenter(self.cfg['microphone'])
        for frame in self._items(self.frames,'source'):
            self.source.rms=float((frame.samples**2).mean()**.5)
            for utterance in segmenter.feed(frame):
                self._increment(utterances=1,audio_gaps=int(utterance.gap))
                self._put(self.utterances,utterance)
        for utterance in segmenter.flush():
            self._increment(utterances=1,audio_gaps=int(utterance.gap));self._put(self.utterances,utterance)

    def _speech(self):
        for utterance in self._items(self.utterances,'segment'):
            # A broken ASR worker fails the daemon; it must not silently label missing speech normal.
            try:
                self.active_inference['stt']=time.monotonic()
                transcript=self.stt.transcribe(utterance)
            except Exception:self._increment(stt_errors=1);raise
            finally:self.active_inference['stt']=None
            if self.abort.is_set():return
            self._put(self.texts,transcript)

    def _new_session(self):
        self.session=str(uuid.uuid4());self.history.clear();self.last_utterance_end=None

    def _decide(self):
        runtime=self.cfg['runtime'];mode=self.cfg['storage']['transcript_mode']
        for transcript in self._items(self.texts,'stt'):
            utterance=transcript.utterance
            if self.reset_requested.is_set() or utterance.gap or (self.last_utterance_end is not None and utterance.started_at-self.last_utterance_end>runtime['session_idle_seconds']):
                self._new_session();self.reset_requested.clear()
            self.last_utterance_end=utterance.ended_at
            text=transcript.text
            common={'utterance_id':utterance.utterance_id,'speaker':'unknown','captured_at':timestamp(utterance.started_at),
                    'speech_duration_sec':len(utterance.samples)/utterance.sample_rate,'audio_gap':utterance.gap,
                    'continuation_id':utterance.continuation_id,'endpoint_reason':utterance.endpoint_reason,
                    'last_speech_at':timestamp(utterance.last_speech_at) if utterance.last_speech_at else None}
            event=self.event('transcription',**common,text=public_text(text,mode),quality=transcript.quality,
                             latency_ms={'asr':transcript.asr_ms,'queue':transcript.queue_ms})
            self.audit.write('transcripts',event);self.publisher.publish('transcription',event)
            self._increment(transcripts=1)
            poor=utterance.gap or transcript.quality.get('partial_rejection') or not text.strip()
            evidence=[utterance.utterance_id]
            started=time.monotonic()
            laya_queue_ms=max(0,(started-transcript.queued_at)*1000) if transcript.queued_at else 0.
            if poor:
                result={'level':'TIDAK_DAPAT_DINILAI' if self.cfg['model']['laya']['effective_policy'].get('decision_mode')=='binary' else 'PERLU_DICEK',
                        'is_fraud':None,'decision_mode':self.cfg['model']['laya']['effective_policy'].get('decision_mode','tiered'),
                        'reason':'kualitas_audio_atau_transkrip_tidak_lengkap','mode':'shadow','production_ready':False}
                self.history.clear()
            else:
                while self.history and utterance.ended_at-self.history[0][0]>runtime['context_window_seconds']:self.history.popleft()
                self.history.append((utterance.ended_at,utterance.utterance_id,text))
                context='\n'.join(item[2] for item in self.history)
                evidence=[item[1] for item in self.history]
                try:
                    self.active_inference['laya']=time.monotonic()
                    result=self.engine.predict(context)
                except Exception:
                    self._increment(laya_errors=1);raise
                finally:self.active_inference['laya']=None
                if result['reason']=='konteks_melebihi_batas_token':self.history.clear()
            if self.abort.is_set():return
            inference_ms=(time.monotonic()-started)*1000
            elapsed=None if self.replay else max(0,(time.time()-(utterance.last_speech_at or utterance.ended_at))*1000)
            action={'ESKALASI':'Minta supervisor memverifikasi melalui prosedur resmi rumah sakit.',
                    'PERLU_DICEK':'Periksa konteks dan verifikasi melalui petugas berwenang.',
                    'TIDAK_ADA_INDIKASI':'Lanjutkan layanan sesuai prosedur rumah sakit.',
                    'FRAUD':'Model mendeteksi indikasi fraud; verifikasi melalui prosedur rumah sakit.',
                    'TIDAK_FRAUD':'Model tidak mendeteksi fraud berdasarkan threshold yang digunakan.',
                    'TIDAK_DAPAT_DINILAI':'Ulangi rekaman atau periksa kualitas audio/konteks.'}[result['level']]
            if result.get('decision_mode')!='binary' and result.get('context_status')=='laporan_kejadian':action='Catat pengaduan dan teruskan kepada supervisor untuk ditindaklanjuti.'
            elif result.get('decision_mode')!='binary' and result.get('context_status')=='konteks_kurang':action='Minta klarifikasi tujuan pembayaran atau permintaan sebelum mengambil tindakan.'
            event=self.event('decision',**common,transcript=public_text(text,mode),evidence_utterance_ids=evidence,
                **result,action_recommendation=action,latency_ms={'asr':transcript.asr_ms,'laya':inference_ms,'queue':transcript.queue_ms,'laya_queue':laya_queue_ms,
                    'endpointing':max(0,(utterance.ended_at-(utterance.last_speech_at or utterance.ended_at))*1000),'end_to_decision':elapsed})
            self.audit.write('fraud_incidents' if result['level'] not in ['TIDAK_ADA_INDIKASI','TIDAK_FRAUD'] else 'events',event)
            if result['level'] not in ['TIDAK_ADA_INDIKASI','TIDAK_FRAUD']:self.publisher.publish('fraud_alert',event)
            elif self.dry_run:self.publisher.publish('decision',event)
            LOG.info('decision utterance_id=%s level=%s reason=%s',utterance.utterance_id,result['level'],result['reason'])
            with self.stats_lock:
                self.counters.update(asr_ms=transcript.asr_ms,laya_ms=inference_ms,queue_ms=transcript.queue_ms,end_to_decision_ms=elapsed)
                self.counters['decisions']+=1

    def telemetry(self):
        with self.stats_lock:counts=dict(self.counters)
        depths={'audio':queue_health(self.frames,'queued_at'),'utterance':queue_health(self.utterances,'queued_at'),
                'text':queue_health(self.texts,'queued_at')}
        event=self.event('telemetry',uptime_seconds=time.monotonic()-self.started,queues=depths,
             counters=counts,gpu=self.health.snapshot(),audio={'device':getattr(self.source,'device',None),'rms':self.source.rms,'dropped_frames':self.source.dropped_frames,'overflows':self.source.overflows},
             mqtt=self.publisher.stats(),workers={t.name:t.is_alive() for t in self.threads})
        self.audit.write('events',event);self.publisher.publish('telemetry',event)
        return event

    def run(self):
        publisher_started=False
        try:
            if not self.replay:device_info(self.cfg['microphone'])
            if self.stt is None:
                from backend.internal.ai_runtime.background_process.speech_to_text import SpeechToText
                LOG.info('loading_whisper');self.stt=SpeechToText(self.cfg['model']['whisper'])
            if self.engine is None:
                from backend.internal.ai_runtime.background_process.laya_ai_process import LayaEngine
                cfg=self.cfg['model']['laya'];LOG.info('loading_laya')
                self.engine=LayaEngine(cfg['checkpoint_dir'],cfg['device'],cfg['decision_policy_file'],cfg['calibration_file'],policy=cfg['effective_policy'])
            if self.stop_source.is_set():return
            if self.publisher is None:
                self.publisher=LocalPublisher(echo=self.dry_run) if self.dry_run or not self.cfg['MQTT']['enabled'] else MQTTPublisher(self.cfg['MQTT'],self.event)
            self.publisher.start();publisher_started=True
            LOG.info('output_mode mqtt=%s decision_csv=%s log_dir=%s',
                     not (self.dry_run or not self.cfg['MQTT']['enabled']),
                     self.cfg['storage']['decision_csv_enabled'],self.cfg['storage']['log_dir'])
            for name,target in [('laya',self._decide),('stt',self._speech),('segment',self._segment),('source',self.source.run)]:
                thread=threading.Thread(target=self._guard,args=(name,target),name=name,daemon=True)
                thread.start();self.threads.append(thread)
            next_telemetry=0.;shutdown_started=None;ready=False;ready_at=None
            while not self.done['laya'].wait(.1):
                if self.abort.is_set():break
                if self.publisher.error:raise RuntimeError('MQTT worker failed')
                now=time.monotonic()
                for worker,started in list(self.active_inference.items()):
                    if started is not None and now-started>self.cfg['runtime']['inference_timeout']:
                        raise TimeoutError(f'{worker} inference exceeded timeout')
                if not ready and self.source.ready.is_set():
                    ready=True;ready_at=now;LOG.info('pipeline_ready source=%s','replay' if self.replay else 'microphone')
                if ready and self.duration and now-ready_at>=self.duration:self.request_stop()
                if ready:
                    healthy=(self.publisher.stats().get('connected') or self.dry_run or not self.cfg['MQTT']['enabled'])
                    healthy=healthy and not self.source.dropped_frames and not self.source.overflows and not self.publisher.stats().get('dropped',0)
                    self.publisher.set_status('online' if healthy else 'degraded')
                if now>=next_telemetry:self.telemetry();next_telemetry=now+self.cfg['runtime']['telemetry_interval']
                if self.stop_source.is_set():
                    if shutdown_started is None:shutdown_started=now
                    elif now-shutdown_started>self.cfg['runtime']['shutdown_timeout']:raise TimeoutError('Pipeline drain timed out')
            if not self.failures.empty():raise RuntimeError('Pipeline worker failed: '+str(self.failures.get()))
            if self.publisher.error:raise RuntimeError('MQTT worker failed')
            self.telemetry()
        finally:
            self.stop_source.set();self.abort.set()
            deadline=time.monotonic()+2
            for thread in self.threads:thread.join(timeout=max(0,deadline-time.monotonic()))
            if publisher_started:self.publisher.close()
            LOG.info('pipeline_stopped')
