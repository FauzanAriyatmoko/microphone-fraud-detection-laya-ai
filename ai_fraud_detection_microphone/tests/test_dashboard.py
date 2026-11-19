"""HTTP controller isolation, process lifecycle and direct entrypoint checks."""
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.internal.delivery.rest_handler import DashboardController,make_handler

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.controller=DashboardController(ROOT/'config/config.yaml',ROOT/'config/microphone.json')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),lambda *a:None)
        self.port=self.server.server_port
        self.server.RequestHandlerClass=make_handler(self.controller,'test-token',self.port)
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start()
        self.addCleanup(self.close)
    def close(self):
        self.server.shutdown();self.server.server_close();self.worker.join();self.controller.close()
    def request(self,path,body=None,**headers):
        request=Request(f'http://127.0.0.1:{self.port}'+path,data=None if body is None else json.dumps(body).encode(),
            headers={'X-Laya-Token':'test-token','Content-Type':'application/json',**headers})
        return urlopen(request,timeout=5)
    def test_html_and_alias_use_one_dashboard(self):
        with self.request('/dashboard.html') as r:html=r.read().decode()
        self.assertIn('content="test-token"',html)
        self.assertRegex(html,r"token\s*===\s*'__LAYA_TOKEN__'")
        self.assertIn('Mulai pengujian',html)
        with self.request('/tests/testing_fraud_detection.html') as r:self.assertEqual(r.read().decode(),html)
    def test_api_rejects_missing_token_cross_origin_and_invalid_host(self):
        for headers in [{'X-Laya-Token':''},{'Origin':'https://untrusted.invalid'},{'Host':'untrusted.invalid'}]:
            with self.assertRaises(HTTPError) as exc:self.request('/api/status',**headers)
            self.assertEqual(exc.exception.code,403)
    def test_config_does_not_expose_credentials(self):
        with self.request('/api/config') as response:data=json.load(response)
        self.assertNotIn('password',data);self.assertNotIn('username',data)
        self.assertFalse(data['mqtt_enabled_for_dashboard'])
    def test_invalid_start_does_not_create_process(self):
        with patch('backend.internal.delivery.rest_handler.subprocess.Popen') as popen:
            with self.assertRaises(HTTPError) as exc:self.request('/api/start',{'source':'invalid'})
            self.assertEqual(exc.exception.code,400);popen.assert_not_called()
    def test_csv_export_uses_same_encoding_fields_and_escaping(self):
        with self.controller.lock:
            self.controller.decisions.append({'event_id':'id','kind':'decision','transcript':'=1+1','level':'PERLU_DICEK','latency_ms':{'asr':1.2}})
        with self.request('/api/export.csv') as response:
            self.assertIn('attachment',response.headers['Content-Disposition'])
            rows=list(csv.DictReader(io.StringIO(response.read().decode('utf-8-sig'))))
        self.assertEqual(rows[0]['transcript'],"'=1+1");self.assertEqual(rows[0]['asr_ms'],'1.2')
    def test_upload_http_validation_and_status(self):
        import numpy as np
        import soundfile as sf
        audio=io.BytesIO();sf.write(audio,np.full(1600,.2),16000,format='WAV')
        req=Request(f'http://127.0.0.1:{self.port}/api/upload',data=audio.getvalue(),
            headers={'X-Laya-Token':'test-token','X-File-Name':'test.wav','Content-Type':'application/octet-stream'})
        with urlopen(req,timeout=5) as response:data=json.load(response)
        self.assertEqual(data['name'],'test.wav');self.assertNotIn('path',data)
        with self.request('/api/status') as response:self.assertEqual(json.load(response)['upload']['upload_id'],data['upload_id'])
        invalid=Request(f'http://127.0.0.1:{self.port}/api/upload',data=b'bad',headers={'X-Laya-Token':'test-token'})
        with self.assertRaises(HTTPError) as exc:urlopen(invalid,timeout=5)
        self.assertEqual(exc.exception.code,400)
        self.assertNotIn('/tmp/',json.loads(exc.exception.read())['error'])

    def test_event_cursor(self):
        with self.controller.lock:
            self.controller.events.extend([{'sequence':1,'event':{}},{'sequence':2,'event':{}}])
        with self.request('/api/status?after=1') as response:data=json.load(response)
        self.assertEqual(data['cursor'],2);self.assertEqual(len(data['events']),1)

class LifecycleTests(unittest.TestCase):
    def controller(self):return DashboardController(ROOT/'config/config.yaml',ROOT/'config/microphone.json')
    def test_start_uses_pipeline_launcher_and_blocks_duplicate_runs(self):
        c=self.controller()
        with patch('backend.internal.delivery.rest_handler.subprocess.Popen') as popen,patch('backend.internal.delivery.rest_handler.threading.Thread'):
            c.start({'source':'sample','duration':10,'transcript_mode':'raw'})
            command=popen.call_args.args[0]
            self.assertIn('--dry-run-output',command);self.assertIn('--replay',command);self.assertIn('raw',command)
            with self.assertRaises(RuntimeError):c.start({'source':'sample'})
    def test_monitor_handles_all_decisions_and_stage(self):
        c=self.controller();c.state='starting'
        event={'event':{'kind':'decision','utterance_id':'id','level':'TIDAK_ADA_INDIKASI'}}
        class Process:
            stdout=io.StringIO(json.dumps(event)+'\n')
            stderr=io.StringIO(json.dumps({'message':'pipeline_ready source=replay','level':'INFO'})+'\n')
            def wait(self):return 0
        c._monitor(Process())
        self.assertEqual(c.state,'stopped');self.assertEqual(c.counts['decision'],1)
        self.assertEqual(len(c.decisions),1)
    def test_stop_during_startup_is_clean(self):
        c=self.controller();c.state='stopping'
        class Process:
            stdout=io.StringIO('');stderr=io.StringIO('')
            def wait(self):return -15
        c._monitor(Process());self.assertEqual(c.state,'stopped')
    def test_dry_run_emits_normal_decisions_for_dashboard(self):
        import soundfile as sf
        import numpy as np
        from backend.app.ai_manager import AIManager
        from backend.internal.utils.config_loader import load_config
        from backend.internal.ai_runtime.background_process.speech_to_text import Transcript
        class STT:
            def transcribe(self,u):return Transcript(u,'Bayar di kasir',{},1.,0.)
        class Engine:
            def predict(self,text):return {'level':'TIDAK_ADA_INDIKASI','reason':'layanan_normal'}
        class Publisher:
            error=None
            def __init__(self):self.records=[]
            def start(self):pass
            def set_status(self,state):pass
            def publish(self,topic,event):self.records.append((topic,event))
            def stats(self):return {'connected':False}
            def close(self):pass
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'audio.wav';sf.write(path,np.full(16000,.1),16000)
            cfg=load_config();cfg['microphone']['sample_rate']=16000;cfg['storage']['log_dir']=directory+'/log'
            publisher=Publisher()
            AIManager(cfg,replay=path,dry_run=True,stt=STT(),engine=Engine(),publisher=publisher).run()
            decisions=[event for topic,event in publisher.records if topic=='decision']
            self.assertEqual(len(decisions),1)
            self.assertEqual(decisions[0]['level'],'TIDAK_ADA_INDIKASI')

    def test_direct_script_works_from_another_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            process=subprocess.run([sys.executable,str(ROOT/'backend/app/main.py'),'--check-config'],cwd=directory,capture_output=True,text=True,timeout=20)
        self.assertEqual(process.returncode,0,process.stderr)
        self.assertTrue(json.loads(process.stdout)['valid'])

if __name__=='__main__':unittest.main()
