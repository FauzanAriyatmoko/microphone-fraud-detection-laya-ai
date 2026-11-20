"""Opt-in GPU integration: real Whisper/Laya and a loopback MQTT broker."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import yaml

ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(os.environ.get('LAYA_RUN_GPU_TESTS')=='1','Set LAYA_RUN_GPU_TESTS=1 for real model integration')
class GPUPipelineTests(unittest.TestCase):
    def test_audio_to_broker_ack(self):
        recording=Path(os.environ.get('LAYA_TEST_RECORDING',''))
        if not recording.is_file():self.skipTest('Set LAYA_TEST_RECORDING to a speech WAV for GPU replay')
        import paho.mqtt.client as mqtt
        broker_python=Path(os.environ.get('LAYA_TEST_BROKER_PYTHON',ROOT.parent/'.env_laya_broker/bin/python'))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        broker=subprocess.Popen([str(broker_python),str(ROOT/'tests/broker/server.py'),str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: broker.poll() is None and broker.terminate())
        deadline=time.monotonic()+10
        while True:
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.1):break
            except OSError:
                if time.monotonic()>deadline:self.fail('Broker startup timed out')
                time.sleep(.05)
        records=[];subscribed=[]
        client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.on_connect=lambda c,u,f,r,p:c.subscribe('laya-e2e/#',qos=1)
        client.on_subscribe=lambda *a:subscribed.append(True)
        client.on_message=lambda c,u,m:records.append({'topic':m.topic,'event':json.loads(m.payload)})
        client.connect('127.0.0.1',port);client.loop_start()
        try:
            deadline=time.monotonic()+5
            while not subscribed:
                if time.monotonic()>deadline:self.fail('Subscriber timed out')
                time.sleep(.05)
            with tempfile.TemporaryDirectory() as directory:
                cfg=yaml.safe_load((ROOT/'config/config.yaml').read_text())
                cfg['MQTT'].update(enabled=True,host='127.0.0.1',port=port,username='',password='',base_topic='laya-e2e',outbox_path=directory+'/outbox.db')
                cfg['storage'].update(log_dir=directory+'/audit',transcript_mode='omit')
                config=Path(directory)/'config.yaml';config.write_text(yaml.safe_dump(cfg))
                env=os.environ.copy()
                for field in ['HOST','PORT','USERNAME','PASSWORD','CLIENT_ID']:env.pop('LAYA_MQTT_'+field,None)
                result=subprocess.run([str(ROOT/'scripts/run_standalone.sh'),'--config',str(config),'--replay',str(recording.resolve())],
                      capture_output=True,text=True,timeout=90,env=env)
                self.assertEqual(result.returncode,0,result.stderr)
                time.sleep(.2)
                import sqlite3
                with sqlite3.connect(directory+'/outbox.db') as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
            for suffix in ['transcription','fraud_alert']:
                self.assertTrue(any(r['topic'].endswith('/'+suffix) for r in records))
            self.assertTrue(any(r['event'].get('status')=='offline' for r in records))
        finally:
            client.disconnect();client.loop_stop();broker.terminate();broker.wait(timeout=5)

if __name__=='__main__':unittest.main()
