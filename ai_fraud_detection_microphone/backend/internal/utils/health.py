"""Optional host telemetry; missing GPU utilities never fail the pipeline."""
import subprocess
import time

class HostHealth:
    def __init__(self):
        self.last_read=0.;self.cached={'available':False}

    def snapshot(self):
        if time.monotonic()-self.last_read<10:return self.cached
        self.last_read=time.monotonic()
        try:
            result=subprocess.run(['nvidia-smi','--query-gpu=index,name,temperature.gpu,memory.used,memory.total,utilization.gpu',
                '--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=1,check=True)
            devices=[]
            for row in result.stdout.splitlines():
                index,name,temp,used,total,util=[v.strip() for v in row.split(',')]
                devices.append({'index':int(index),'name':name,'temperature_c':float(temp),'memory_used_mib':float(used),
                    'memory_total_mib':float(total),'utilization_percent':float(util)})
            self.cached={'available':True,'devices':devices}
        except (OSError,subprocess.SubprocessError,ValueError):self.cached={'available':False}
        return self.cached


def queue_health(items, timestamp_field):
    # Queue's own mutex protects inspection from concurrent producers/consumers.
    with items.mutex:
        count=len(items.queue)
        oldest=getattr(items.queue[0],timestamp_field,None) if count else None
    return {'depth':count,'oldest_age_seconds':max(0,time.monotonic()-oldest) if oldest is not None else 0.}
