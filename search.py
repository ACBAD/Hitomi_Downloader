import hashlib
import struct
import time
import urllib.parse
import requests

# 定义全局变量
domain = 'ltn.hitomi.la'
galleryblockextension = '.html'
galleryblockdir = 'galleryblock'
nozomiextension = '.nozomi'

index_dir = 'tagindex'
galleries_index_dir = 'galleriesindex'
languages_index_dir = 'languagesindex'
nozomiurl_index_dir = 'nozomiurlindex'
tag_index_version = '1717125673'
galleries_index_version = '1717132361'
languages_index_version = '1717125953'
nozomiurl_index_version = '1717125950'


def get_index_version(name):
    name_dict = {
        'tag_index_version': 'tagindex',
        'galleries_index_version': 'galleriesindex',
        'languages_index_version': 'languagesindex',
        'nozomiurl_index_version': 'nozomiurlindex',
    }
    url = f'http://{domain}/{name_dict[name]}/version?_={int(time.time() * 1000)}'
    response = requests.get(url)
    return response.text


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


def get_url_at_range(url, inner_range):
    for _ in range(10):
        headers = {
            'Range': f'bytes={inner_range[0]}-{inner_range[1]}'
        }
        time.sleep(0.5)
        response = requests.get(url, headers=headers)
        if response.status_code == 200 or response.status_code == 206:
            return response.content
        elif response.status_code == 503:
            print('503 ', _)
            time.sleep(3)
        else:
            raise Exception(
                f"get_url_at_range({url}, {inner_range}) failed, response.status_code: {response.status_code}")
    raise ConnectionError('重试次数过多')


def get_node_at_address(field, address, refresh_version=False):
    max_node_size = 464
    global tag_index_version
    global galleries_index_version
    global languages_index_version
    global nozomiurl_index_version

    versions = {
        'tag_index_version': tag_index_version,
        'galleries_index_version': galleries_index_version,
        'languages_index_version': languages_index_version,
        'nozomiurl_index_version': nozomiurl_index_version,
    }

    if refresh_version:
        for version_name, version in versions.items():
            _ = 0
            for _ in range(10):
                version = get_index_version(version_name)
                if not version:
                    print(f'{version_name} failed', _)
                    time.sleep(1)
                else:
                    globals()[version_name] = version
                    break
            if version == '':
                raise ConnectionError(f'{version_name} failed totally')

    url = f'http://{domain}/{index_dir}/{field}.{tag_index_version}.index'
    if field == 'galleries':
        url = f'http://{domain}/{galleries_index_dir}/galleries.{galleries_index_version}.index'
    elif field == 'languages':
        url = f'http://{domain}/{languages_index_dir}/languages.{languages_index_version}.index'
    elif field == 'nozomiurl':
        url = f'http://{domain}/{nozomiurl_index_dir}/nozomiurl.{nozomiurl_index_version}.index'

    nodedata = get_url_at_range(url, [address, address + max_node_size - 1])
    return decode_node(nodedata)


def nozomi_address_from_state(inner_state):
    if inner_state['orderby'] != 'date' or inner_state['orderbykey'] == 'published':
        if inner_state['area'] == 'all':
            return f"//{domain}/{inner_state['orderby']}/{inner_state['orderbykey']}-{inner_state['language']}{nozomiextension}"
        return f"//{domain}/{inner_state['area']}/{inner_state['orderby']}/{inner_state['orderbykey']}/{inner_state['tag']}-{inner_state['language']}{nozomiextension}"

    if inner_state['area'] == 'all':
        return f"//{domain}/{inner_state['tag']}-{inner_state['language']}{nozomiextension}"
    return f"//{domain}/{inner_state['area']}/{inner_state['tag']}-{inner_state['language']}{nozomiextension}"


def get_galleryids_from_nozomi(inner_state):
    url = nozomi_address_from_state(inner_state)
    response = requests.get(f'http:{url}')
    nozomi: list[int] = []
    if response.status_code == 200:
        array_buffer = response.content
        total = len(array_buffer) // 4
        for i in range(total):
            nozomi.append(struct.unpack('>I', array_buffer[i * 4:(i + 1) * 4])[0])
    return nozomi


def b_search(field, key, node):
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
        for i, node_key in enumerate(inner_node['keys']):
            cmp_result = compare_arraybuffers(inner_key, node_key)
            if cmp_result <= 0:
                break
        return [cmp_result == 0, i]

    def is_leaf(inner_node):
        return all(addr == 0 for addr in inner_node['subnode_addresses'])

    if not node:
        print('节点不存在')
        return False
    if not node['keys']:  # special case for empty root
        print('节点键值不存在')
        return False
    there, where = locate_key(key, node)
    if there:
        return node['datas'][where]
    elif is_leaf(node):
        print('节点为叶子节点')
        return False
    if node['subnode_addresses'][where] == 0:
        raise IndexError('B树搜索算法出错')
    subnode_address = node['subnode_addresses'][where]
    subnode = get_node_at_address(field, subnode_address)
    return b_search(field, key, subnode)


def get_galleryids_from_data(data, refresh_version=False):
    if not data:
        return []
    global galleries_index_version
    global galleries_index_dir
    if refresh_version:
        galleries_index_version = get_index_version('galleriesindex')
    url = f'http://{domain}/{galleries_index_dir}/galleries.{galleries_index_version}.data'
    offset, length = data
    if length > 100000000 or length <= 0:
        print(f"length {length} is too long")
        return []
    inbuf = get_url_at_range(url, [offset, offset + length - 1])
    if not inbuf:
        return []
    galleryids = []
    pos = 0
    number_of_galleryids = struct.unpack_from('>I', inbuf, pos)[0]  # big-endian int32
    pos += 4
    expected_length = number_of_galleryids * 4 + 4
    if number_of_galleryids > 10000000 or number_of_galleryids <= 0:
        print(f"number_of_galleryids {number_of_galleryids} is too long")
        return []
    elif len(inbuf) != expected_length:
        print(f"inbuf.byteLength {len(inbuf)} !== expected_length {expected_length}")
        return []
    for _ in range(number_of_galleryids):
        galleryid = struct.unpack_from('>I', inbuf, pos)[0]  # big-endian int32
        galleryids.append(galleryid)
        pos += 4
    return galleryids


def get_galleryids_for_query(inner_query, inner_state):
    inner_query = inner_query.replace('_', ' ')
    initial_result = get_galleryids_from_nozomi(inner_state)
    print(f'初始搜索结果数{len(initial_result)}')
    key = hashlib.sha256(inner_query.encode()).digest()[:4]
    field = 'galleries'
    node = get_node_at_address(field, 0, True)
    if not node:
        print('not node')
        return []
    data = b_search(field, key, node)
    if not data:
        print('not data')
        return []
    positive_result = get_galleryids_from_data(data)
    print(f'正向搜索结果数{len(positive_result)}')
    filted_result = [gallery for gallery in initial_result if gallery in positive_result]
    return filted_result


def process_query(query_string):
    terms = urllib.parse.unquote(query_string).lower().strip().split()
    inner_state = {
        'area': 'all',
        'tag': 'index',
        'language': 'chinese',
        'orderby': 'date',
        'orderbykey': 'added',
        'orderbydirection': 'desc'
    }
    return get_galleryids_for_query(terms[0], inner_state)


query = 'mountainhan'

print(process_query(query))
