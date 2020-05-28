from setuptools import setup


def readme():
    with open('README.md') as f:
        return f.read()


setup(
    name='download_finviz_data',
    version='0.1',
    description='Download FINVIZ US Equities data by scraping the HTML tables on the site',
    long_description=readme(),
    packages=['download_finviz_data'],
    author='Deepak Seshadri',
    license='MIT',
    entry_points={
        'console_scripts': [
            'download_finviz_data = download_finviz_data.__main__:main'
        ]
    },
)
