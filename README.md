# 2.0更新🎇

# Hitomi.la Downloader

[English](https://github.com/tetrix1993/hitomi-downloader/blob/master/README_EN.md)

hitomi.la上漫画的搜索和下载的python实现

jm的漫画后边有广告，eh的漫画需要点数才能下，这个网站允许游客下载，但是看了看目前GitHub上好像还没有这个的逆向，于是花了点时间写了这个

基于逆向该网站客户端的js代码

## 欢迎提issue和pr

## 特点

- 支持代理
- 重试机制

## 使用

```python
from hitomiv2 import Hitomi

# 可以添加代理，以requests库的格式，对全局有效
proxy = {
    'http': 'http://127.0.0.1:10809',
    'https': 'http://127.0.0.1:10809'
}
hitomi = Hitomi(proxy_settings=proxy)

# 搜索
query_str = 'HayaseYuuka'
results: list = hitomi.query(query_str)
# 下载
target_gallery = results[0]
filename = target_gallery.download(max_threads=5)
if filename:
    print(f'{filename}下载完成')
else:
    print('不存在的id')
```

## 参数详解

- `Hitomi`类
    - `storage_path_fmt`用于传入下载路径，下载的漫画将以压缩包的形式存储在这，默认采用工作目录
    - `proxy_settings`用于传入代理设置，以上文实现中的格式
    - `debug_fmt`调试模式，默认为False，当你认为脚本工作不正常时可以传入True来查看调试信息，提交issue请附带debug日志

- `hitomi.query`函数
    - `query_string`搜索关键词，一个字符串变量
    - `origin_result`默认为False，即只返回中文结果。传入True时将返回完全根据关键词查询的结果
    - 返回的结果是列表，包含搜索到搜索到的Comic类的实例，失败和没有结果都会返回空列表
- `Comic.download`函数
  - `max_threads`最大线程数，默认是1，不启用多线程
    - 返回下载的文件名，如果下载失败就为空字符串

## 注意事项

1. 关于初始化
   由于该网站具有反爬机制，因此需要获取一些参数用于解析。初始化的本质就是请求一些参数存储在本地，以防请求次数过多封禁ip，所以如果抛出没捕获的异常导致脚本停止运行也不会产生问题

    