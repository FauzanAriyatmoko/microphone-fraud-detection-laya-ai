"""Loopback-only broker fixture; run with the isolated broker virtualenv."""
import asyncio
import logging
import signal
import sys
from amqtt.broker import Broker

async def main():
    stop=asyncio.Event();loop=asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM,stop.set)
    loop.add_signal_handler(signal.SIGINT,stop.set)
    broker=Broker({'listeners':{'default':{'type':'tcp','bind':'127.0.0.1:'+sys.argv[1]}},
        'plugins':{'amqtt.plugins.authentication.AnonymousAuthPlugin':{'allow_anonymous':True}}})
    await broker.start()
    await stop.wait()
    await broker.shutdown()

if __name__=='__main__':
    logging.basicConfig(level=logging.ERROR)
    asyncio.run(main())
