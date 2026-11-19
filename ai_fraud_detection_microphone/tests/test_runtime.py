import copy
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.internal.utils.config_loader import load_config
from backend.internal.utils.logger import AuditLogger,public_text
from backend.internal.utils.audio import Frame,Segmenter,mono_float,resample
from backend.internal.client.mqtt_client import Outbox,LocalPublisher
from backend.internal.ai_runtime.background_process.speech_to_text import Transcript
from backend.app.ai_manager import AIManager

class ConfigTests(unittest.TestCase):
    def test_environment_and_root_resolution(self):
        with patch.dict(os.environ,{'LAYA_MQTT_HOST':'localhost','LAYA_MQTT_PASSWORD':'test-only'}):
            cfg=load_config()
        self.assertEqual(cfg['MQTT']['host'],'localhost')
        self.assertTrue(Path(cfg['model']['laya']['checkpoint_dir']).is_absolute())
    def test_invalid_queue(self):
        import yaml
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad.yaml';cfg=yaml.safe_load((ROOT/'config/config.yaml').read_text())
            cfg['runtime']['audio_queue_seconds']=0;p.write_text(yaml.safe_dump(cfg))
            with self.assertRaises(ValueError):load_config(p)
    def test_invalid_threshold_rejected(self):
        import yaml
        with tempfile.TemporaryDirectory() as d:
            cfg=yaml.safe_load((ROOT/'config/config.yaml').read_text());policy=Path(d)/'policy.json'
            policy.write_text(json.dumps({'threshold_review':.8,'threshold_escalate':.2}))
            cfg['model']['laya']['decision_policy_file']=str(policy)
            p=Path(d)/'cfg.yaml';p.write_text(yaml.safe_dump(cfg))
            with self.assertRaises(ValueError):load_config(p)

class AudioTests(unittest.TestCase):
    def setUp(self):self.cfg=load_config()['microphone'];self.cfg['sample_rate']=1000
    def frame(self,i,value=.1,n=100):return Frame(i,np.full(n,value,dtype=np.float32),(i+1)/10)
    def test_pcm_and_resample(self):
        self.assertAlmostEqual(mono_float(np.array([-32768,0,32767],dtype=np.int16))[0],-1)
        self.assertEqual(list(mono_float(np.array([0,128],dtype=np.uint8))),[-1,0])
        self.assertEqual(len(resample(np.zeros(48000),48000)),16000)
    def test_silence_is_empty(self):
        s=Segmenter(self.cfg)
        self.assertEqual(sum((s.feed(self.frame(i,0)) for i in range(30)),[]),[])
        self.assertEqual(s.flush(),[])
    def test_prebuffer_and_endpoint(self):
        s=Segmenter(self.cfg);out=[]
        for i in range(4):out+=s.feed(self.frame(i,0))
        for i in range(4,10):out+=s.feed(self.frame(i))
        for i in range(10,16):out+=s.feed(self.frame(i,0))
        self.assertEqual(len(out),1);self.assertEqual(len(out[0].samples),1600)
        self.assertAlmostEqual(out[0].started_at,0)
    def test_max_duration_counts_interword_silence(self):
        self.cfg['max_speech_duration']=1.2
        s=Segmenter(self.cfg);out=[]
        for i in range(30):out+=s.feed(self.frame(i,.1 if i%3!=2 else 0))
        self.assertGreaterEqual(len(out),2)
        self.assertTrue(all(len(u.samples)<=1200 for u in out))
        self.assertEqual(out[0].continuation_id,out[1].continuation_id)
    def test_drop_resets_partial_utterance(self):
        s=Segmenter(self.cfg)
        for i in range(3):s.feed(self.frame(i))
        out=[]
        for i in range(5,11):out+=s.feed(self.frame(i))
        out+=s.flush();self.assertTrue(out[0].gap)
        self.assertEqual(len(out[0].samples),600)
    def test_short_noise_ignored(self):
        s=Segmenter(self.cfg);s.feed(self.frame(0))
        self.assertEqual(s.flush(),[])

class AuditTests(unittest.TestCase):
    def test_redaction_and_permissions(self):
        self.assertEqual(public_text('diagnosis confidential','omit'),'[TRANSCRIPT_OMITTED]')
        self.assertNotIn('123456',public_text('OTP: 123456','masked'))
        with tempfile.TemporaryDirectory() as d:
            a=AuditLogger(d);a.write('transcripts',{'text':public_text('raw secret','omit')})
            p=next(Path(d).glob('*/*.jsonl'))
            self.assertEqual(p.stat().st_mode&0o777,0o600)
            self.assertNotIn('raw secret',p.read_text())
    def test_retention_does_not_follow_links_or_delete_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);outside=root/'external';outside.mkdir();(outside/'keep').write_text('x')
            logs=root/'logs';a=AuditLogger(logs,1)
            (logs/'2000-01-01').symlink_to(outside,target_is_directory=True)
            old=logs/'2000-01-02';old.mkdir();(old/'events_20000102.jsonl').write_text('{}')
            (old/'unrelated.txt').write_text('keep');a.cleanup()
            self.assertTrue((outside/'keep').exists());self.assertTrue((old/'unrelated.txt').exists())
            self.assertFalse((old/'events_20000102.jsonl').exists())

class OutboxTests(unittest.TestCase):
    def event(self,i,session='one'):return {'event_id':str(i),'session_id':session}
    def test_persistent_order_ack_and_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'outbox.db';o=Outbox(path)
            o.enqueue('transcription',self.event(1));o.enqueue('fraud_alert',self.event(2),priority=10)
            o.enqueue('fraud_alert',self.event(3,'other'),priority=10)
            o.enqueue('transcription',self.event(1));o.close();o=Outbox(path)
            self.assertEqual(o.stats()['queued'],3)
            row=o.next();self.assertEqual(row[3],'3');o.ack(row[0])
            row=o.next();self.assertEqual(row[3],'1');o.ack(row[0])
            self.assertEqual(o.next()[3],'2');o.close()
    def test_quota_and_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            o=Outbox(Path(d)/'out.db',max_rows=1)
            self.assertTrue(o.enqueue('x',self.event(1),ttl=-1))
            self.assertIsNone(o.next())
            self.assertTrue(o.enqueue('x',self.event(2)))
            self.assertFalse(o.enqueue('x',self.event(3)))
            self.assertEqual(o.stats()['dropped'],1);o.close()

class FakeSTT:
    def __init__(self,bad=False):self.bad=bad
    def transcribe(self,u):
        return Transcript(u,'jangan bayar ke rekening pribadi petugas',{'partial_rejection':self.bad},1.,0.)
class FakeEngine:
    def __init__(self):self.inputs=[]
    def predict(self,text):
        self.inputs.append(text)
        return {'level':'PERLU_DICEK','reason':'test_policy','mode':'shadow','production_ready':False}
class RecordingPublisher(LocalPublisher):
    def __init__(self):super().__init__();self.records=[]
    def publish(self,suffix,event):self.records.append((suffix,event));return True

class PipelineTests(unittest.TestCase):
    def run_pipeline(self,bad=False):
        import soundfile as sf
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        cfg=load_config();cfg['storage']['log_dir']=temp.name+'/log';cfg['MQTT']['enabled']=False
        cfg['microphone']['sample_rate']=16000
        wav=Path(temp.name)/'input.wav'
        sf.write(wav,np.concatenate([np.full(16000,.1),np.zeros(16000)]),16000)
        engine=FakeEngine();publisher=RecordingPublisher()
        manager=AIManager(cfg,replay=wav,stt=FakeSTT(bad),engine=engine,publisher=publisher)
        manager.run();return manager,engine,publisher
    def test_replay_drains_and_preserves_evidence(self):
        manager,engine,pub=self.run_pipeline()
        events=[e for topic,e in pub.records if topic in ['transcription','fraud_alert']]
        self.assertEqual(len(events),2)
        self.assertEqual(events[0]['utterance_id'],events[1]['evidence_utterance_ids'][0])
        self.assertEqual(events[0]['session_id'],events[1]['session_id'])
        self.assertEqual(len(engine.inputs),1)
        self.assertTrue(all(not t.is_alive() for t in manager.threads))
        self.assertEqual(events[0]['text'],'[TRANSCRIPT_OMITTED]')
        self.assertIn('action_recommendation',events[1])
        telemetry=next(e for topic,e in pub.records if topic=='telemetry')
        self.assertIn('gpu',telemetry)
        self.assertIn('oldest_age_seconds',telemetry['queues']['audio'])
    def test_bad_asr_skips_model_and_routes_to_review(self):
        manager,engine,pub=self.run_pipeline(True)
        self.assertEqual(engine.inputs,[])
        event=next(e for topic,e in pub.records if topic=='fraud_alert')
        self.assertEqual(event['level'],'PERLU_DICEK')
    def test_worker_failure_returns_without_hanging(self):
        class Broken:
            def transcribe(self,u):raise RuntimeError('failure')
        import soundfile as sf
        with tempfile.TemporaryDirectory() as d:
            wav=Path(d)/'audio.wav';sf.write(wav,np.full(16000,.1),16000)
            cfg=load_config();cfg['storage']['log_dir']=d+'/log'
            manager=AIManager(cfg,replay=wav,stt=Broken(),engine=FakeEngine(),publisher=RecordingPublisher())
            start=time.monotonic()
            with self.assertRaises(RuntimeError):manager.run()
            self.assertLess(time.monotonic()-start,5)

    def test_inference_watchdog(self):
        class Slow:
            def transcribe(self,u):
                time.sleep(.5)
                return FakeSTT().transcribe(u)
        import soundfile as sf
        with tempfile.TemporaryDirectory() as d:
            wav=Path(d)/'audio.wav';sf.write(wav,np.full(16000,.1),16000)
            cfg=load_config();cfg['storage']['log_dir']=d+'/log';cfg['runtime']['inference_timeout']=.1
            manager=AIManager(cfg,replay=wav,stt=Slow(),engine=FakeEngine(),publisher=RecordingPublisher())
            with self.assertRaises(TimeoutError):manager.run()
            self.assertTrue(all(not t.is_alive() for t in manager.threads))

    def test_stop_drains_workers(self):
        cfg=load_config()
        with tempfile.TemporaryDirectory() as d:
            cfg['storage']['log_dir']=d+'/log'
            manager=AIManager(cfg,stt=FakeSTT(),engine=FakeEngine(),publisher=RecordingPublisher())
            class Source:
                rms=0.;overflows=0;dropped_frames=0;ready=threading.Event()
                def run(self):
                    self.ready.set();manager.stop_source.wait()
            manager.source=Source()
            with patch('backend.app.ai_manager.device_info',return_value=(0,{})):
                stopper=threading.Timer(.2,manager.request_stop);stopper.start()
                try:manager.run()
                finally:stopper.cancel()
            self.assertTrue(all(not t.is_alive() for t in manager.threads))

if __name__=='__main__':unittest.main()
