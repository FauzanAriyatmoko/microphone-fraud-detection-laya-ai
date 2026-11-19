"""Explicit verification subscriber. Uses config/env credentials; never prints them."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.internal.utils.config_loader import load_config

def main():
    import paho.mqtt.client as mqtt
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path);args=p.parse_args()
    cfg=load_config(args.config)['MQTT']
    client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id=cfg['client_id']+'-observer')
    if cfg.get('username'):client.username_pw_set(cfg['username'],cfg.get('password'))
    if cfg['use_tls']:
        client.tls_set(ca_certs=cfg.get('ca_certs'),certfile=cfg.get('certfile'),keyfile=cfg.get('keyfile'))
        client.tls_insecure_set(cfg['tls_insecure'])
    def connected(c,u,f,r,properties):
        if r.is_failure:raise RuntimeError('Broker refused subscriber')
        c.subscribe(cfg['base_topic'].rstrip('/')+'/#',qos=1)
    def message(c,u,m):
        data=json.loads(m.payload)
        missing={'schema_version','event_id','timestamp','counter_id'}-set(data)
        print(json.dumps({'topic':m.topic,'missing':sorted(missing),'event':data},ensure_ascii=False),flush=True)
    client.on_connect=connected;client.on_message=message
    client.connect(cfg['host'],cfg['port'],cfg['keep_alive'])
    try:client.loop_forever()
    except KeyboardInterrupt:client.disconnect()

if __name__=='__main__':main()
