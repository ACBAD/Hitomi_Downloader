import asyncio
import json

import aioconsole
import zmq

from Hitomi import Hitomi
from setup_logger import setup

zmq_context = zmq.Context()
req_socket = zmq_context.socket(zmq.REP)
notify_socket = zmq_context.socket(zmq.PUB)

notify_socket.bind('tcp://127.0.0.1:37890')
req_socket.bind('tcp://127.0.0.1:37980')
logger = setup('hitomi_server')

proxy = {
    'http': 'http://127.0.0.1:10809',
    'https': 'http://127.0.0.1:10809'
}
hitomi = Hitomi(proxy_fmt=proxy)
queue = asyncio.Queue()


async def zmq_server():
    try:
        while True:
            req = await asyncio.to_thread(req_socket.recv_json)
            if 'type' not in req:
                req_socket.send_json({'status': 'error', 'result': 'Bad Request'})
                continue
            logger.warning(f'收到客户端请求:{req["type"]}')
            response = {
                'type': req['type'],
                'status': 'async'
            }
            if req['type'] == 'check_queue':
                response['result'] = queue.qsize()
                response['status'] = 'success'
            else:
                await queue.put(req)
            req_socket.send_json(response)
    except zmq.ZMQError:
        req_socket.close()
        zmq_context.term()
        logger.warning('shutdown')


async def run_command():
    while True:
        command = await queue.get()
        logger.info(f'收到{command["type"]}')
        if command['type'] == 'exit':
            break
        response = {
            'type': command['type'],
            'status': 'success',
            'result': None
        }
        if command['type'] == 'search':
            origin_result = False
            if 'origin_result' in command:
                origin_result = command['origin_result']
            query_str = command['query_str']
            results = set()
            try:
                results: set = await asyncio.to_thread(hitomi.process_query, query_str, origin_result)
                results = results[:10]
            except ValueError as e:
                logger.error(f'反爬虫配置失效，搜索失败{e}')
            except NotImplementedError as e:
                logger.error(f'反爬虫配置失效，搜索失败{e}')
            except ConnectionError as e:
                logger.error(f'网络链接失效，搜索失败{e}')
            except Exception as e:
                logger.error(f'其他异常，搜索失败{e}')
            if results:
                galleries = []
                for result in results:
                    gallery = {}
                    try:
                        temp_json = hitomi.get_gallery_info(result)
                        for label, val in temp_json.items():
                            if not label == 'files':
                                gallery[label] = val
                    except Exception as e:
                        logger.error(f'获取info时失败{e}')
                    if gallery:
                        galleries.append(gallery)
                response['result'] = json.dumps(galleries)
            else:
                response['status'] = 'failed'
        elif command['type'] == 'download':
            gallery_id = command['gallery_id']
            filename = ''
            try:
                filename = await asyncio.to_thread(hitomi.download, gallery_id)
            except ValueError as e:
                logger.error(f'反爬虫配置失效，下载失败{e}')
            except NotImplementedError as e:
                logger.error(f'反爬虫配置失效，下载失败{e}')
            except ConnectionError as e:
                logger.error(f'网络链接失效，下载失败{e}')
            except Exception as e:
                logger.error(f'其他异常，下载失败{e}')
            if filename:
                response['result'] = filename
            else:
                response['status'] = 'failed'
                response['result'] = 'gallery not found or server error'
        else:
            response['status'] = 'failed'
            response['result'] = 'Method Not Allowed'
        notify_socket.send_json(response)
        logger.info(f'{command["type"]}: Done')


async def shell():
    while True:
        usr_in: str = await aioconsole.ainput('Waiting Command')
        if usr_in == 'exit':
            await queue.put({'type': 'exit'})
            logger.warning('shell shutdown')
            break
        else:
            usr_in.split(' ', maxsplit=1)
            if usr_in[0] == 'download':
                if not usr_in[1].isdigit():
                    logger.error('Not Digit')
                    continue
                await queue.put({'type': usr_in[0], 'gallery_id': int(usr_in[1])})

loop = asyncio.get_event_loop()
loop.create_task(zmq_server())
loop.create_task(shell())
loop.run_until_complete(run_command())
req_socket.close()
zmq_context.term()
logger.warning('Server App down')
