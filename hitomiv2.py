import asyncio
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
import zipfile
from typing import IO, Callable, Optional, Awaitable, Any
import httpx
from pydantic import BaseModel, Field, field_validator
from tqdm import tqdm
from setup_logger import getLogger, DEBUG_LEVEL, INFO_LEVEL

logger, setLoggerLevel, _ = getLogger('Hitomi')

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
debug = False

if os.environ.get('HTTP_PROXY', None):
    HTTP_PROXY = os.environ.get('HTTP_PROXY', None)
    proxy = HTTP_PROXY


def setProxy(http_proxy_url: str):
    global proxy
    proxy = http_proxy_url


def setDebug(target_state: bool = None):
    global debug
    if target_state is None:
        debug = not debug
    else:
        debug = target_state
    if debug:
        setLoggerLevel(DEBUG_LEVEL)
    else:
        setLoggerLevel(INFO_LEVEL)
    return debug


search_cache = {}


async def robustGet(client: httpx.AsyncClient, get_url: str, header=None):
    logger.debug(f'请求 {get_url}')
    for itime in range(10):
        try:
            response = await client.get(get_url, headers=header)
            if 200 <= response.status_code < 300:
                return response
            elif response.status_code == 404:
                return None
            else:
                if itime > 2:
                    logger.warning(f'服务器返回{response.status_code}，当前次数 {itime}')
        except Exception as e:
            logger.warning(f'请求错误: {type(e)}:{e}')
        await asyncio.sleep(0.5 * (itime + 1))
    return None


async def setGG(client: httpx.AsyncClient, add_timestamp=False):
    if add_timestamp:
        gg_url = f'https://ltn.gold-usergeneratedcontent.net/gg.js?_={int(time.time() * 1000)}'
    else:
        gg_url = 'https://ltn.gold-usergeneratedcontent.net/gg.js?'
    gg_resp = (await robustGet(client, gg_url)).text
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
    parodys: Optional[list[Parody]] = Field(default_factory=list)
    tags: Optional[list[Tag]] = Field(default_factory=list)
    characters: Optional[list[Character]] = Field(default_factory=list)
    artists: Optional[list[Artist]] = Field(default_factory=list)
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


async def decodeDownloadUrls(files: list[PageInfo]) -> dict[str, str]:
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=5)
    async with httpx.AsyncClient(
            proxy=proxy,
            timeout=5,
            limits=limits,
            verify=False,  # 如果为了极致速度且信任环境，可关闭 verify (可选)
            http2=True  # 如果服务器支持 HTTP/2，速度会起飞 (可选，需安装 httpx[http2])
    ) as client:
        gg_m, gg_b, gg_d = await setGG(client)

    # noinspection PyUnusedLocal
    def url2hash(galleryid, image: PageInfo, ext=None):
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
        download_urls[image_name] = url2hash(0, file, None)
    return download_urls


async def refreshVersion():
    for version_name, version in index_versions.items():
        if version_name == index_dir:
            continue
        url = f'https://{domain}/{version_name}/version?_={int(time.time() * 1000)}'
        logger.debug(f'请求url: {url}')
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=20)
        async with httpx.AsyncClient(
                proxy=proxy,
                timeout=20,
                limits=limits,
                verify=False,  # 如果为了极致速度且信任环境，可关闭 verify (可选)
                http2=True  # 如果服务器支持 HTTP/2，速度会起飞 (可选，需安装 httpx[http2])
        ) as client:
            response = await robustGet(client, url)
        version = response.text
        if not version:
            logger.error(f'refresh_versions: getting {version_name} failed')
        else:
            logger.debug(f'{version_name}:{version}')
            index_versions[version_name] = version
            break
        if version == '':
            raise ConnectionError(f'{version_name} failed totally')


async def getComic(gallery_id) -> Optional[Comic]:
    req_url = f'https://{domain}/galleries/{gallery_id}.js'
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=20)
    async with httpx.AsyncClient(
            proxy=proxy,
            timeout=20,
            limits=limits,
            verify=False,  # 如果为了极致速度且信任环境，可关闭 verify (可选)
            http2=True  # 如果服务器支持 HTTP/2，速度会起飞 (可选，需安装 httpx[http2])
    ) as client:
        response = await robustGet(client, req_url)
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


async def downloadComic(comic: Comic, file: IO[bytes],
                        max_threads=5,
                        phase_callback: Callable[[str], Awaitable[None]] = None) -> bool:
    if not comic.files:
        logger.warning(f'comic has no files')
        return False
    headers = {'referer': 'https://hitomi.la' + urllib.parse.quote(comic.galleryurl)}
    pbar: Optional[tqdm] = None
    file_urls = await decodeDownloadUrls(comic.files)
    if phase_callback is None:
        pbar = tqdm(total=len(file_urls), desc="Downloading", unit="file")

    # noinspection PyUnusedLocal
    async def _tqdm_callback(dl_url: str):
        pbar.update(1)
    if phase_callback is None:
        phase_callback = _tqdm_callback
    sem = asyncio.Semaphore(max_threads)

    async def download_file(_sem: asyncio.Semaphore, client: httpx.AsyncClient, url_name: str, url: str) -> tuple[str, tempfile.SpooledTemporaryFile]:
        async with _sem:
            response = await robustGet(client, url, header=headers)
            f = tempfile.SpooledTemporaryFile(max_size=1024 ** 2)
            f.write(response.content)
            f.seek(0)
            await phase_callback(url)
            return url_name, f

    limits = httpx.Limits(max_keepalive_connections=max_threads, max_connections=max_threads)
    async with httpx.AsyncClient(
            proxy=proxy,
            timeout=5,
            limits=limits,
            http2=True  # 如果服务器支持 HTTP/2，速度会起飞 (可选，需安装 httpx[http2])
    ) as client_o:
        tasks = [download_file(sem, client_o, name, url) for name, url in file_urls.items()]
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


import struct
import hashlib

# ================= 核心配置与工具函数 =================

# B-Tree 分支因子 (Hitomi 默认为 16)
B = 16


async def get_bytes(client: httpx.AsyncClient, url: str, start: int, length: int) -> bytes:
    """基于 robustGet 的 Range 请求封装"""
    end = start + length - 1
    headers = {'Range': f'bytes={start}-{end}', 'Referer': 'https://hitomi.la/'}
    logger.debug(f'正在向 {url} 请求 {start} 到 {end} 的数据')
    resp = await robustGet(client, f"https://{domain}/{url}", header=headers)
    if resp and resp.status_code in [200, 206]:
        return resp.content
    return b''


def hash_term(term: str) -> bytes:
    """计算搜索词的 SHA-256 哈希（前4字节）"""
    sha = hashlib.sha256()
    sha.update(term.encode('utf-8'))
    return sha.digest()[:4]


# ================= B-Tree 与 索引解析类 =================

class BTreeNode:
    def __init__(self, data: bytes):
        self.keys: list[bytes] = []
        self.datas: list[tuple[int, int]] = []  # (offset, length)
        self.subnode_addrs: list[int] = []
        self._parse(data)

    def _parse(self, data: bytes):
        view = memoryview(data)
        pos = 0
        # 1. 解析 Keys
        num_keys = struct.unpack('>i', view[pos:pos + 4])[0]
        pos += 4
        for _ in range(num_keys):
            key_size = struct.unpack('>i', view[pos:pos + 4])[0]
            pos += 4
            key = view[pos:pos + key_size].tobytes()
            self.keys.append(key)
            pos += key_size
        # 2. 解析 Datas (Offset/Length)
        num_datas = struct.unpack('>i', view[pos:pos + 4])[0]
        pos += 4
        for _ in range(num_datas):
            offset = struct.unpack('>Q', view[pos:pos + 8])[0]
            pos += 8
            length = struct.unpack('>i', view[pos:pos + 4])[0]
            pos += 4
            self.datas.append((offset, length))
        # 3. 解析子节点地址
        num_subnodes = B + 1
        for _ in range(num_subnodes):
            addr = struct.unpack('>Q', view[pos:pos + 8])[0]
            pos += 8
            self.subnode_addrs.append(addr)


async def b_search_recursive(client: httpx.AsyncClient, key: bytes, node_addr: int = 0) -> Optional[tuple[int, int]]:
    """递归遍历远程 B-Tree"""
    version = index_versions[galleries_index_dir]
    index_url = f"{galleries_index_dir}/galleries.{version}.index"
    logger.debug(f'对 key: {key} node_addr: {node_addr} 执行b树搜索')
    # 读取节点头 (4KB 通常足够包含一个节点)
    node_data = await get_bytes(client, index_url, node_addr, 4096)
    if not node_data:
        return None
    node = BTreeNode(node_data)
    # 比较 Key
    idx = 0
    found = False
    for i, k in enumerate(node.keys):
        if key < k:
            idx = i
            break
        elif key == k:
            idx = i
            found = True
            break
        else:
            idx = i + 1
    if found:
        return node.datas[idx]
    # 如果是叶子节点且没找到
    if all(addr == 0 for addr in node.subnode_addrs):
        return None
    sub_addr = node.subnode_addrs[idx]
    if sub_addr == 0:
        return None
    return await b_search_recursive(client, key, sub_addr)


async def get_ids_from_data(client: httpx.AsyncClient, offset: int, length: int) -> set[int]:
    """从 .data 文件读取 ID 列表"""
    logger.debug(f'正在获取 offset: {offset}, length: {length} 的数据')
    version = index_versions[galleries_index_dir]
    data_url = f"{galleries_index_dir}/galleries.{version}.data"
    raw_data = await get_bytes(client, data_url, offset, length)
    if not raw_data:
        return set()
    # 解析 int32 数组: [count, id1, id2, ...]
    count = struct.unpack('>i', raw_data[0:4])[0]
    ids = set()
    for i in range(count):
        start = 4 + i * 4
        gid = struct.unpack('>i', raw_data[start: start + 4])[0]
        ids.add(gid)
    return ids


async def get_ids_from_nozomi(client: httpx.AsyncClient, subpath: str) -> set[int]:
    """解析 .nozomi 文件 (纯 ID 列表)"""
    logger.debug(f'对 {subpath} 发起 nozomi 请求')
    url = f"nozomi/{subpath}.nozomi"
    # 请求头中需要设置正确的 Referer，否则可能 403
    headers = {'Referer': 'https://hitomi.la/'}
    resp = await robustGet(client, f"https://{domain}/{url}", header=headers)
    if not resp or resp.status_code != 200:
        return set()
    data = resp.content
    total_ids = len(data) // 4
    ids = set()
    for i in range(total_ids):
        gid = struct.unpack('>i', data[i * 4: (i + 1) * 4])[0]
        ids.add(gid)
    return ids


# ================= 搜索逻辑 =================

async def search_single_term(client: httpx.AsyncClient, term: str) -> set[int]:
    """处理单个搜索词（包含 Tag 映射逻辑）"""
    term = term.replace('_', ' ')
    # 1. 处理命名空间 Tag (例如: female:big_breasts)
    if ':' in term:
        logger.debug(f'处理命名空间 Tag: {term}')
        left, right = term.split(':', 1)
        # 根据 search.js 的 nozomi 映射规则
        if left in ['female', 'male']:
            return await get_ids_from_nozomi(client, f"tag/{left}-{right}-all")
        elif left == 'language':
            return await get_ids_from_nozomi(client, f"index-{right}-all")
        elif left in ['artist', 'character', 'series', 'group']:
            return await get_ids_from_nozomi(client, f"{left}/{left}-{right}-all")
        elif left == 'type':  # e.g. type:manga
            return await get_ids_from_nozomi(client, f"type/{right}-all")
    # 2. 普通文本搜索 (B-Tree)
    logger.debug(f'处理单词: {term}')
    key = hash_term(term)
    data_ptr = await b_search_recursive(client, key, 0)
    if data_ptr:
        offset, length = data_ptr
        return await get_ids_from_data(client, offset, length)
    logger.debug(f'单词 {term} 未检索到任何结果')
    return set()


async def searchIDs(query: str, max_threads: int = 5) -> list[int]:
    """
        主搜索入口 (全并行优化版)
        """
    logger.info(f"搜索: {query}")
    terms = query.lower().strip().split()
    positive_terms = []
    negative_terms = []
    or_groups = [[]]
    # 1. 词法解析
    for i, term in enumerate(terms):
        if term == 'or':
            continue
        is_prev_or = (i > 0 and terms[i - 1] == 'or')
        is_next_or = (i + 1 < len(terms) and terms[i + 1] == 'or')
        if is_prev_or or is_next_or:
            or_groups[-1].append(term)
            if not is_next_or:
                or_groups.append([])
            continue
        if term.startswith('-'):
            negative_terms.append(term[1:])
        else:
            positive_terms.append(term)
    or_groups = [g for g in or_groups if g]
    # 注意：在并行模式下，"将带冒号的 term 提到最前" 的排序不再影响网络请求顺序，
    # 但仍有助于后续集合运算时的某种微小确定性，故保留。
    positive_terms.sort(key=lambda x: 0 if ':' in x else 1)
    current_ids = set()
    first_round = True
    # ================= 执行搜索逻辑 (全并行化) =================
    # 1. 构建所有并行任务 (Tasks Construction)
    # 我们将 OR 组的处理、AND 词的处理、NOT 词的处理全部放入任务池
    # 1.1 OR 组任务
    # 每个 OR 组内部是并行的，组与组之间我们也希望并行获取数据
    or_tasks = []
    limits = httpx.Limits(max_keepalive_connections=max_threads, max_connections=max_threads)
    async with httpx.AsyncClient(
            proxy=proxy,
            timeout=5,
            limits=limits,
            verify=False,  # 如果为了极致速度且信任环境，可关闭 verify (可选)
            http2=True  # 如果服务器支持 HTTP/2，速度会起飞 (可选，需安装 httpx[http2])
    ) as client:
        for group in or_groups:
            # 对每个组创建一个 gather 任务
            or_tasks.append(asyncio.gather(*[search_single_term(client, t) for t in group]))
        # 1.2 AND 词任务
        and_tasks = [search_single_term(client, t) for t in positive_terms]
        # 1.3 NOT 词任务
        not_tasks = [search_single_term(client, t) for t in negative_terms]
        # ================= 等待数据返回 (Await I/O) =================
        # 这里我们分阶段 await，以便于逻辑处理，但 request 已经在此时可以并发发出
        # 若追求极致，可以使用 asyncio.gather 将所有 task 一起发出，但这会使结果处理逻辑变得复杂
        # 鉴于 or_groups 较少见，我们优先并行化 and_tasks
        # A. 处理 OR 组 (如果存在)
        if or_tasks:
            # group_results_list 是一个列表，每个元素是该组内所有 term 的结果列表
            group_results_list = await asyncio.gather(*or_tasks)
            for group_results in group_results_list:
                # 组内取并集 (Union)
                group_union = set()
                for res in group_results:
                    group_union.update(res)
                # 组间取交集 (Intersection)
                if first_round:
                    current_ids = group_union
                    first_round = False
                else:
                    current_ids.intersection_update(group_union)
        # B. 处理 AND 词 (正向筛选)
        if and_tasks:
            # === 关键修改：此处通过 gather 并行执行所有 AND 词的搜索 ===
            and_results = await asyncio.gather(*and_tasks)
            for res in and_results:
                if first_round:
                    current_ids = res
                    first_round = False
                else:
                    # 剪枝：如果已经为空，就没必要继续交集运算了
                    if not current_ids:
                        break
                    current_ids.intersection_update(res)
        # C. 处理 NOT 词 (负向筛选)
        if not_tasks and (current_ids or first_round):
            # 注意：如果 current_ids 为空且 first_round 为 True (即只有排除词)，
            # 逻辑上应该返回全集减去排除词。但 Hitomi 默认行为通常是不给全集的。
            # 这里维持原逻辑：只在有结果时进行排除。
            not_results = await asyncio.gather(*not_tasks)
            for res in not_results:
                if current_ids:
                    current_ids.difference_update(res)
    # 排序结果 (ID 越大越新)
    return sorted(list(current_ids), reverse=True)


async def cliDownload(comic_list: list[int]):
    await refreshVersion()
    for comic_id in comic_list:
        comic = await getComic(comic_id_g)
        with open(f'{comic_id}.zip', 'wb') as f:
            await downloadComic(comic, f, max_threads=5)


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

    asyncio.run(cliDownload(comic_list_g))
