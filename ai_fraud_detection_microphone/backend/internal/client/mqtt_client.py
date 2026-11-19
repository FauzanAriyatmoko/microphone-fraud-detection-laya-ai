"""Durable bounded outbox and a single network worker; delete only after broker ACK."""
import json
import logging
import os
import random
import sqlite3
import threading
import time
from pathlib import Path

LOG=logging.getLogger('mqtt')

class Outbox:
    def __init__(self,path,max_rows=10000,max_bytes=16777216):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if self.path.is_symlink():raise ValueError('Outbox must not be a symlink')
        self.db=sqlite3.connect(self.path,check_same_thread=False,timeout=5)
        os.chmod(self.path,0o600)
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE, topic TEXT, payload TEXT, session_id TEXT, priority INTEGER, expires REAL, created REAL, bytes INTEGER)')
        self.db.commit();self.lock=threading.Lock();self.max_rows=max_rows;self.max_bytes=max_bytes
        self.dropped=0;self.expired=0

    def enqueue(self,topic,event,priority=0,ttl=300):
        payload=json.dumps(event,ensure_ascii=False,allow_nan=False);size=len(payload.encode())+len(topic.encode())
        with self.lock,self.db:
            if self.db.execute('SELECT 1 FROM events WHERE event_id=?',(event['event_id'],)).fetchone():return True
            self._expire()
            rows,total=self.db.execute('SELECT COUNT(*),COALESCE(SUM(bytes),0) FROM events').fetchone()
            # Quota rejection is explicit and visible in telemetry; disk never grows without bound.
            if rows>=self.max_rows or total+size>self.max_bytes:
                self.dropped+=1;return False
            self.db.execute('INSERT INTO events(event_id,topic,payload,session_id,priority,expires,created,bytes) VALUES(?,?,?,?,?,?,?,?)',
              (event['event_id'],topic,payload,event.get('session_id') or event['event_id'],priority,time.time()+ttl,time.time(),size))
        return True

    def _expire(self):
        self.expired+=self.db.execute('DELETE FROM events WHERE expires<?',(time.time(),)).rowcount

    def next(self):
        with self.lock,self.db:
            self._expire()
            # Alert priority across sessions; chronological ordering within each session.
            return self.db.execute('''SELECT e.seq,e.topic,e.payload,e.event_id FROM events e
              WHERE NOT EXISTS(SELECT 1 FROM events older WHERE older.session_id=e.session_id AND older.seq<e.seq)
              ORDER BY e.priority DESC,e.seq LIMIT 1''').fetchone()

    def ack(self,sequence):
        with self.lock,self.db:self.db.execute('DELETE FROM events WHERE seq=?',(sequence,))

    def stats(self):
        with self.lock:
            count,size,oldest=self.db.execute('SELECT COUNT(*),COALESCE(SUM(bytes),0),MIN(created) FROM events').fetchone()
        return {'queued':count,'payload_bytes':size,'oldest_age_seconds':max(0,time.time()-oldest) if oldest else 0,
                'dropped':self.dropped,'expired':self.expired}

    def close(self):
        with self.lock:self.db.close()

class MQTTPublisher:
    def __init__(self,config,event_factory):
        import paho.mqtt.client as mqtt
        self.config=config;self.make_event=event_factory
        self.outbox=Outbox(config['outbox_path'],config['outbox_max_rows'],config['outbox_max_bytes'])
        self.stop_event=threading.Event();self.connected=threading.Event();self.error=None
        self.client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id=config['client_id'],clean_session=config['clean_session'])
        self.client.connect_timeout=2.0
        self.client.max_inflight_messages_set(1);self.client.max_queued_messages_set(4)
        if config.get('username'):self.client.username_pw_set(config['username'],config.get('password'))
        if config['use_tls']:
            self.client.tls_set(ca_certs=config.get('ca_certs'),certfile=config.get('certfile'),keyfile=config.get('keyfile'))
            self.client.tls_insecure_set(config['tls_insecure'])
        self.client.will_set(self.topic('status'),json.dumps(self.make_event('status',status='offline',reason='connection_lost')),qos=1,retain=True)
        self.client.on_connect=self._connected;self.client.on_disconnect=lambda *args:self.connected.clear()
        self.thread=threading.Thread(target=self._run,name='mqtt',daemon=True)
        self.published=0;self.last_ack_ms=None;self.status='starting'

    def topic(self,suffix):return self.config['base_topic'].rstrip('/')+'/'+suffix
    def start(self):self.thread.start()
    def set_status(self,status):self.status=status

    def _connected(self,client,userdata,flags,reason,properties):
        if reason.is_failure:return
        self.connected.set()
        client.publish(self.topic('status'),json.dumps(self.make_event('status',status=self.status)),qos=1,retain=True)
        LOG.info('broker_connected')

    def publish(self,suffix,event):
        ttl=self.config['alert_ttl'] if suffix=='fraud_alert' else self.config['transcription_ttl']
        ok=self.outbox.enqueue(self.topic(suffix),event,10 if suffix=='fraud_alert' else 0,ttl)
        if not ok:LOG.error('outbox_quota_reached event_id=%s',event['event_id'])
        return ok

    def stats(self):
        return dict(self.outbox.stats(),connected=self.connected.is_set(),published=self.published,last_ack_ms=self.last_ack_ms)

    def _run(self):
        pending=None;attempt=0;connection_started=None;last_status=None
        try:
            while not self.stop_event.is_set():
                if connection_started is None:
                    try:
                        self.client.connect(self.config['host'],self.config['port'],self.config['keep_alive'])
                        connection_started=time.monotonic()
                    except (OSError,TimeoutError):
                        self.stop_event.wait(min(30,2**min(attempt,5))+random.uniform(0,.5));attempt+=1;continue
                rc=self.client.loop(timeout=.1)
                if rc!=0 or (not self.connected.is_set() and time.monotonic()-connection_started>5):
                    self.connected.clear();self.client.disconnect();connection_started=None
                    self.stop_event.wait(min(30,2**min(attempt,5))+random.uniform(0,.5));attempt+=1;continue
                if not self.connected.is_set():continue
                attempt=0
                if self.status!=last_status:
                    self.client.publish(self.topic('status'),json.dumps(self.make_event('status',status=self.status)),qos=1,retain=True)
                    last_status=self.status
                if pending:
                    row,info,started=pending
                    if info.is_published():
                        self.outbox.ack(row[0]);self.published+=1;self.last_ack_ms=(time.monotonic()-started)*1000;pending=None
                    # Keep pending across disconnect: Paho retransmits QoS 1/2 on reconnect.
                    elif time.monotonic()-started>self.config['ack_timeout']:
                        self.client.disconnect();self.connected.clear();connection_started=None
                        pending=(row,info,time.monotonic())
                    continue
                row=self.outbox.next()
                if row:
                    info=self.client.publish(row[1],row[2],qos=self.config['qos'],retain=False)
                    if info.rc==0:pending=(row,info,time.monotonic())
                else:self.stop_event.wait(.05)
            if self.connected.is_set():
                info=self.client.publish(self.topic('status'),json.dumps(self.make_event('status',status='offline',reason='shutdown')),qos=1,retain=True)
                until=time.monotonic()+1
                while not info.is_published() and time.monotonic()<until:self.client.loop(timeout=.1)
        except Exception as exc:
            self.error=type(exc).__name__;LOG.error('mqtt_worker_failed type=%s',self.error)
        finally:
            self.client.disconnect();self.connected.clear()

    def close(self,timeout=None):
        until=time.monotonic()+(self.config['drain_timeout'] if timeout is None else timeout)
        while self.connected.is_set() and self.outbox.stats()['queued'] and time.monotonic()<until:
            if not self.thread.is_alive():break
            time.sleep(.05)
        self.stop_event.set();self.thread.join(timeout=4)
        if self.thread.is_alive():raise RuntimeError('MQTT worker did not stop; outbox preserved')
        self.outbox.close()

class LocalPublisher:
    """Dry-run output uses the same sanitized event contracts without network traffic."""
    def __init__(self,echo=False):self.echo=echo;self.events=0;self.error=None
    def start(self):pass
    def set_status(self,status):pass
    def publish(self,suffix,event):
        self.events+=1
        if self.echo:print(json.dumps({'topic_suffix':suffix,'event':event},ensure_ascii=False,allow_nan=False),flush=True)
        return True
    def stats(self):return {'connected':False,'disabled':True,'events':self.events,'queued':0}
    def close(self,timeout=None):pass
