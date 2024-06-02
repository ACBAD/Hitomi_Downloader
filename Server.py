import asyncio
import json

import aioconsole
import zmq

from Hitomi import Hitomi
from setup_logger import setup

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
                results = []
                try:
                    results = await asyncio.to_thread(hitomi.process_query(query_str))
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
            elif req['type'] == 'download':
                gallery_id = req['gallery_id']
                filename = ''
                try:
                    filename = await asyncio.to_thread(hitomi.download(gallery_id))
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
            zmq_socket.send_json(response)
    except zmq.ZMQError:
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
