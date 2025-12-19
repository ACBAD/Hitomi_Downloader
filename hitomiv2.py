import asyncio
import hashlib
import json
import logging
import os
import re
import struct
import sys
import tempfile
import time
import urllib.parse
import zipfile
from typing import IO, Callable, Optional, Awaitable, Any
import httpx
from pydantic import BaseModel, Field, field_validator
from tqdm import tqdm

from setup_logger import get_logger

logger = get_logger('Hitomi')

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
HTTP_PROXY = None

if os.environ.get('HTTP_PROXY', None):
    HTTP_PROXY = os.environ.get('HTTP_PROXY', None)


async def robust_get(get_url: str, header=None):
    logger.debug(f'请求 {get_url}')
    for itime in range(10):
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=10, max_redirects=50) as client:
                response = await client.get(get_url, headers=header)
            if 200 <= response.status_code < 300:
                return response
            else:
                if itime > 5:
                    logger.warning(f'服务器返回{response.status_code}，当前次数 {itime}')
        except KeyboardInterrupt:
            raise KeyboardInterrupt
        except Exception as e:
            logger.error(f'请求时发生错误: {e}')
        await asyncio.sleep(1)
    logger.error('请求失败')
    return None


async def set_gg(add_timestamp=False):
    if add_timestamp:
        gg_url = f'https://ltn.gold-usergeneratedcontent.net/gg.js?_={int(time.time() * 1000)}'
    else:
        gg_url = 'https://ltn.gold-usergeneratedcontent.net/gg.js?'
    gg_resp = (await robust_get(gg_url)).text

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


class Language(BaseModel):
    name: str
    galleryid: int
    language_localname: str
    url: str


class Parody(BaseModel):
    # 保留原始字段名 parody，即使它是单数形式
    parody: str
    url: str


class Group(BaseModel):
    group: str
    url: str


class Tag(BaseModel):
    tag: str
    url: str
    male: Optional[str] = ""
    female: Optional[str] = ""

    @field_validator('male', 'female', mode='before')
    @classmethod
    def coerce_int_to_str(cls, v):
        """
        拦截原始输入：若为 int 则强转为 str，
        解决 tags.x.female 报错 [input_value=1, input_type=int]
        """
        if isinstance(v, int):
            return str(v)
        return v


class PageInfo(BaseModel):
    hasavif: int
    hash: str
    height: int
    width: int
    name: str


class Character(BaseModel):
    character: str
    url: str


class Artist(BaseModel):
    artist: str
    url: str


# --- 主模型定义 ---

class Comic(BaseModel):
    id: str  # 原始 JSON 中 id 为字符串类型
    title: str
    type: str
    language: str
    language_localname: str
    date: str

    galleryurl: str
    blocked: int
    # 嵌套结构：Pydantic 会自动处理 list[Model] 的转换
    files: list[PageInfo]
    languages: list[Language]
    # 初始化可选
    parodys: Optional[list[Parody]] = None
    tags: Optional[list[Tag]] = None
    characters: Optional[list[Character]] = None
    artists: Optional[list[Artist]] = None
    # 可选字段 (Nullable)
    datepublished: Optional[str] = None
    related: Optional[list[int]] = None
    groups: Optional[list[Group]] = None
    videofilename: Optional[str] = None
    japanese_title: Optional[str] = None
    video: Optional[str] = None
    # 这里的 list[Any] 用于处理空列表或未知结构的列表
    scene_indexes: list[Any] = Field(default_factory=list)

    # 针对 id 的预处理验证器
    @field_validator('id', mode='before')
    @classmethod
    def coerce_id_to_str(cls, v):
        """
        拦截原始输入：若为 int 则强转为 str，
        解决 id 报错 [input_value=1441484, input_type=int]
        """
        if isinstance(v, int):
            return str(v)
        return v

    def model_post_init(self, context: Any, /) -> None:
        if self.parodys is None:
            self.parodys = []
        if self.tags is None:
            self.tags = []
        if self.characters is None:
            self.characters = []
        if self.artists is None:
            self.artists = []


async def decode_download_urls(files: list[PageInfo]) -> dict[str, str]:
    gg_m, gg_b, gg_d = await set_gg()

    # noinspection PyUnusedLocal
    def url_from_hash(galleryid, image: PageInfo, ext=None):
        # 修改点 1: image['name'] -> image.name
        # 注意：保留了原代码中的逻辑（虽然 'or image.name...' 这部分永远不会执行）
        ext = ext or "webp" or image.name.split('.').pop()

        # 修改点 2: image["hash"] -> image.hash
        ihash = image.hash

        # 核心逻辑保持不变
        inum = int(ihash[-1] + ihash[-3:-1], 16)

        url = "https://{}{}.{}/{}/{}/{}.{}".format(
            ext[0],
            gg_m.get(inum, gg_d) + 1,
            "gold-usergeneratedcontent.net",
            gg_b,
            inum,
            ihash,
            ext,
        )

        return url

    download_urls = {}
    for file in files:
        # 修改点 3: file['name'] -> file.name
        image_name = re.sub(r'\.[^.]+$', '.webp', file.name)

        # 传入 Pydantic 对象 file
        download_urls[image_name] = url_from_hash(0, file, None)

    return download_urls


class Hitomi:
    def __init__(self, proxy_settings=None, debug_fmt=False):
        global proxy
        self.debug = debug_fmt
        if self.debug:
            for handler in logger.handlers:
                if isinstance(handler, logging.StreamHandler):
                    handler.setLevel(logging.DEBUG)
        proxy = proxy_settings

    @staticmethod
    async def refresh_version():
        for version_name, version in index_versions.items():
            if version_name == index_dir:
                continue
            url = f'https://{domain}/{version_name}/version?_={int(time.time() * 1000)}'
            logger.debug(f'请求url: {url}')
            response = await robust_get(url)
            version = response.text
            if not version:
                logger.error(f'refresh_versions: getting {version_name} failed')
            else:
                logger.debug(f'{version_name}:{version}')
                index_versions[version_name] = version
                break
            if version == '':
                raise ConnectionError(f'{version_name} failed totally')

    @staticmethod
    async def get_url_at_range(url, inner_range):
        logger.debug(inner_range)
        for _ in range(10):
            headers = {
                'Range': f'bytes={inner_range[0]}-{inner_range[1]}'
            }
            response = await robust_get(url, header=headers)
            if response.status_code == 200 or response.status_code == 206:
                return response.content
            elif response.status_code == 503:
                logger.warning(f'503 error in getting indexes, now:{_}')
                await asyncio.sleep(2)
            else:
                raise Exception(
                    f"get_url_at_range({url}, {inner_range}) failed, response.status_code: {response.status_code}")
        raise ConnectionError('重试次数过多')

    async def get_node_at_address(self, field, address):
        max_node_size = 464

        async def decode_node(data):
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
        nodedata = await self.get_url_at_range(url, [address, address + max_node_size - 1])
        return await decode_node(nodedata)

    async def b_search(self, field, key, node):
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
            # raise NotImplementedError('index_versions已过期')
            logger.debug('reach node leaf, perheps error')
            return None
        if node['subnode_addresses'][where] == 0:
            # raise NotImplementedError('index_versions已过期')
            logger.debug('reach node leaf, perheps error')
            return None
        logger.debug(f'subnode_addresses: {node["subnode_addresses"]}')
        subnode_address = node['subnode_addresses'][where]
        logger.debug(f'where:{where}, subnode_address:{subnode_address}')
        subnode = await self.get_node_at_address(field, subnode_address)
        return self.b_search(field, key, subnode)

    async def get_galleryids_for_query(self, inner_query) -> set:
        async def get_galleryids_from_data(inner_data) -> set:
            galleryids = set()
            if not inner_data:
                return galleryids
            url = f'http://{domain}/{galleries_index_dir}/galleries.{index_versions[galleries_index_dir]}.data'
            offset, length = inner_data
            if length > 100000000 or length <= 0:
                logger.error(f"results length {length} is too long")
                return galleryids
            inbuf = await self.get_url_at_range(url, [offset, offset + length - 1])
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
        node = await self.get_node_at_address(field, 0)
        if not node:
            logger.error('not node')
            return set()
        data = self.b_search(field, key, node)
        if not data:
            logger.debug('not data')
            return set()
        return await get_galleryids_from_data(data)

    @staticmethod
    async def get_comic(gallery_id) -> Optional[Comic]:
        req_url = f'https://{domain}/galleries/{gallery_id}.js'
        response = await robust_get(req_url)
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
            return Comic.model_validate(galleryinfo_dict)
        else:
            raise ValueError(f"Error getting gallery info: {response.status_code}")

    async def query(self, query_string, origin_result=False, ret_id=False) -> list[Comic] | set[int]:
        terms = urllib.parse.unquote(query_string).lower().strip().split(' ')
        results = set()
        tasks = [self.get_galleryids_for_query(term) for term in terms]
        search_results = await asyncio.gather(*tasks)
        for search_result in search_results:
            if not results:
                results = search_result.copy()
            else:
                results = results & search_result

        logger.info(f'正向搜索结果数{len(results)}')

        async def get_galleryids_from_nozomi(nozomi_state):
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
            response = await robust_get(f'http:{url}')
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
            initial_result = await get_galleryids_from_nozomi(inner_state)
            logger.info(f'偏好过滤结果数{len(initial_result)}')
            final_result_ids = {gallery for gallery in initial_result if gallery in results}
        if ret_id:
            return final_result_ids
        results = []
        for icomic_id in final_result_ids:
            results.append(self.get_comic(icomic_id))
        return results


async def download_comic(comic: Comic, file: IO[bytes],
                         max_threads=1,
                         phase_callback: Callable[[str], Awaitable[None]] = None) -> bool:
    if not comic.files:
        logger.warning(f'comic has no files')
        return False
    headers = {'referer': 'https://hitomi.la' + urllib.parse.quote(comic.galleryurl)}
    pbar: Optional[tqdm] = None
    file_urls = await decode_download_urls(comic.files)
    if phase_callback is None:
        pbar = tqdm(total=len(file_urls), desc="Downloading", unit="file")

    # noinspection PyUnusedLocal
    async def _tqdm_callback(dl_url: str):
        pbar.update(1)
    if phase_callback is None:
        phase_callback = _tqdm_callback
    sem = asyncio.Semaphore(max_threads)

    async def download_file(url_name: str, url: str) -> tuple[str, tempfile.SpooledTemporaryFile]:
        async with sem:
            response = await robust_get(url, header=headers)
            if response.status_code >= 500:
                raise TimeoutError('线程数量过多')
            if not response:
                raise NotImplementedError('反爬虫配置可能已失效')
            f = tempfile.SpooledTemporaryFile(max_size=1024 ** 2)
            f.write(response.content)
            f.seek(0)
            await phase_callback(url)
            return url_name, f
    tasks = [download_file(name, url) for name, url in file_urls.items()]
    downloaded_files_data: list[tuple[str, tempfile.SpooledTemporaryFile]] = await asyncio.gather(*tasks)
    downloaded_files_data.sort(key=lambda item: item[0])

    # 哈希级可复现构建, 勿修改任何打包流程
    with zipfile.ZipFile(file, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_name, file_data in downloaded_files_data:
            zinfo = zipfile.ZipInfo(file_name, date_time=(1980, 1, 1, 0, 0, 0))
            zinfo.external_attr = 0o100644 << 16
            zinfo.compress_type = zipfile.ZIP_DEFLATED
            zipf.writestr(zinfo, file_data.read())
    file.seek(0)

    return True


async def cli_download(hitomi: Hitomi, comic_list: list[int]):
    await hitomi.refresh_version()
    for comic_id in comic_list:
        comic = await hitomi.get_comic(comic_id_g)
        with open(f'{comic_id}.zip', 'wb') as f:
            await download_comic(comic, f, max_threads=5)

if __name__ == '__main__':
    if len(sys.argv) == 1:
        print('直接将id作为参数以下载')
        exit(0)
    arg_list_g = sys.argv
    del arg_list_g[0]
    comic_list_g = []
    for comic_id_g in arg_list_g:
        if not comic_id_g.isdigit():
            print(f'{comic_id_g}为非法id，退出')
            exit(0)
        comic_list_g.append(int(comic_id_g))

    hitomi_g = Hitomi(proxy_settings=HTTP_PROXY)
    asyncio.run(cli_download(hitomi_g, comic_list_g))
