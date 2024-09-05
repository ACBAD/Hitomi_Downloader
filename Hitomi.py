import hashlib
import json
import os
import re
import struct
import time
import urllib.parse
import zipfile

import requests

from setup_logger import setup

logger = setup('Hitomi')

domain = 'ltn.hitomi.la'
galleryblockextension = '.html'
galleryblockdir = 'galleryblock'
nozomiextension = '.nozomi'

index_dir = 'tagindex'
galleries_index_dir = 'galleriesindex'
languages_index_dir = 'languagesindex'
nozomiurl_index_dir = 'nozomiurlindex'


def secure_get(*get_args, **get_kwargs):
    for itime in range(10):
        try:
            response = requests.get(*get_args, **get_kwargs)
            if 200 <= response.status_code < 300:
                return response
        except KeyboardInterrupt:
            raise KeyboardInterrupt
        except Exception as e:
            logger.warning(str(e))
        logger.warning(f'网络请求出错，重试{itime}')
        time.sleep(1)
    logger.error('请求失败')
    return None


class Hitomi:
    def __init__(self, storage_path_fmt=None, proxy_fmt=None, debug_fmt=False):
        logger.warning('Hitomi init called')
        self.index_versions = {
            index_dir: '',
            galleries_index_dir: '',
            languages_index_dir: '',
            nozomiurl_index_dir: ''
        }
        self.debug = debug_fmt
        if self.debug:
            logger.setLevel('DEBUG')
        self.init = False
        self.proxy = proxy_fmt
        if storage_path_fmt is None:
            self.storage_path = os.curdir
            logger.warning(f'下载路径未配置，默认采用当前工作路径:{self.storage_path}')
        elif not os.path.exists(storage_path_fmt):
            logger.warning('配置的下载路径不存在，创建')
            os.mkdir(self.storage_path)
        self.refresh_version()
        logger.info('搜索模块初始化完成')
        self.gg_list = []
        self.fucking_b = ''
        self.fucking_o = None
        self.set_gg()
        logger.info('下载模块初始化完成')
        logger.warning('启动完成')

    def set_gg(self, add_timestamp=False):
        if add_timestamp:
            gg_url = f'https://ltn.hitomi.la/gg.js?_={int(time.time() * 1000)}'
        else:
            gg_url = 'https://ltn.hitomi.la/gg.js?'
        gg_resp = secure_get(gg_url, proxies=self.proxy).text.split('\n')
        gg_dict = {'gg_list': [], 'fucking_b': '', 'fucking_o': None}
        for line in gg_resp:
            if line.startswith('case'):
                gg_dict['gg_list'].append(int(line[5:][:-1]))
            elif line.startswith('b:'):
                gg_dict['fucking_b'] = str(line[4:][:-1])
            elif line.startswith('o = 1'):
                gg_dict['fucking_o'] = True
            elif line.startswith('o = 0'):
                gg_dict['fucking_o'] = False
        self.gg_list = gg_dict['gg_list']
        self.fucking_b = gg_dict['fucking_b']
        self.fucking_o = gg_dict['fucking_o']

    def refresh_version(self):
        for version_name, version in self.index_versions.items():
            _ = 0
            for _ in range(10):
                url = f'http://{domain}/{version_name}/version?_={int(time.time() * 1000)}'
                response = secure_get(url, proxies=self.proxy)
                version = response.text
                if not version:
                    logger.warning(f'refresh_versions: getting {version_name} failed, now:{_}')
                    time.sleep(1)
                else:
                    logger.debug(f'{version_name}:{version}')
                    self.index_versions[version_name] = version
                    break
            if version == '':
                raise ConnectionError(f'{version_name} failed totally')
        self.init = True

    def url_from_url(self, url, base):
        def subdomain_from_url(inner_url, inner_base):
            if not self.gg_list or self.fucking_o is None:
                raise ValueError('反爬虫破解未配置')

            def decide_gg(inner_g):
                if inner_g in self.gg_list:
                    return 1 if self.fucking_o else 0
                return 0 if self.fucking_o else 1

            retval = 'b'
            if inner_base:
                retval = inner_base
            b = 16
            match = re.search(r'/[0-9a-f]{61}([0-9a-f]{2})([0-9a-f])', inner_url)
            if not match:
                return 'a'
            m1, m2 = match.group(1), match.group(2)
            g = int(m2 + m1, b)
            return chr(97 + decide_gg(g)) + retval

        return re.sub(r'//..?\.hitomi\.la/', f'//{subdomain_from_url(url, base)}.hitomi.la/', url)

    def get_download_urls(self, info):
        def url_from_hash(galleryid, image, inner_dir=None, ext=None):
            ext = ext or inner_dir or image['name'].split('.').pop()
            inner_dir = inner_dir or 'images'
            if self.fucking_b == 0:
                raise ValueError('Invalid fucking_b')

            def gg_s(h):
                m = re.search(r'(..)(.)$', h)
                if m:
                    return str(int(m.group(2) + m.group(1), 16))
                return ''

            return f'https://a.hitomi.la/{inner_dir}/{self.fucking_b}{gg_s(image["hash"])}/{image["hash"]}.{ext}'

        download_urls = {}
        for file in info['files']:
            image_name = re.sub(r'\.[^.]+$', '.webp', file['name'])
            download_urls[image_name] = self.url_from_url(url_from_hash(info['id'], file, 'webp', None), 'a')
        return download_urls

    def get_url_at_range(self, url, inner_range):
        logger.debug(inner_range)
        for _ in range(10):
            headers = {
                'Range': f'bytes={inner_range[0]}-{inner_range[1]}'
            }
            response = secure_get(url, headers=headers, proxies=self.proxy)
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

        if not self.init:
            raise ValueError('index_versions_init failed!')
        url = f'http://{domain}/{index_dir}/{field}.{self.index_versions[index_dir]}.index'
        if field == 'galleries':
            url = f'http://{domain}/{galleries_index_dir}/galleries.{self.index_versions[galleries_index_dir]}.index'
        elif field == 'languages':
            url = f'http://{domain}/{languages_index_dir}/languages.{self.index_versions[languages_index_dir]}.index'
        elif field == 'nozomiurl':
            url = f'http://{domain}/{nozomiurl_index_dir}/nozomiurl.{self.index_versions[nozomiurl_index_dir]}.index'
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

    def get_galleryids_from_nozomi(self, nozomi_state):
        if nozomi_state['orderby'] != 'date' or nozomi_state['orderbykey'] == 'published':
            if nozomi_state['area'] == 'all':
                url = f"//{domain}/{nozomi_state['orderby']}/{nozomi_state['orderbykey']}-{nozomi_state['language']}{nozomiextension}"
            else:
                url = f"//{domain}/{nozomi_state['area']}/{nozomi_state['orderby']}/{nozomi_state['orderbykey']}/{nozomi_state['tag']}-{nozomi_state['language']}{nozomiextension}"
        elif nozomi_state['area'] == 'all':
            url = f"//{domain}/{nozomi_state['tag']}-{nozomi_state['language']}{nozomiextension}"
        else:
            url = f"//{domain}/{nozomi_state['area']}/{nozomi_state['tag']}-{nozomi_state['language']}{nozomiextension}"
        response = secure_get(f'http:{url}', proxies=self.proxy)
        nozomi: set = set()
        if response.status_code == 200:
            array_buffer = response.content
            total = len(array_buffer) // 4
            for i in range(total):
                nozomi.add(struct.unpack('>I', array_buffer[i * 4:(i + 1) * 4])[0])
        return nozomi

    def get_galleryids_for_query(self, inner_query) -> set:
        def get_galleryids_from_data(inner_data) -> set:
            galleryids = set()
            if not inner_data:
                return galleryids
            url = f'http://{domain}/{galleries_index_dir}/galleries.{self.index_versions[galleries_index_dir]}.data'
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
        try:
            data = self.b_search(field, key, node)
        except NotImplementedError:
            logger.warning('index_version过期，尝试刷新')
            self.refresh_version()
            try:
                data = self.b_search(field, key, node)
            except NotImplementedError:
                raise ValueError(f'B树搜索出错,{self.index_versions[galleries_index_dir]}')
        if not data:
            logger.error('not data')
            return set()
        return get_galleryids_from_data(data)

    def get_gallery_info(self, gallery_id):
        req_url = f'https://ltn.hitomi.la/galleries/{gallery_id}.js'
        response = secure_get(req_url, proxies=self.proxy)
        if response.status_code == 404:
            return {}
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
            return galleryinfo_dict
        else:
            raise ValueError(f"Error getting gallery info: {response.status_code}")

    def process_query(self, query_string, origin_result=False):
        terms = urllib.parse.unquote(query_string).lower().strip().split(' ')
        inner_state = {
            'area': 'all',
            'tag': 'index',
            'language': 'chinese',
            'orderby': 'date',
            'orderbykey': 'added',
            'orderbydirection': 'desc'
        }
        results = set()
        for term in terms:
            logger.debug(f'now searching for {term}')
            positive_result = self.get_galleryids_for_query(term)
            if not results:
                results = positive_result.copy()
            else:
                results = results & positive_result
                if not results:
                    logger.warning('SET EMPTY')
                # new_results = set()
                # new_results = {galleryid for galleryid in positive_result if galleryid in results}
                # results.update(new_results)
        logger.info(f'正向搜索结果数{len(results)}')
        if not origin_result:
            initial_result = self.get_galleryids_from_nozomi(inner_state)
            logger.info(f'偏好过滤结果数{len(initial_result)}')
            filted_result = {gallery for gallery in initial_result if gallery in results}
            return filted_result
        return results

    def download(self, gellary_id: int):
        assert isinstance(gellary_id, int)
        download_path = self.storage_path
        downloaded_files_path = []
        if not os.path.exists('temp'):
            os.makedirs('temp')
        gallery_info = self.get_gallery_info(gellary_id)
        if not gallery_info:
            logger.warning(f'gallery_id{gellary_id}无效')
            return ''
        urls = self.get_download_urls(gallery_info)
        headers = {
            'referer': 'https://hitomi.la' + urllib.parse.quote(gallery_info['galleryurl'])
        }

        def download_file(inner_urls):
            total_num = len(inner_urls)
            now_num = 0
            for name, url in inner_urls.items():
                with open(f"temp/{name}", 'wb') as fi:
                    logger.debug(f'downloading {name}')
                    logger.info(f'Prograssing {now_num / total_num * 100:.2f}%')
                    repsone = secure_get(url, headers=headers, proxies=self.proxy)
                    if repsone.status_code != 200:
                        if repsone.status_code == 404 or repsone.status_code == 403:
                            raise NotImplementedError('反爬虫配置可能已失效')
                    fi.write(repsone.content)
                    downloaded_files_path.append(f"temp/{name}")
                now_num += 1

        try:
            download_file(urls)
            logger.warning('下载完成')
        except NotImplementedError:
            logger.warning('反爬配置失效，正在重新配置')
            downloaded_files_path = []
            self.set_gg()
            try:
                download_file(urls)
            except NotImplementedError:
                raise ValueError('爬虫失效')

        def clean_filename(filename):
            # 定义不允许的字符
            illegal_chars = {'\\', '/', ':', '*', '?', '"', '<', '>', '|'}
            # 替换不允许的字符为空格
            cleaned_filename = ''.join(c if c not in illegal_chars else ' ' for c in filename)
            # 去除连续空格并去除首尾空格
            cleaned_filename = ' '.join(cleaned_filename.split())
            return cleaned_filename

        with zipfile.ZipFile(os.path.join(download_path, 'temp.zip'), 'w') as zipf:
            for file_path in downloaded_files_path:
                zipf.write(file_path, arcname=os.path.basename(file_path))
        for img in os.listdir('temp'):
            img_path = os.path.join('temp', img)
            os.remove(img_path)
        os.rename(os.path.join(download_path, 'temp.zip'),
                  os.path.join(download_path, clean_filename(gallery_info['title']) + '.zip'))
        logger.warning('压缩完成')
        return clean_filename(gallery_info['title']) + '.zip'


if __name__ == '__main__':
    hitomi = Hitomi()
    print(hitomi.process_query('Kikyo No Seikatsu Kanri'))
    # for comic in download_list:
    #     hitomi.download(comic)
