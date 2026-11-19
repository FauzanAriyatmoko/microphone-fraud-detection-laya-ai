"""Local output integration and CSV fidelity without a network/model dependency."""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.internal.utils.config_loader import load_config
from backend.internal.utils.logger import AuditLogger
from backend.internal.ai_runtime.background_process.speech_to_text import Transcript
from backend.app.ai_manager import AIManager

class CSVTests(unittest.TestCase):
    def event(self,**changes):
        event={'kind':'decision','event_id':'one','timestamp':'2026-11-19T10:00:00+07:00',
            'transcript':'Ibu, "bayar di kasir"\nterima kasih','level':'TIDAK_ADA_INDIKASI',
            'evidence_utterance_ids':['id-1','id-2'],'fraud_score':.15,
            'latency_ms':{'asr':123.,'end_to_decision':None}}
        event.update(changes);return event

    def test_unicode_multiline_append_after_restart_and_missing_score(self):
        with tempfile.TemporaryDirectory() as directory:
            event=self.event()
            AuditLogger(directory,decision_csv_enabled=True).write('events',event)
            AuditLogger(directory,decision_csv_enabled=True).write('fraud_incidents',self.event(event_id='two',fraud_score=None,level='PERLU_DICEK'))
            file=next(Path(directory).glob('*/*.csv'))
            with file.open(newline='',encoding='utf-8') as handle:rows=list(csv.DictReader(handle))
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0]['transcript'],event['transcript'])
            self.assertEqual(json.loads(rows[0]['evidence_utterance_ids']),['id-1','id-2'])
            self.assertEqual(rows[0]['asr_ms'],'123.0')
            self.assertEqual(rows[1]['fraud_score'],'')
            self.assertEqual(file.stat().st_mode&0o777,0o600)

    def test_non_decisions_and_disabled_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            AuditLogger(directory).write('events',self.event())
            AuditLogger(directory,decision_csv_enabled=True).write('transcripts',{'kind':'transcription'})
            self.assertFalse(list(Path(directory).glob('*/*.csv')))
            self.assertTrue(list(Path(directory).glob('*/*.jsonl')))

    def test_formula_cells_are_escaped(self):
        with tempfile.TemporaryDirectory() as directory:
            AuditLogger(directory,decision_csv_enabled=True).write('events',self.event(transcript='=1+1'))
            with next(Path(directory).glob('*/*.csv')).open(newline='') as file:row=next(csv.DictReader(file))
            self.assertEqual(row['transcript'],"'=1+1")

    def test_retention_cleans_generated_csv_only(self):
        with tempfile.TemporaryDirectory() as directory:
            logger=AuditLogger(directory,1,True)
            folder=Path(directory)/'2000-01-01';folder.mkdir()
            generated=folder/'decisions_20000101.csv';generated.write_text('old')
            keep=folder/'my_results.csv';keep.write_text('keep')
            logger.cleanup()
            self.assertFalse(generated.exists());self.assertTrue(keep.exists())

class LocalPipelineTests(unittest.TestCase):
    def test_local_only_creates_csv_without_constructing_mqtt(self):
        import soundfile as sf
        class STT:
            def transcribe(self,utterance):
                return Transcript(utterance,'Pembayaran di kasir resmi, ya Bu.',{},1.,0.)
        class Engine:
            def predict(self,text):
                return {'level':'TIDAK_ADA_INDIKASI','reason':'layanan_normal','fraud_score':.1,'context_status':'layanan_normal','mode':'shadow'}
        with tempfile.TemporaryDirectory() as directory:
            cfg=load_config();cfg['MQTT']['enabled']=False
            cfg['storage'].update(log_dir=directory+'/log',decision_csv_enabled=True,transcript_mode='raw')
            cfg['MQTT']['outbox_path']=directory+'/outbox.sqlite3'
            audio=Path(directory)/'test.wav'
            sf.write(audio,np.concatenate([np.full(16000,.1),np.zeros(16000)]),16000)
            cfg['microphone']['sample_rate']=16000
            with patch('backend.app.ai_manager.MQTTPublisher') as mqtt:
                manager=AIManager(cfg,replay=audio,stt=STT(),engine=Engine())
                manager.run();mqtt.assert_not_called()
            self.assertFalse(Path(cfg['MQTT']['outbox_path']).exists())
            with next(Path(directory).glob('log/*/*.csv')).open(newline='') as file:rows=list(csv.DictReader(file))
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['level'],'TIDAK_ADA_INDIKASI')
            self.assertEqual(rows[0]['transcript'],'Pembayaran di kasir resmi, ya Bu.')

    def test_csv_config_requires_boolean(self):
        import yaml
        with tempfile.TemporaryDirectory() as directory:
            cfg=yaml.safe_load((ROOT/'config/config.yaml').read_text())
            cfg['storage']['decision_csv_enabled']='yes'
            path=Path(directory)/'config.yaml';path.write_text(yaml.safe_dump(cfg))
            with self.assertRaises(ValueError):load_config(path)

if __name__=='__main__':unittest.main()
