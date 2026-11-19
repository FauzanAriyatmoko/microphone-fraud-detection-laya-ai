"""Real loopback broker tests: ACK, retained status, outages and recovery."""
import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.internal.client.mqtt_client import MQTTPublisher
from backend.internal.utils.config_loader import load_config

BROKER_PYTHON=Path(os.environ.get('LAYA_TEST_BROKER_PYTHON',ROOT.parent/'.env_laya_broker/bin/python'))

def wait_until(fn,timeout=15):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if fn():return
        time.sleep(.05)
    raise AssertionError('Timed out waiting for MQTT condition')

@unittest.skipUnless(BROKER_PYTHON.exists(),'Install isolated tests/broker/requirements.txt first')
class MQTTIntegrationTests(unittest.TestCase):
    def setUp(self):
        import paho.mqtt.client as mqtt
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));self.port=sock.getsockname()[1]
        self.broker=None;self.addCleanup(self.stop_broker)
        self.messages=queue.Queue();self.sub=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.sub.on_connect=lambda c,u,f,r,p:c.subscribe('laya-test/#',qos=1)
        self.sub.on_message=lambda c,u,m:self.messages.put((m.topic,json.loads(m.payload),m.retain))
        self.addCleanup(self.sub.disconnect);self.addCleanup(self.sub.loop_stop)
        self.config=load_config()['MQTT'];self.config.update(host='127.0.0.1',port=self.port,username=None,password=None,
           base_topic='laya-test',client_id='test-'+uuid.uuid4().hex,outbox_path=self.temp.name+'/outbox.db',drain_timeout=.5)
        self.factory=lambda kind,**fields:dict(event_id=uuid.uuid4().hex,kind=kind,session_id='session',**fields)
        self.publisher=None;self.addCleanup(self.stop_publisher)

    def start_broker(self):
        self.broker=subprocess.Popen([str(BROKER_PYTHON),str(ROOT/'tests/broker/server.py'),str(self.port)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        def listening():
            try:
                with socket.create_connection(('127.0.0.1',self.port),timeout=.1):return True
            except OSError:return False
        wait_until(listening)

    def stop_broker(self):
        if self.broker and self.broker.poll() is None:
            self.broker.terminate()
            try:self.broker.wait(timeout=5)
            except subprocess.TimeoutExpired:self.broker.kill();self.broker.wait()

    def stop_publisher(self):
        if self.publisher:
            self.publisher.close(timeout=.5);self.publisher=None

    def subscribe(self):
        self.sub.connect_async('127.0.0.1',self.port);self.sub.loop_start()
        wait_until(self.sub.is_connected)
        time.sleep(.15)

    def received(self,event_id):
        found=False
        while True:
            try:topic,event,retained=self.messages.get_nowait()
            except queue.Empty:return found
            if event['event_id']==event_id:found=True

    def test_offline_restart_ack_and_reconnect(self):
        self.publisher=MQTTPublisher(self.config,self.factory);self.publisher.start()
        event=self.factory('fraud_alert',level='PERLU_DICEK')
        self.publisher.publish('fraud_alert',event)
        self.assertEqual(self.publisher.stats()['queued'],1)
        self.stop_publisher()  # Process restart with a persistent SQLite queue.
        self.start_broker();self.subscribe()
        self.publisher=MQTTPublisher(self.config,self.factory);self.publisher.start()
        wait_until(lambda:self.publisher.stats()['queued']==0)
        wait_until(lambda:self.received(event['event_id']))
        self.stop_broker()
        wait_until(lambda:not self.publisher.connected.is_set())
        event2=self.factory('transcription',text='[TRANSCRIPT_OMITTED]')
        self.publisher.publish('transcription',event2)
        self.assertEqual(self.publisher.stats()['queued'],1)
        self.start_broker()
        wait_until(lambda:self.publisher.stats()['queued']==0,timeout=25)
        self.assertIsNone(self.publisher.error)

    def test_retained_offline_on_clean_shutdown(self):
        self.start_broker();self.subscribe()
        self.publisher=MQTTPublisher(self.config,self.factory);self.publisher.start()
        wait_until(self.publisher.connected.is_set)
        self.stop_publisher()
        def got_offline():
            while True:
                try:topic,event,retained=self.messages.get_nowait()
                except queue.Empty:return False
                if topic=='laya-test/status' and event.get('status')=='offline':return True
        wait_until(got_offline)

if __name__=='__main__':unittest.main()
