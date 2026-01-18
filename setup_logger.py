import logging
import sys
import os
from logging.handlers import RotatingFileHandler
import colorlog
from pathlib import Path
from typing import Callable


DEBUG_LEVEL = logging.DEBUG
INFO_LEVEL = logging.INFO
WARNING_LEVEL = logging.WARNING
ERROR_LEVEL = logging.ERROR
CRITICAL_LEVEL = logging.CRITICAL


def getLogger(module_name: str, log_dir=Path("logs"), debug: bool = False)\
        -> tuple[logging.Logger, Callable[[int], None], Callable[[int], None]]:
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

    def preventSB(level: int):
        raise NotImplementedError(f"Logger '{module_name}' 已经被初始化过了，别乱改 Level！")
    # 防止重复添加 Handler (Jupyter 或 多次调用时常见问题)
    if logger.handlers:
        return logger, preventSB, preventSB
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

    def setConsoleLevel(level: int):
        console_handler.setLevel(level)

    def setFileLevel(level: int):
        file_handler.setLevel(level)

    return logger, setConsoleLevel, setFileLevel


if __name__ == '__main__':
    test_logger, setLoggerConsoleLevel, setLoggerFileLevel = getLogger('logger')
    test_logger.info("HayaseYuuka!")
    test_logger.debug("This should not appear")
    setLoggerConsoleLevel(DEBUG_LEVEL)
    test_logger.debug("This should appear")

    test_logger, _, _ = getLogger('logger')
    _(DEBUG_LEVEL)
