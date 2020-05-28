import logging

from download_finviz_data.finviz import Finviz


logger = logging.getLogger(__name__)


def main():
    logger.info('starting')
    f = Finviz()
    f.run()
    logger.info('done')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Goodbye!')
