import zmq
from Hitomi import Hitomi
from setup_logger import setup
import asyncio
import aioconsole

zmq_context = zmq.Context()
zmq_socket = zmq_context.socket(zmq.REP)
zmq_socket.bind('tcp://127.0.0.1:37980')
logger = setup('hitomi_server')

proxy = {
    'http': 'http://127.0.0.1:10809',
    'https': 'http://127.0.0.1:10809'
}
hitomi = Hitomi(proxy_fmt=proxy)


async def zmq_server():
    try:
        while True:
            req = await asyncio.to_thread(zmq_socket.recv_json)
            if 'type' not in req:
                zmq_socket.send_json({'status': 'error'})
                continue
            response = {
                'status': 'success',
                'result': ''
            }
            if req['type'] == 'search':
                query_str = req['query_str']
                results = await asyncio.to_thread(hitomi.process_query(query_str))
                if results:
                    response['result'] = results[:10]
                else:
                    response['status'] = 'failed'
            elif req['type'] == 'download':
                gallery_id = req['gallery_id']
                filename = await asyncio.to_thread(hitomi.download(gallery_id))
                if filename:
                    response['result'] = filename
                else:
                    response['status'] = 'failed'
                    response['result'] = 'gallery not found'
            zmq_socket.send_json(response)
    except zmq.ZMQError as e:
        zmq_socket.close()
        zmq_context.term()
        logger.warning('shutdown')


async def shell():
    while True:
        usr_in = await aioconsole.ainput('Waiting Command')
        if usr_in == 'exit':
            zmq_socket.close()
            zmq_context.term()
            logger.warning('Server shutdown')
            break

loop = asyncio.get_event_loop()
loop.create_task(zmq_server())
loop.run_until_complete(shell())
logger.warning('Server App down')
