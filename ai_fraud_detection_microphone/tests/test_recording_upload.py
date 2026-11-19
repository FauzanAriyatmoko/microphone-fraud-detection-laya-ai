import csv
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import soundfile as sf

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.internal.utils.recording import prepare_recording,MAX_UPLOAD_BYTES
from backend.internal.delivery.rest_handler import DashboardController


def wav_bytes():
    data=io.BytesIO();sf.write(data,np.full((4800,2),.2,dtype=np.float32),48000,format='WAV');return data.getvalue()

class ConversionTests(unittest.TestCase):
    def test_stereo_resampled_to_pcm_mono_and_original_removed(self):
        data=wav_bytes()
        with tempfile.TemporaryDirectory() as directory:
            result=prepare_recording(io.BytesIO(data),len(data),directory)
            info=sf.info(result['path'])
            self.assertEqual(info.samplerate,16000);self.assertEqual(info.channels,1)
            self.assertAlmostEqual(result['duration_seconds'],.1,places=2)
            self.assertFalse((Path(directory)/'input.audio').exists())
    def test_compressed_mp3(self):
        import av
        buffer=io.BytesIO()
        with av.open(buffer,mode='w',format='mp3') as container:
            stream=container.add_stream('libmp3lame',rate=48000)
            frame=av.AudioFrame.from_ndarray(np.full((2,4800),.2,dtype=np.float32),format='fltp',layout='stereo')
            frame.sample_rate=48000
            for packet in stream.encode(frame):container.mux(packet)
            for packet in stream.encode(None):container.mux(packet)
        data=buffer.getvalue()
        with tempfile.TemporaryDirectory() as directory:
            result=prepare_recording(io.BytesIO(data),len(data),directory)
            self.assertGreater(result['duration_seconds'],0)
            self.assertEqual(sf.info(result['path']).samplerate,16000)

    def test_bad_audio_and_truncated_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            for data,size in [(b'invalid',7),(b'x',10),(b'',0)]:
                with self.assertRaises(ValueError):prepare_recording(io.BytesIO(data),size,directory)
    def test_size_and_duration_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):prepare_recording(io.BytesIO(),MAX_UPLOAD_BYTES+1,directory)
            data=wav_bytes()
            with patch('backend.internal.utils.recording.MAX_AUDIO_SECONDS',.01):
                with self.assertRaisesRegex(ValueError,'Durasi'):prepare_recording(io.BytesIO(data),len(data),directory)

class UploadLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.controller=DashboardController(ROOT/'config/config.yaml',ROOT/'config/microphone.json')
        self.addCleanup(self.controller.close)
    def upload(self,name='rekaman.wav'):
        data=wav_bytes();return self.controller.upload(io.BytesIO(data),len(data),name)
    def test_opaque_id_replacement_and_cleanup(self):
        first=self.upload('../../rekaman.wav');path=Path(self.controller.uploaded['path'])
        self.assertEqual(first['name'],'rekaman.wav');self.assertNotIn('path',first)
        second=self.upload();self.assertNotEqual(second['upload_id'],first['upload_id']);self.assertFalse(path.exists())
        final=Path(self.controller.uploaded['path']);self.controller.close();self.assertFalse(final.exists())
    def test_bad_upload_keeps_previous_recording(self):
        first=self.upload();path=Path(self.controller.uploaded['path'])
        with self.assertRaises(ValueError):self.controller.upload(io.BytesIO(b'bad'),3,'bad.wav')
        self.assertEqual(self.controller.uploaded['upload_id'],first['upload_id']);self.assertTrue(path.exists())
    def test_busy_upload_and_invalid_id_rejected(self):
        self.controller.state='running'
        with self.assertRaises(RuntimeError):self.upload()
        self.controller.state='idle'
        with self.assertRaises(ValueError):self.controller.start({'source':'upload','upload_id':'/etc/passwd'})
    def test_upload_start_uses_internal_path_until_end_of_file(self):
        upload=self.upload()
        with patch('backend.internal.delivery.rest_handler.subprocess.Popen') as process,patch('backend.internal.delivery.rest_handler.threading.Thread'):
            self.controller.start({'source':'upload','upload_id':upload['upload_id']})
            command=process.call_args.args[0]
            self.assertIn('--replay',command);self.assertNotIn('--duration',command)
            self.assertIn(self.controller.uploaded['path'],command)
            self.assertEqual(self.controller.run_options['recording_name'],upload['name'])
        self.controller.process=None;self.controller.monitor=None;self.controller.state='stopped'

if __name__=='__main__':unittest.main()
