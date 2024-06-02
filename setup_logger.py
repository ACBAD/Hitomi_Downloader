import logging
import colorlog


def setup(module_name):
    logger = logging.getLogger(module_name)
    # 创建彩色日志记录器
    handler = colorlog.StreamHandler()
    handler.setFormatter(colorlog.ColoredFormatter(
        '%(log_color)s[%(asctime)s][%(levelname)s](%(name)s)%(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        log_colors={
            'DEBUG': 'white',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red,bold',
            'CRITICAL': 'purple,bold',
        },
        reset=True,
        style='%'
    ))
    file_handler = logging.FileHandler('hitomi.log')
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(
        logging.Formatter('[%(asctime)s][%(levelname)s](%(name)s)%(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
    # 添加处理器到日志记录器
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.addHandler(file_handler)
    return logger
