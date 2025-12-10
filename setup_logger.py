import logging
import sys
import os
from logging.handlers import RotatingFileHandler
import colorlog
from pathlib import Path


def get_logger(module_name: str, log_dir=Path("logs"), debug: bool = False):
    """
    获取配置好的 Logger 对象
    :param module_name: 模块名称
    :param log_dir: 日志目录
    :param debug: 是否开启控制台调试模式 (True: 显示DEBUG级别, False: 显示INFO级别)
    :return: logging.Logger
    """
    logger = logging.getLogger(module_name)
    logger.propagate = False

    # 1. 关键修改：将总记录器的级别设置为 DEBUG
    # 这样所有级别的日志才能通过“总闸”，流向后面的 Handler 进行筛选
    logger.setLevel(logging.DEBUG)

    # 防止重复添加 Handler (Jupyter 或 多次调用时常见问题)
    if logger.handlers:
        return logger

    # 基础格式字符串
    base_fmt = (
        "[%(asctime)s] %(levelname)-8s "
        "[%(threadName)s|%(processName)s] %(name)s "
        "%(filename)s:%(funcName)s:%(lineno)s | %(message)s"
    )
    date_fmt = "%H:%M:%S"

    # ---------------------------------------------------------------
    # 2. 配置控制台 Handler (动态控制)
    # ---------------------------------------------------------------
    console_handler = logging.StreamHandler(sys.stdout)

    # 根据传入的 debug 参数决定控制台的过滤级别
    console_level = logging.DEBUG if debug else logging.INFO
    console_handler.setLevel(console_level)

    color_fmt = f"%(log_color)s{base_fmt}%(reset)s"
    console_handler.setFormatter(colorlog.ColoredFormatter(
        color_fmt,
        datefmt=date_fmt,
        reset=True,
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'red,bg_white'
        }
    ))
    logger.addHandler(console_handler)

    # ---------------------------------------------------------------
    # 3. 配置文件 Handler (只记录 WARNING)
    # ---------------------------------------------------------------
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    file_path = log_dir / Path(module_name).with_suffix('.log')
    file_handler = RotatingFileHandler(
        file_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8'
    )

    # 关键修改：强制文件 Handler 只接收 WARNING 及以上级别
    file_handler.setLevel(logging.WARNING)

    file_handler.setFormatter(logging.Formatter(base_fmt, datefmt="%Y-%m-%d " + date_fmt))
    logger.addHandler(file_handler)

    return logger


if __name__ == '__main__':
    # 场景 1: 生产环境 (默认 info, 文件仅 warning)
    print("--- 生产模式测试 ---")
    prod_logger = get_logger('prod_mod', debug=False)
    prod_logger.debug('这条 DEBUG 不会出现在控制台，也不会出现在文件')
    prod_logger.info('这条 INFO 会出现在控制台，但在文件中会被丢弃')
    prod_logger.warning('这条 WARNING 会同时出现在控制台和文件中')

    # 场景 2: 调试环境 (开启 debug)
    print("\n--- 调试模式测试 ---")
    # 注意：通常不同模块名会对应不同 Logger，这里为了演示方便用了新名字
    dev_logger = get_logger('dev_mod', debug=True)
    dev_logger.debug('这条 DEBUG 现在会出现在控制台了！(但文件依然不记录)')