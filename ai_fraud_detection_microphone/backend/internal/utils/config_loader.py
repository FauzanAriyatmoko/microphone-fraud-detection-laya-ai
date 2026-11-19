"""Validated configuration; relative application paths are rooted at the project."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import yaml
from backend.internal.ai_runtime.ai_fraud_detection import resolve_policy

ROOT = Path(__file__).resolve().parents[3]
DEFAULTS = {
    'app': {'name':'hermina-fraud-detector','environment':'shadow','counter_id':'loket-01','log_level':'INFO'},
    'runtime': {'audio_queue_seconds':5.0,'utterance_queue_size':8,'text_queue_size':16,
                'telemetry_interval':15.0,'shutdown_timeout':25.0,'context_utterances':1,
                'context_window_seconds':30.0,'session_idle_seconds':30.0,'inference_timeout':120.0},
    'storage': {'log_dir':'./log','retention_days':30,'transcript_mode':'omit','decision_csv_enabled':True},
    'MQTT': {'enabled':True,'port':1883,'qos':1,'keep_alive':60,'clean_session':True,
             'use_tls':False,'tls_insecure':False,'outbox_path':'./log/outbox.sqlite3',
             'outbox_max_rows':10000,'outbox_max_bytes':16777216,'transcription_ttl':300,
             'alert_ttl':604800,'ack_timeout':5.0,'drain_timeout':5.0},
}

def _merge(base, changes):
    result=copy.deepcopy(base)
    for key,value in changes.items():
        result[key]=_merge(result.get(key,{}),value) if isinstance(value,dict) else value
    return result

def _positive(value, name, integer=False):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0 or (integer and not isinstance(value,int)):
        raise ValueError(f'{name} must be a positive {"integer" if integer else "number"}')

def _path(root, value):
    p=Path(value).expanduser()
    return str((root/p).resolve() if not p.is_absolute() else p.resolve())

def load_config(config_path=None, microphone_path=None, *, root=ROOT, verify_weights=False):
    path=Path(config_path or root/'config/config.yaml').resolve()
    raw=yaml.safe_load(path.read_text())
    if not isinstance(raw,dict): raise ValueError('Config must be a mapping')
    cfg=_merge(DEFAULTS,raw)
    mic_path=Path(microphone_path or root/'config/microphone.json').resolve()
    mic=json.loads(mic_path.read_text())
    mic.setdefault('device_name',None);mic.setdefault('host_api',None)
    mic.setdefault('continuation_overlap',0.2)
    for section in ['app','runtime','storage','MQTT','model']:
        if not isinstance(cfg.get(section),dict): raise ValueError(f'{section} must be a mapping')
    for key in ['name','counter_id','environment']:
        if not isinstance(cfg['app'].get(key),str) or not cfg['app'][key]: raise ValueError(f'app.{key} is required')
    if cfg['app']['log_level'] not in ['DEBUG','INFO','WARNING','ERROR','CRITICAL']: raise ValueError('Invalid log_level')
    for key in ['sample_rate','channels']:_positive(mic[key],key,True)
    for key in ['block_duration','silence_threshold','silence_timeout','min_speech_duration','max_speech_duration']:_positive(mic[key],key)
    for key in ['pre_buffer_duration','continuation_overlap']:
        if not isinstance(mic[key],(int,float)) or not math.isfinite(mic[key]) or mic[key]<0: raise ValueError(f'Invalid {key}')
    if mic['block_duration']>mic['max_speech_duration']:raise ValueError('Audio block exceeds max duration')
    if not mic['min_speech_duration']<=mic['max_speech_duration']: raise ValueError('min speech exceeds max speech')
    if mic['pre_buffer_duration']+mic['continuation_overlap']>=mic['max_speech_duration']: raise ValueError('Audio buffers exceed max duration')
    if int(mic['sample_rate']*mic['block_duration'])<1: raise ValueError('Audio block is empty')
    for key,value in cfg['runtime'].items():_positive(value,'runtime.'+key,key.endswith('_size') or key=='context_utterances')
    if cfg['runtime']['context_utterances']>3: raise ValueError('context_utterances must be <=3')
    st=cfg['storage']
    _positive(st['retention_days'],'retention_days',True)
    if st['transcript_mode'] not in ['omit','masked','raw']: raise ValueError('Invalid transcript_mode')
    if not isinstance(st['decision_csv_enabled'],bool):raise ValueError('storage.decision_csv_enabled must be boolean')
    st['log_dir']=_path(root,st['log_dir'])
    mq=cfg['MQTT']
    for field,env in [('host','LAYA_MQTT_HOST'),('username','LAYA_MQTT_USERNAME'),('password','LAYA_MQTT_PASSWORD'),('client_id','LAYA_MQTT_CLIENT_ID')]:
        if env in os.environ:mq[field]=os.environ[env]
    if 'LAYA_MQTT_PORT' in os.environ:mq['port']=int(os.environ['LAYA_MQTT_PORT'])
    for key in ['enabled','use_tls','tls_insecure','clean_session']:
        if not isinstance(mq[key],bool):raise ValueError(f'MQTT.{key} must be boolean')
    for key in ['port','keep_alive','outbox_max_rows','outbox_max_bytes','transcription_ttl','alert_ttl']:_positive(mq[key],'MQTT.'+key,True)
    for key in ['ack_timeout','drain_timeout']:_positive(mq[key],'MQTT.'+key)
    if mq['port']>65535 or isinstance(mq['qos'],bool) or mq['qos'] not in [1,2]:raise ValueError('MQTT requires port <=65535 and QoS 1 or 2')
    if mq['enabled']:
        for key in ['host','client_id','base_topic']:
            if not isinstance(mq.get(key),str) or not mq[key]:raise ValueError(f'MQTT.{key} is required')
        if any(c in mq['base_topic'] for c in '#+\x00'):raise ValueError('Invalid MQTT base_topic')
    mq['outbox_path']=_path(root,mq['outbox_path'])
    for key in ['ca_certs','certfile','keyfile']:
        if mq.get(key):
            mq[key]=_path(root,mq[key])
            if not Path(mq[key]).is_file():raise ValueError(f'Missing TLS {key}')
    laya=cfg['model']['laya']
    laya.setdefault('device','cuda')
    for key in ['checkpoint_dir','decision_policy_file','calibration_file']:
        laya[key]=_path(root,laya[key])
    bundle=Path(laya['checkpoint_dir'])
    for filename in ['model.safetensors','questions.json','rl_agent_config.json','model_manifest.json','encoder/config.json','tokenizer/tokenizer.json']:
        if not (bundle/filename).is_file():raise ValueError(f'Model bundle missing {filename}')
    manifest=json.loads((bundle/'model_manifest.json').read_text())
    schema_hash=hashlib.sha256((bundle/'questions.json').read_bytes()).hexdigest()
    if schema_hash!=manifest['questions_sha256']:raise ValueError('Model questions hash mismatch')
    if verify_weights:
        with (bundle/'model.safetensors').open('rb') as f: actual=hashlib.file_digest(f,'sha256').hexdigest()
        if actual!=manifest['weights_sha256']:raise ValueError('Model weights hash mismatch')
    policy=json.loads(Path(laya['decision_policy_file']).read_text())
    policy=resolve_policy(policy,laya)
    laya['effective_policy']=policy
    calibration=json.loads(Path(laya['calibration_file']).read_text())
    if not isinstance(calibration.get('temperature'),list):raise ValueError('Calibration temperature missing')
    whisper=cfg['model']['whisper']
    _positive(whisper['beam_size'],'beam_size',True)
    for key in ['model_size','device','compute_type','language']:
        if not isinstance(whisper.get(key),str) or not whisper[key]:raise ValueError(f'whisper.{key} required')
    cfg['microphone']=mic
    cfg['model_version']=manifest['model_version']
    cfg['policy_version']=hashlib.sha256(json.dumps(policy,sort_keys=True,allow_nan=False).encode()).hexdigest()[:16]
    return cfg
