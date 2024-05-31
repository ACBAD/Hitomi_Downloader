import json
import os
import re
import time
import urllib.parse
import requests

gg_list = []
fucking_b = ''
fucking_o = None


def extract_galleryinfo(js_code):
    # 使用正则表达式匹配 galleryinfo 变量的 JSON 对象
    if 'galleryinfo' not in js_code:
        print(js_code)
        raise ValueError("galleryinfo not found")
    match = re.search(r'{.*', js_code, re.DOTALL)
    # 提取匹配的 JSON 字符串
    json_str = match.group(0)
    # 解析 JSON 字符串为 Python 字典
    try:
        galleryinfo_dict = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Error decoding JSON: {e}")
    return galleryinfo_dict


def get_gallery_info(gallery_id):
    req_url = f'https://ltn.hitomi.la/galleries/{gallery_id}.js'
    response = requests.get(req_url)
    if response.status_code != 200:
        raise ValueError(f"Error getting gallery info: {response.status_code}")
    else:
        return extract_galleryinfo(response.text)


def subdomain_from_url(url, base):
    if not gg_list or fucking_o is None:
        raise ValueError('Invalid gg_config')

    def decide_gg(inner_g):
        if inner_g in gg_list:
            return 1 if fucking_o else 0
        return 0 if fucking_o else 1

    retval = 'b'
    if base:
        retval = base
    b = 16
    match = re.search(r'/[0-9a-f]{61}([0-9a-f]{2})([0-9a-f])', url)
    if not match:
        return 'a'
    m1, m2 = match.group(1), match.group(2)
    g = int(m2 + m1, b)
    return chr(97 + decide_gg(g)) + retval


def url_from_url(url, base):
    return re.sub(r'//..?\.hitomi\.la/', f'//{subdomain_from_url(url, base)}.hitomi.la/', url)


def gg_s(h):
    m = re.search(r'(..)(.)$', h)
    if m:
        return str(int(m.group(2) + m.group(1), 16))
    return ''


def url_from_hash(galleryid, image, inner_dir=None, ext=None):
    ext = ext or inner_dir or image['name'].split('.').pop()
    inner_dir = inner_dir or 'images'
    if fucking_b == 0:
        raise ValueError('Invalid fucking_b')
    return f'https://a.hitomi.la/{inner_dir}/{fucking_b}{gg_s(image["hash"])}/{image["hash"]}.{ext}'


def get_download_urls(info):
    download_urls = {}
    for file in info['files']:
        image_name = re.sub(r'\.[^.]+$', '.webp', file['name'])
        download_urls[image_name] = url_from_url(url_from_hash(info['id'], file, 'webp', None), 'a')
    return download_urls


def set_gg():
    add_timestamp = False
    if add_timestamp:
        gg_url = f'https://ltn.hitomi.la/gg.js?_={int(time.time() * 1000)}'
    else:
        gg_url = 'https://ltn.hitomi.la/gg.js?'
    gg_resp = requests.get(gg_url).text.split('\n')
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
    globals()['gg_list'] = gg_dict['gg_list']
    globals()['fucking_b'] = gg_dict['fucking_b']
    globals()['fucking_o'] = gg_dict['fucking_o']


def test_dl():
    download_path = 'gallery_dl'
    if not os.path.exists(download_path):
        os.makedirs(download_path)
    set_gg()
    gallery_info = get_gallery_info(2859991)
    urls = get_download_urls(gallery_info)
    proxy = {
        'http': 'http://127.0.0.1:10809',
    }
    headers = {
        'referer': 'https://hitomi.la' + urllib.parse.quote(gallery_info['galleryurl'])
    }
    for name, url in urls.items():
        with open(f"{download_path}/{name}", 'wb') as f:
            print(f'downloading {name}')
            repsone = requests.get(url, proxies=proxy, headers=headers)
            if repsone.status_code != 200:
                if repsone.status_code == 404 or repsone.status_code == 403:
                    print('寄' + url)
                    break
            f.write(repsone.content)
            print(f'downloaded {name}')


def test_dl_1():
    proxy = {
        'http': 'http://127.0.0.1:10809',
    }
    headers = {
        'referer': urllib.parse.quote(
            'https://hitomi.la/doujinshi/%E8%80%81%E5%B8%AB%E8%AE%8A%E5%B0%8F%E4%BA%86-%E6%98%AF%E6%99%82%E5%80%99%E5%8F%8D%E6%93%8A%E5%96%87-%E4%B8%AD%E6%96%87-2859991.html')
    }
    resp = requests.get(
        'https://aa.hitomi.la/webp/1717156801/3064/6f939b07185c2721cb78408202ac75796a8699c8d7fb2034b999d2e837d59f8b.webp',
        proxies=proxy, headers=headers)
    print(resp.status_code)


test_dl()
