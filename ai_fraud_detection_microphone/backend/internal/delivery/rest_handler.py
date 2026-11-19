"""Loopback-only dashboard API supervising the existing CLI pipeline process."""
import csv
import io
import json
import logging
import os
from pathlib import Path
import secrets
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse,parse_qs,unquote
from backend.internal.utils.config_loader import ROOT,load_config
from backend.internal.utils.logger import CSV_FIELDS,decision_csv_row
from backend.internal.utils.recording import MAX_UPLOAD_BYTES,MAX_AUDIO_SECONDS,prepare_recording

LOG=logging.getLogger('dashboard')

class DashboardController:
    def __init__(self,config,microphone):
        self.config=Path(config).resolve();self.microphone=Path(microphone).resolve()
        self.lock=threading.RLock();self.process=None;self.monitor=None
        self.state='idle';self.error=None;self.run_id=None;self.sequence=0
        self.events=deque(maxlen=2000);self.decisions=deque(maxlen=2000)
        self.counts={'transcription':0,'decision':0};self.telemetry=None
        self.started_at=None;self.transcript_mode=None;self.stage='Belum dimulai'
        self.closing=False;self.run_options=None
        self.uploaded=None;self.upload_dir=None;self.upload_processing=False

    def devices(self):
        import sounddevice as sd
        apis=sd.query_hostapis()
        return [{'index':i,'name':d['name'],'channels':d['max_input_channels'],
                 'sample_rate':int(d['default_samplerate']),'host_api':apis[d['hostapi']]['name']}
                for i,d in enumerate(sd.query_devices()) if d['max_input_channels']>0]

    def upload(self,stream,size,name):
        with self.lock:
            if self.closing or self.upload_processing or self.state in ['starting','running','stopping']:
                raise RuntimeError('Tunggu upload/pipeline selesai sebelum mengunggah rekaman baru')
            self.upload_processing=True
        directory=None
        try:
            directory=tempfile.TemporaryDirectory(prefix='laya-recording-')
            prepared=prepare_recording(stream,size,directory.name)
            with self.lock:
                if self.closing:raise RuntimeError('Dashboard sedang dihentikan')
                previous=self.upload_dir
                self.upload_dir=directory
                self.uploaded=dict(prepared,upload_id=uuid.uuid4().hex,name=Path(name.replace('\\','/')).name[:160] or 'rekaman')
                result={k:v for k,v in self.uploaded.items() if k!='path'}
                if previous:previous.cleanup()
                return result
        except Exception:
            if directory:directory.cleanup()
            raise
        finally:
            with self.lock:self.upload_processing=False

    def start(self,options):
        mode=options.get('source','microphone')
        if mode not in ['microphone','sample','upload']:raise ValueError('Sumber audio tidak valid')
        text_mode=options.get('transcript_mode','omit')
        if text_mode not in ['omit','masked','raw']:raise ValueError('Mode transkrip tidak valid')
        duration=options.get('duration',120) if mode=='microphone' else 120
        if type(duration) is not int or not 5<=duration<=3600:raise ValueError('Durasi harus 5–3600 detik')
        cfg=load_config(self.config,self.microphone)
        command=[str(ROOT/'scripts/run_standalone.sh'),'--config',str(self.config),'--microphone-config',str(self.microphone),
                 '--dry-run-output','--transcript-mode',text_mode]
        if mode=='sample':
            sample=ROOT.parent/'legacy/test_output_new.wav'
            if not sample.is_file():raise ValueError('Rekaman contoh legacy/test_output_new.wav tidak tersedia')
            command+=['--replay',str(sample)]
        elif mode=='upload':
            # Resolve opaque IDs inside the lifecycle lock below; never accept client paths.
            pass
        else:
            index=options.get('device_index')
            if type(index) is not int or index<0:raise ValueError('Pilih perangkat mikrofon')
            import sounddevice as sd
            mic=dict(cfg['microphone'],device_index=index,device_name=None)
            try:sd.check_input_settings(device=index,channels=mic['channels'],samplerate=mic['sample_rate'],dtype='float32')
            except Exception as exc:raise ValueError('Mikrofon tidak tersedia atau tidak mendukung sample rate/channels konfigurasi. Pilih perangkat lain atau periksa microphone.json.') from exc
            command+=['--device-index',str(index),'--duration',str(duration)]
        with self.lock:
            if self.closing:raise RuntimeError('Dashboard sedang dihentikan')
            if self.upload_processing:raise RuntimeError('Tunggu proses upload selesai')
            if mode=='upload':
                if not self.uploaded or options.get('upload_id')!=self.uploaded['upload_id']:
                    raise ValueError('Unggah rekaman terlebih dahulu atau pilih ulang file setelah server restart')
                command+=['--replay',self.uploaded['path']]
            if self.state in ['starting','running','stopping']:raise RuntimeError('Pipeline masih aktif; tunggu sampai berhenti')
            self.run_id=uuid.uuid4().hex;self.events.clear();self.decisions.clear();self.telemetry=None
            self.counts={'transcription':0,'decision':0};self.error=None;self.stage='Memuat model'
            self.transcript_mode=text_mode;self.started_at=time.time();self.state='starting'
            self.run_options={'source':mode,'device_index':options.get('device_index'),'duration':duration,'transcript_mode':text_mode,
                              'recording_name':self.uploaded['name'] if mode=='upload' else None}
            try:
                self.process=subprocess.Popen(command,cwd=str(ROOT),stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    text=True,bufsize=1,start_new_session=True)
            except Exception:
                self.state='error';self.error='Proses pipeline gagal dibuat';raise
            self.monitor=threading.Thread(target=self._monitor,args=(self.process,),daemon=True)
            self.monitor.start()
        return self.snapshot()

    def _monitor(self,process):
        stderr=threading.Thread(target=self._stderr,args=(process,),daemon=True);stderr.start()
        try:
            for line in process.stdout:
                try:data=json.loads(line)
                except (ValueError,TypeError):continue
                event=data.get('event')
                if not isinstance(event,dict):continue
                kind=event.get('kind')
                with self.lock:
                    self.sequence+=1;self.events.append({'sequence':self.sequence,'event':event})
                    if kind=='transcription':self.counts['transcription']+=1
                    if kind=='decision':self.counts['decision']+=1;self.decisions.append(event)
                    if kind=='telemetry':self.telemetry=event
            code=process.wait();stderr.join(timeout=2)
            with self.lock:
                clean=code==0 or (code==-signal.SIGTERM and self.state=='stopping')
                self.state='stopped' if clean else 'error'
                self.stage='Selesai' if clean else 'Pipeline gagal'
                if not clean and not self.error:self.error='Pipeline keluar dengan kode '+str(code)+'. Periksa perangkat dan log terminal.'
        finally:
            process.stdout.close();process.stderr.close()

    def _stderr(self,process):
        stages={'loading_whisper':'Memuat Whisper','loading_laya':'Memuat Laya','pipeline_ready':'Mendengarkan audio'}
        for line in process.stderr:
            try:record=json.loads(line)
            except ValueError:continue
            message=record.get('message','')
            with self.lock:
                for key,label in stages.items():
                    if message.startswith(key):
                        self.stage=label
                        if key=='pipeline_ready' and self.state=='starting':self.state='running'
                if record.get('level') in ['ERROR','CRITICAL']:self.error=message[:500]
            LOG.info('pipeline: %s',message[:500])

    def stop(self):
        with self.lock:
            process=self.process
            if process and process.poll() is None:
                self.state='stopping';self.stage='Menguras antrean dan menyimpan hasil'
                try:process.send_signal(signal.SIGTERM)
                except ProcessLookupError:pass
                threading.Thread(target=self._terminate_later,args=(process,),daemon=True).start()
        return self.snapshot()

    def _terminate_later(self,process):
        try:process.wait(timeout=38)
        except subprocess.TimeoutExpired:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass

    def snapshot(self,after=0):
        with self.lock:
            batch=[item for item in self.events if item['sequence']>after][:300]
            return {'state':self.state,'stage':self.stage,'error':self.error,'run_id':self.run_id,
                'started_at':self.started_at,'transcript_mode':self.transcript_mode,'options':self.run_options,'counts':dict(self.counts),
                'telemetry':self.telemetry,'events':batch,'cursor':batch[-1]['sequence'] if batch else after,
                'history_truncated':bool(self.events and after and after<self.events[0]['sequence']-1),
                'csv_rows_available':len(self.decisions),
                'upload':{k:v for k,v in self.uploaded.items() if k!='path'} if self.uploaded else None}

    def export(self):
        with self.lock:events=list(self.decisions)
        output=io.StringIO(newline='');writer=csv.DictWriter(output,fieldnames=CSV_FIELDS)
        writer.writeheader()
        for event in events:writer.writerow(decision_csv_row(event))
        return output.getvalue().encode('utf-8-sig')

    def close(self):
        with self.lock:self.closing=True
        self.stop()
        if self.monitor:self.monitor.join(timeout=40)
        with self.lock:
            if self.upload_dir:self.upload_dir.cleanup();self.upload_dir=None;self.uploaded=None


def make_handler(controller,token,port):
    hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
    origins={f'http://{host}' for host in hosts}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def respond(self,status,data,kind='application/json; charset=utf-8',download=False):
            payload=json.dumps(data,ensure_ascii=False).encode() if isinstance(data,(dict,list)) else data
            self.send_response(status);self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(payload)));self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            if download:self.send_header('Content-Disposition','attachment; filename="laya-testing.csv"')
            self.end_headers()
            try:self.wfile.write(payload)
            except (BrokenPipeError,ConnectionResetError):pass
        def allowed(self,api=False):
            if self.headers.get('Host') not in hosts:return False
            if self.headers.get('Origin') and self.headers['Origin'] not in origins:return False
            return not api or secrets.compare_digest(self.headers.get('X-Laya-Token',''),token)
        def do_GET(self):
            path=urlparse(self.path)
            if not self.allowed(path.path.startswith('/api/')):
                self.respond(403,{'error':'Akses hanya untuk dashboard lokal'});return
            try:
                if path.path in ['/','/dashboard.html','/tests/testing_fraud_detection.html']:
                    html=(ROOT/'dashboard.html').read_text().replace('__LAYA_TOKEN__',token,1)
                    self.respond(200,html.encode(),'text/html; charset=utf-8')
                elif path.path=='/api/status':
                    after=int(parse_qs(path.query).get('after',['0'])[0]);self.respond(200,controller.snapshot(after))
                elif path.path=='/api/devices':
                    try:self.respond(200,{'devices':controller.devices()})
                    except Exception:self.respond(503,{'error':'PortAudio/perangkat tidak tersedia. Periksa --list-devices.'})
                elif path.path=='/api/config':
                    cfg=load_config(controller.config,controller.microphone)
                    self.respond(200,{'model_version':cfg['model_version'],'sample_rate':cfg['microphone']['sample_rate'],
                        'device_index':cfg['microphone'].get('device_index'),'transcript_mode':cfg['storage']['transcript_mode'],
                        'log_dir':cfg['storage']['log_dir'],'decision_csv_enabled':cfg['storage']['decision_csv_enabled'],
                        'sample_available':(ROOT.parent/'legacy/test_output_new.wav').is_file(),'mqtt_enabled_for_dashboard':False,
                        'max_upload_bytes':MAX_UPLOAD_BYTES,'max_audio_seconds':MAX_AUDIO_SECONDS})
                elif path.path=='/api/export.csv':self.respond(200,controller.export(),'text/csv; charset=utf-8',download=True)
                else:self.respond(404,{'error':'Tidak ditemukan'})
            except (ValueError,TypeError):self.respond(400,{'error':'Parameter tidak valid'})
            except Exception:LOG.exception('dashboard_request_failed');self.respond(500,{'error':'Gagal membaca data dashboard'})
        def do_POST(self):
            if not self.allowed(True):self.respond(403,{'error':'Token atau origin tidak valid'});return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if self.path=='/api/upload':
                    if not 0<length<=MAX_UPLOAD_BYTES:
                        self.respond(413,{'error':'Ukuran rekaman harus 1 byte hingga 50 MB'});return
                    self.connection.settimeout(60)
                    name=unquote(self.headers.get('X-File-Name','rekaman'))
                    self.respond(200,controller.upload(self.rfile,length,name));return
                if not 0<=length<=4096:raise ValueError('Payload terlalu besar')
                data=json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(data,dict):raise ValueError('JSON harus berupa object')
                if self.path=='/api/start':self.respond(200,controller.start(data))
                elif self.path=='/api/stop':self.respond(200,controller.stop())
                else:self.respond(404,{'error':'Tidak ditemukan'})
            except (ValueError,TypeError) as exc:self.respond(400,{'error':str(exc)})
            except RuntimeError as exc:self.respond(409,{'error':str(exc)})
            except Exception:self.respond(500,{'error':'Gagal menjalankan pipeline. Periksa perangkat dan konfigurasi.'})
    return Handler


def serve_dashboard(config,microphone,port=8765):
    controller=DashboardController(config,microphone)
    server=ThreadingHTTPServer(('127.0.0.1',port),make_handler(controller,secrets.token_hex(32),port))
    server.daemon_threads=True
    previous=signal.getsignal(signal.SIGTERM)
    def terminate(signum,frame):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,terminate)
    print(f'Dashboard lokal: http://127.0.0.1:{port}/dashboard.html',flush=True)
    try:server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:pass
    finally:
        server.server_close();controller.close();signal.signal(signal.SIGTERM,previous)
