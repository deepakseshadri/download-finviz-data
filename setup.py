from setuptools import setup, find_packages


def readme():
    with open('README.md') as f:
        return f.read()


setup(
    name='download_finviz_data',
    version='0.1',
    description='Download FINVIZ US Equities data by scraping the HTML tables on the site',
    long_description=readme(),
    author='Deepak Seshadri',
    packages=find_packages(),
    license='MIT',
    python_requires='>=3.8',
    entry_points={
        'console_scripts': [
            'download_finviz_data = download_finviz_data.__main__:main'
        ]
    },
)
