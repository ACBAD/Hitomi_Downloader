Translation by ChatGPT

---

# 2.0 Update 🎇

# Hitomi.la Downloader

A Python implementation for searching and downloading comics from hitomi.la.

Comics on jm have ads, and comics on eh require points to download. This website allows guests to download, but after checking, it seems that there isn't a reverse-engineered solution on GitHub, so I spent some time writing this.

Based on the reverse-engineered JS code of the website's client.

## Feel free to submit issues and pull requests.

## Features

- Supports proxies
- Retry mechanism

## Usage

```python
from hitomiv2 import Hitomi

# You can add a proxy, in the format of the requests library, it will be applied globally
proxy = {
    'http': 'http://127.0.0.1:10809',
    'https': 'http://127.0.0.1:10809'
}
hitomi = Hitomi(proxy_settings=proxy)

# Search
query_str = 'HayaseYuuka'
results: list = hitomi.query(query_str)
# Download
target_gallery = results[0]
filename = target_gallery.download(max_threads=5)
if filename:
    print(f'{filename} download completed')
else:
    print('Non-existent ID')
```

## Parameter Explanation

- `Hitomi` class
    - `storage_path_fmt`: Used to specify the download path. The downloaded comics will be stored in this path as compressed files, with the default being the working directory.
    - `proxy_settings`: Used to pass proxy settings, in the format mentioned above.
    - `debug_fmt`: Debug mode, default is False. If you think the script isn't working as expected, set it to True to view debug information. Please include the debug logs when submitting an issue.

- `hitomi.query` function
    - `query_string`: The search keyword, a string variable.
    - `origin_result`: Defaults to False, which only returns Chinese results. Set to True to get fully keyword-based results.
    - The returned result is a list containing instances of the Comic class. If no results or an error occurs, an empty list is returned.

- `Comic.download` function
    - `max_threads`: Maximum number of threads, default is 1 (no multithreading).
    - Returns the downloaded filename, or an empty string if the download fails.

## Notes

1. **Initialization**  
   Due to the website’s anti-scraping mechanism, some parameters need to be retrieved for parsing. The initialization essentially requests and stores some parameters locally to avoid IP bans due to excessive requests. So, if an unhandled exception occurs and the script stops running, it won't cause any issues.
