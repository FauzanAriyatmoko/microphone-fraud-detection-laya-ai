"""Structured journal output and dated audit files. No implicit raw transcript logging."""
import csv
import json
import logging
import os
import re
import threading
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ZONE=ZoneInfo('Asia/Jakarta')

def timestamp(epoch=None):
    return (datetime.now(ZONE) if epoch is None else datetime.fromtimestamp(epoch,ZONE)).isoformat(timespec='milliseconds')

class JSONFormatter(logging.Formatter):
    def format(self,record):
        return json.dumps({'timestamp':timestamp(record.created),'level':record.levelname,'component':record.name,
                           'message':record.getMessage()},ensure_ascii=False)

def configure_logging(level='INFO'):
    handler=logging.StreamHandler();handler.setFormatter(JSONFormatter())
    logging.basicConfig(level=level,handlers=[handler],force=True)
    for noisy in ['httpx','httpx2','httpcore','huggingface_hub','faster_whisper']:logging.getLogger(noisy).setLevel(logging.WARNING)

def public_text(text, mode):
    if mode=='omit':return '[TRANSCRIPT_OMITTED]'
    if mode=='raw':return text
    # Best-effort numeric/contact masking only; not a medical/name anonymizer.
    text=re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b','[EMAIL]',text)
    text=re.sub(r'\b\d(?:[\d .+-]*\d)?\b','[NUMBER]',text)
    text=re.sub(r'(?i)\b(otp|pin|password|sandi|cvv)\s*[:=]\s*\S+',r'\1 [SECRET]',text)
    return text

CSV_FIELDS = [
    'schema_version', 'event_id', 'timestamp', 'counter_id', 'session_id', 'utterance_id',
    'model_version', 'policy_version', 'captured_at', 'transcript', 'speech_duration_sec',
    'audio_gap', 'level', 'fraud_score', 'context_status', 'context_confidence', 'reason',
    'action_recommendation', 'mode', 'production_ready', 'evidence_utterance_ids',
    'asr_ms', 'laya_ms', 'queue_ms', 'laya_queue_ms', 'endpointing_ms', 'end_to_decision_ms',
]


def csv_value(value):
    # Prevent speech/config strings from becoming spreadsheet formulas on opening CSV.
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def decision_csv_row(event):
    row={key:event.get(key) for key in CSV_FIELDS}
    row['evidence_utterance_ids']=json.dumps(event.get('evidence_utterance_ids',[]),ensure_ascii=False)
    for key,value in event.get('latency_ms',{}).items():
        if key+'_ms' in CSV_FIELDS:row[key+'_ms']=value
    return {key:csv_value(value) for key,value in row.items()}


class AuditLogger:
    def __init__(self, directory, retention_days=30, decision_csv_enabled=False):
        self.root=Path(directory).resolve();self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.decision_csv_enabled=decision_csv_enabled
        self.retention=retention_days;self.lock=threading.Lock();self.last_cleanup=None

    def write(self,kind,event):
        if kind not in ['transcripts','fraud_incidents','events']:raise ValueError('Invalid audit kind')
        now=datetime.now(ZONE);day=now.strftime('%Y-%m-%d')
        with self.lock:
            folder=self.root/day
            if folder.is_symlink():raise ValueError('Audit day cannot be a symlink')
            folder.mkdir(exist_ok=True,mode=0o700)
            path=folder/f'{kind}_{now:%Y%m%d}.jsonl'
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'a',encoding='utf-8') as f:
                f.write(json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n')
                f.flush()
                if kind=='fraud_incidents':os.fsync(f.fileno())
            if self.decision_csv_enabled and event.get('kind')=='decision':
                self._write_decision_csv(folder, now, event)
            if self.last_cleanup!=day:
                self.cleanup(now);self.last_cleanup=day

    def _write_decision_csv(self, folder, now, event):
        path=folder/f'decisions_{now:%Y%m%d}.csv'
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'a',encoding='utf-8',newline='') as file:
            writer=csv.DictWriter(file,fieldnames=CSV_FIELDS)
            if os.fstat(file.fileno()).st_size==0:
                writer.writeheader()
            writer.writerow(decision_csv_row(event))
            file.flush()
            os.fsync(file.fileno())

    def cleanup(self,now=None):
        cutoff=(now or datetime.now(ZONE)).date()-timedelta(days=self.retention)
        for folder in self.root.iterdir():
            if folder.is_symlink() or not folder.is_dir():continue
            try:date=datetime.strptime(folder.name,'%Y-%m-%d').date()
            except ValueError:continue
            if date>=cutoff:continue
            for file in folder.iterdir():
                if not file.is_symlink() and re.fullmatch(r'(?:(transcripts|fraud_incidents|events)_\d{8}\.jsonl|decisions_\d{8}\.csv)',file.name):file.unlink()
            try:folder.rmdir()
            except OSError:pass  # Unknown files are never deleted by retention.
