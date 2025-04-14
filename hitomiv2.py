import concurrent.futures
import hashlib
import json
import os
import re
import struct
import sys
import time
import urllib.parse
import zipfile
from io import BytesIO
from typing import Union, List
import requests
from tqdm import tqdm

from setup_logger import setup

logger = setup('Hitomi')

domain = 'ltn.gold-usergeneratedcontent.net'
galleryblockextension = '.html'
galleryblockdir = 'galleryblock'
nozomiextension = '.nozomi'

index_dir = 'tagindex'
galleries_index_dir = 'galleriesindex'
languages_index_dir = 'languagesindex'
nozomiurl_index_dir = 'nozomiurlindex'

index_versions = {
    index_dir: '',
    galleries_index_dir: '',
    languages_index_dir: '',
    nozomiurl_index_dir: ''
}

proxy = None


def secure_get(get_url, header=None):
    for itime in range(10):
        try:
            response = requests.get(get_url, headers=header, proxies=proxy)
            if 200 <= response.status_code < 300:
                return response
            else:
                if itime > 5:
                    logger.warning(f'服务器返回{response.status_code}，当前次数 {itime}')
        except KeyboardInterrupt:
            raise KeyboardInterrupt
        except Exception as e:
            logger.warning(str(e))
        time.sleep(1)
    logger.error('请求失败')
    return None


def set_gg(add_timestamp=False):
    if add_timestamp:
        gg_url = f'https://ltn.gold-usergeneratedcontent.net/gg.js?_={int(time.time() * 1000)}'
    else:
        gg_url = 'https://ltn.gold-usergeneratedcontent.net/gg.js?'
    gg_resp = secure_get(gg_url).text

    m = {}

    keys = []
    for match in re.finditer(
            r"case\s+(\d+):(?:\s*o\s*=\s*(\d+))?", gg_resp):
        key, value = match.groups()
        keys.append(int(key))

        if value:
            value = int(value)
            for key in keys:
                m[key] = value
            keys.clear()

    for match in re.finditer(
            r"if\s+\(g\s*===?\s*(\d+)\)[\s{]*o\s*=\s*(\d+)", gg_resp):
        m[int(match.group(1))] = int(match.group(2))

    d = re.search(r"(?:var\s|default:)\s*o\s*=\s*(\d+)", gg_resp)
    b = re.search(r"b:\s*[\"'](.+)[\"']", gg_resp)

    return m, b.group(1).strip("/"), int(d.group(1)) if d else 0


def refresh_version():
    for version_name, version in index_versions.items():
        if version_name == index_dir:
            continue
        url = f'http://{domain}/{version_name}/version?_={int(time.time() * 1000)}'
        logger.debug(f'请求url: {url}')
        response = secure_get(url)
        version = response.text
        if not version:
            logger.error(f'refresh_versions: getting {version_name} failed')
        else:
            logger.debug(f'{version_name}:{version}')
            index_versions[version_name] = version
            break
        if version == '':
            raise ConnectionError(f'{version_name} failed totally')


def decode_download_urls(info):
    gg_m, gg_b, gg_d = set_gg()

    # noinspection PyUnusedLocal
    def url_from_hash(galleryid, image, ext=None):
        ext = ext or "webp" or image['name'].split('.').pop()
        ihash = image["hash"]
        inum = int(ihash[-1] + ihash[-3:-1], 16)
        url = "https://{}{}.{}/{}/{}/{}.{}".format(
            ext[0], gg_m.get(inum, gg_d) + 1, "gold-usergeneratedcontent.net",
            gg_b, inum, ihash, ext,
        )

        return url

    download_urls = {}
    for file in info['files']:
        image_name = re.sub(r'\.[^.]+$', '.webp', file['name'])
        download_urls[image_name] = url_from_hash(info['id'], file, None)
    return download_urls


class Comic:
    def __init__(self, json_info: dict, storage_path=None):
        self.raw_info = json_info
        self.title = json_info['title']
        self.authors = json_info['artists']
        self.id = json_info['id']
        self.url = json_info['galleryurl']
        self.file_urls = decode_download_urls(json_info)
        self.tags = json_info['tags']
        self.storage_path = storage_path
        self.parodys = json_info['parodys']
        self.characters = json_info['characters']

    def __str__(self):
        return f'Title: {self.title} ID: {self.id} Author: {self.authors}'

    def download(self, max_threads=1, filename=None, storage_path=None):
        if storage_path is not None:
            download_path = storage_path
        elif self.storage_path is not None:
            download_path = self.storage_path
        else:
            logger.warning('下载路径未配置，默认当前目录')
            download_path = '.'
        downloaded_files_data = []
        if not self.raw_info:
            logger.warning(f'gallery_id{self.id}无效')
            return ''
        headers = {'referer': 'https://hitomi.la' + urllib.parse.quote(self.url)}

        def download_file(inner_name, inner_url):
            response = secure_get(inner_url, header=headers)
            if response.status_code >= 500:
                raise TimeoutError('线程数量过多')
            if not response:
                raise NotImplementedError('反爬虫配置可能已失效')

            # 将请求内容直接存储到内存中的BytesIO对象
            img_byte_arr = BytesIO(response.content)
            return inner_name, img_byte_arr

        if max_threads > 1:
            # 使用tqdm跟踪并发下载进度
            total_num = len(self.file_urls)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
                # 提交所有的下载任务
                futures = {executor.submit(download_file, name, url): name for name, url in self.file_urls.items()}
                with tqdm(total=total_num, desc="Downloading", ncols=100, unit="file") as pbar:
                    # 使用as_completed来获取任务的完成状态
                    for future in concurrent.futures.as_completed(futures):
                        name = futures[future]
                        try:
                            result = future.result()
                            downloaded_files_data.append(result)
                        except Exception as e:
                            logger.error(f"Error downloading {name}: {e}")
                        # 更新进度条
                        pbar.update(1)
        else:
            total_num = len(self.file_urls)
            now_num = 0
            # 使用tqdm显示进度条
            for name, url in tqdm(self.file_urls.items(), desc="Downloading", total=total_num, ncols=100, unit="file"):
                logger.debug(f'downloading {name}')
                downloaded_files_data.append(download_file(name, url))
                now_num += 1

        logger.warning(f'{self.id}下载完成')
        if filename is None:
            filename = str(self.id)
        # 在内存中创建一个ZIP文件
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_name, file_data in downloaded_files_data:
                zipf.writestr(file_name, file_data.read())
        # 将ZIP文件保存到硬盘
        zip_buffer.seek(0)
        with open(os.path.join(download_path, f'{filename}.zip'), 'wb') as f:
            f.write(zip_buffer.read())
        return f'{filename}.zip'

    def get_tag_list(self):
        return [tag['tag'] for tag in self.tags]


class Hitomi:
    def __init__(self, storage_path_fmt=os.curdir, proxy_settings=None, debug_fmt=False):
        global proxy
        self.debug = debug_fmt
        if self.debug:
            logger.setLevel('DEBUG')
        proxy = proxy_settings
        refresh_version()
        logger.info('搜索功能初始化完成')
        self.storage_path = storage_path_fmt
        if storage_path_fmt is os.curdir:
            logger.warning(f'下载路径未配置，默认采用当前工作路径:{self.storage_path}')
        elif not os.path.exists(storage_path_fmt):
            logger.warning('配置的下载路径不存在，创建')
            os.mkdir(storage_path_fmt)

    @staticmethod
    def get_url_at_range(url, inner_range):
        logger.debug(inner_range)
        for _ in range(10):
            headers = {
                'Range': f'bytes={inner_range[0]}-{inner_range[1]}'
            }
            response = secure_get(url, header=headers)
            if response.status_code == 200 or response.status_code == 206:
                return response.content
            elif response.status_code == 503:
                logger.warning(f'503 error in getting indexes, now:{_}')
                time.sleep(2)
            else:
                raise Exception(
                    f"get_url_at_range({url}, {inner_range}) failed, response.status_code: {response.status_code}")
        raise ConnectionError('重试次数过多')

    def get_node_at_address(self, field, address):
        max_node_size = 464

        def decode_node(data):
            b_size = 16
            if not data:
                return None
            node = {
                'keys': [],
                'datas': [],
                'subnode_addresses': [],
            }
            pos = 0
            number_of_keys, = struct.unpack_from('>I', data, pos)
            pos += 4
            keys = []
            for _ in range(number_of_keys):
                key_size, = struct.unpack_from('>I', data, pos)
                if not key_size or key_size > 32:
                    raise ValueError("fatal: !key_size || key_size > 32")
                pos += 4
                key = data[pos:pos + key_size]
                keys.append(key)
                pos += key_size
            number_of_datas, = struct.unpack_from('>I', data, pos)
            pos += 4
            datas = []
            for _ in range(number_of_datas):
                offset, = struct.unpack_from('>Q', data, pos)
                pos += 8
                length, = struct.unpack_from('>I', data, pos)
                pos += 4
                datas.append((offset, length))
            number_of_subnode_addresses = b_size + 1
            subnode_addresses = []
            for _ in range(number_of_subnode_addresses):
                subnode_address, = struct.unpack_from('>Q', data, pos)
                pos += 8
                subnode_addresses.append(subnode_address)
            node['keys'] = keys
            node['datas'] = datas
            node['subnode_addresses'] = subnode_addresses
            return node

        url = f'http://{domain}/{index_dir}/{field}.{index_versions[index_dir]}.index'
        if field == 'galleries':
            url = f'http://{domain}/{galleries_index_dir}/galleries.{index_versions[galleries_index_dir]}.index'
        elif field == 'languages':
            url = f'http://{domain}/{languages_index_dir}/languages.{index_versions[languages_index_dir]}.index'
        elif field == 'nozomiurl':
            url = f'http://{domain}/{nozomiurl_index_dir}/nozomiurl.{index_versions[nozomiurl_index_dir]}.index'
        nodedata = self.get_url_at_range(url, [address, address + max_node_size - 1])
        return decode_node(nodedata)

    def b_search(self, field, key, node):
        logger.debug('b树搜索')

        def compare_arraybuffers(dv1, dv2):
            top = min(len(dv1), len(dv2))
            for i in range(top):
                if dv1[i] < dv2[i]:
                    return -1
                elif dv1[i] > dv2[i]:
                    return 1
            return 0

        def locate_key(inner_key, inner_node):
            cmp_result = -1
            i = 0
            flag = True
            for i in range(len(inner_node['keys'])):
                cmp_result = compare_arraybuffers(inner_key, inner_node['keys'][i])
                if cmp_result <= 0:
                    logger.debug(inner_key, inner_node)
                    flag = False
                    break
            return [cmp_result == 0, i + 1 if flag else i]

        def is_leaf(inner_node):
            return all(addr == 0 for addr in inner_node['subnode_addresses'])

        if not node:
            raise NotImplementedError('index_versions已过期')
        if not node['keys']:  # special case for empty root
            raise NotImplementedError('index_versions已过期')
        there, where = locate_key(key, node)
        if there:
            return node['datas'][where]
        elif is_leaf(node):
            raise NotImplementedError('index_versions已过期')
        if node['subnode_addresses'][where] == 0:
            raise NotImplementedError('index_versions已过期')
        logger.debug(f'subnode_addresses: {node["subnode_addresses"]}')
        subnode_address = node['subnode_addresses'][where]
        logger.debug(f'where:{where}, subnode_address:{subnode_address}')
        subnode = self.get_node_at_address(field, subnode_address)
        return self.b_search(field, key, subnode)

    def get_galleryids_for_query(self, inner_query) -> set:
        def get_galleryids_from_data(inner_data) -> set:
            galleryids = set()
            if not inner_data:
                return galleryids
            url = f'http://{domain}/{galleries_index_dir}/galleries.{index_versions[galleries_index_dir]}.data'
            offset, length = inner_data
            if length > 100000000 or length <= 0:
                logger.error(f"results length {length} is too long")
                return galleryids
            inbuf = self.get_url_at_range(url, [offset, offset + length - 1])
            if not inbuf:
                return galleryids
            pos = 0
            number_of_galleryids = struct.unpack_from('>I', inbuf, pos)[0]  # big-endian int32
            pos += 4
            expected_length = number_of_galleryids * 4 + 4
            if number_of_galleryids > 10000000 or number_of_galleryids <= 0:
                logger.error(f"number_of_galleryids {number_of_galleryids} is too long")
                return galleryids
            elif len(inbuf) != expected_length:
                logger.error(f"inbuf.byteLength {len(inbuf)} !== expected_length {expected_length}")
                return galleryids
            for _ in range(number_of_galleryids):
                galleryid = struct.unpack_from('>I', inbuf, pos)[0]  # big-endian int32
                galleryids.add(galleryid)
                pos += 4
            return galleryids

        inner_query = inner_query.replace('_', ' ')

        key = hashlib.sha256(inner_query.encode()).digest()[:4]
        field = 'galleries'
        node = self.get_node_at_address(field, 0)
        if not node:
            logger.error('not node')
            return set()
        data = self.b_search(field, key, node)
        if not data:
            logger.error('not data')
            return set()
        return get_galleryids_from_data(data)

    def get_comic(self, gallery_id) -> Union[Comic, None]:
        req_url = f'https://ltn.gold-usergeneratedcontent.net/galleries/{gallery_id}.js'
        response = secure_get(req_url)
        if response.status_code == 404:
            return None
        if response.status_code == 200:
            # 使用正则表达式匹配 galleryinfo 变量的 JSON 对象
            if 'galleryinfo' not in response.text:
                logger.error(response.text)
                raise ValueError("galleryinfo not found")
            match = re.search(r'{.*', response.text, re.DOTALL)
            # 提取匹配的 JSON 字符串
            json_str = match.group(0)
            # 解析 JSON 字符串为 Python 字典
            try:
                galleryinfo_dict = json.loads(json_str)
            except json.JSONDecodeError as e:
                raise ValueError(f"Error decoding JSON: {e}")
            return Comic(galleryinfo_dict, storage_path=self.storage_path)
        else:
            raise ValueError(f"Error getting gallery info: {response.status_code}")

    def query(self, query_string, origin_result=False, multithreading=True, ret_id=False) -> Union[List[Comic], List[int]]:
        terms = urllib.parse.unquote(query_string).lower().strip().split(' ')
        results = set()
        if multithreading:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                search_results = executor.map(self.get_galleryids_for_query, terms)
                for search_result in search_results:
                    if not results:
                        results = search_result.copy()
                    else:
                        results = results & search_result
        else:
            # legacy search
            for term in terms:
                logger.debug(f'now searching for {term}')
                positive_result = self.get_galleryids_for_query(term)
                if not results:
                    results = positive_result.copy()
                else:
                    results = results & positive_result
                    if not results:
                        logger.warning('SET EMPTY')
        logger.info(f'正向搜索结果数{len(results)}')

        def get_galleryids_from_nozomi(nozomi_state):
            if nozomi_state['orderby'] != 'date' or nozomi_state['orderbykey'] == 'published':
                if nozomi_state['area'] == 'all':
                    url = (
                        f"//{domain}/{nozomi_state['orderby']}/{nozomi_state['orderbykey']}-{nozomi_state['language']}"
                        f"{nozomiextension}")
                else:
                    url = (f"//{domain}/{nozomi_state['area']}/{nozomi_state['orderby']}/{nozomi_state['orderbykey']}/"
                           f"{nozomi_state['tag']}-{nozomi_state['language']}{nozomiextension}")
            elif nozomi_state['area'] == 'all':
                url = f"//{domain}/{nozomi_state['tag']}-{nozomi_state['language']}{nozomiextension}"
            else:
                url = f"//{domain}/{nozomi_state['area']}/{nozomi_state['tag']}-{nozomi_state['language']}{nozomiextension}"
            response = secure_get(f'http:{url}')
            nozomi: set = set()
            if response.status_code == 200:
                array_buffer = response.content
                total = len(array_buffer) // 4
                for i in range(total):
                    nozomi.add(struct.unpack('>I', array_buffer[i * 4:(i + 1) * 4])[0])
            return nozomi

        final_result_ids = results

        if not origin_result:
            inner_state = {
                'area': 'all',
                'tag': 'index',
                'language': 'chinese',
                'orderby': 'date',
                'orderbykey': 'added',
                'orderbydirection': 'desc'
            }
            initial_result = get_galleryids_from_nozomi(inner_state)
            logger.info(f'偏好过滤结果数{len(initial_result)}')
            final_result_ids = {gallery for gallery in initial_result if gallery in results}
        if ret_id:
            return final_result_ids
        results = []
        for icomic_id in final_result_ids:
            results.append(self.get_comic(icomic_id))
        return results


if __name__ == '__main__':
    if len(sys.argv) == 1:
        print('直接将id作为参数以下载')
        exit(0)
    comic_list = sys.argv
    del comic_list[0]
    for comic_id in comic_list:
        if not comic_id.isdigit():
            print(f'{comic_id}为非法id，退出')
            exit(0)

    hitomi = Hitomi(proxy_settings={'http': 'http://127.0.0.1:10809',
                                    'https': 'http://127.0.0.1:10809'})
    for comic_id in comic_list:
        comic = hitomi.get_comic(comic_id)
        comic.download(max_threads=5)
