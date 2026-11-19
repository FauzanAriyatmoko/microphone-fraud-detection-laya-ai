"""Headless service CLI. python -m backend.app.main --help"""
import argparse
import json
import logging
import math
import os
import signal
from pathlib import Path
from backend.internal.utils.config_loader import load_config,ROOT
from backend.internal.utils.logger import configure_logging


def main(argv=None):
    os.umask(0o077)
    parser=argparse.ArgumentParser(description='Laya hospital receptionist voice pipeline')
    parser.add_argument('--config',type=Path,default=ROOT/'config/config.yaml')
    parser.add_argument('--microphone-config',type=Path,default=ROOT/'config/microphone.json')
    parser.add_argument('--check-config',action='store_true')
    parser.add_argument('--check-device',action='store_true')
    parser.add_argument('--verify-weights',action='store_true')
    parser.add_argument('--list-devices',action='store_true')
    parser.add_argument('--replay',type=Path,help='Replay WAV through the same segmentation/STT/Laya workers')
    parser.add_argument('--dry-run-output',action='store_true',help='Print sanitized events; disable MQTT')
    parser.add_argument('--no-mqtt',action='store_true')
    parser.add_argument('--duration',type=float,help='Stop capture after N seconds of readiness')
    parser.add_argument('--sample-rate',type=int,help='Override capture sample rate supported by the device')
    parser.add_argument('--whisper-model',help='Whisper size or a local model directory')
    parser.add_argument('--whisper-device',choices=['cpu','cuda'])
    parser.add_argument('--whisper-compute-type',choices=['float16','float32','int8','int8_float16'])
    args=parser.parse_args(argv)
    configure_logging()
    try:
        if args.duration is not None and (not math.isfinite(args.duration) or args.duration<=0):raise ValueError('duration must be positive')
        if args.list_devices:
            import sounddevice as sd
            print(json.dumps({'devices':list(sd.query_devices()),'host_apis':list(sd.query_hostapis())},indent=2));return 0
        cfg=load_config(args.config,args.microphone_config,verify_weights=args.verify_weights)
        configure_logging(cfg['app']['log_level'])
        if args.no_mqtt:cfg['MQTT']['enabled']=False
        if args.sample_rate:
            if args.sample_rate<1:raise ValueError('sample_rate must be positive')
            cfg['microphone']['sample_rate']=args.sample_rate
        if args.replay:
            import soundfile as sf
            info=sf.info(args.replay)
            cfg['microphone']['sample_rate']=info.samplerate
        for arg,key in [(args.whisper_model,'model_size'),(args.whisper_device,'device'),(args.whisper_compute_type,'compute_type')]:
            if arg:cfg['model']['whisper'][key]=arg
        if args.check_config:
            print(json.dumps({'valid':True,'model_version':cfg['model_version'],'policy_version':cfg['policy_version'],
                'config':str(args.config.resolve()),'weights_verified':args.verify_weights},indent=2));return 0
        if args.check_device:
            from backend.internal.utils.audio import device_info
            try:index,info=device_info(cfg['microphone'])
            except Exception as exc:
                raise ValueError('Microphone selection failed; run --list-devices and update device_name/device_index and sample_rate') from exc
            print(json.dumps({'index':index,'device':info},indent=2));return 0
        from backend.app.ai_manager import AIManager
        manager=AIManager(cfg,replay=args.replay,dry_run=args.dry_run_output,duration=args.duration)
        previous={}
        for sig in [signal.SIGINT,signal.SIGTERM,signal.SIGUSR1]:
            previous[sig]=signal.getsignal(sig)
            signal.signal(sig,lambda signum,frame: manager.reset_session() if signum==signal.SIGUSR1 else manager.request_stop())
        try:manager.run()
        finally:
            for sig,handler in previous.items():signal.signal(sig,handler)
        return 0
    except Exception as exc:
        # No arbitrary exception text from model/network libraries in audit logs.
        logging.getLogger('main').error('startup_or_runtime_failed type=%s',type(exc).__name__)
        if isinstance(exc,(ValueError,FileNotFoundError)):logging.getLogger('main').error('%s',str(exc))
        return 1

if __name__=='__main__':raise SystemExit(main())
