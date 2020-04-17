import io
from setuptools import setup, find_packages

setup(
    name='pg-cognition',
    version='0.0.1',
    description='Building blocks for a Postgres + Appsync + Cognito framework',
    author='Mathew Moon',
    author_email='mmoon@quinovas.com',
    url='https://github.com/QuiNovas/pg-cognition',
    license='Apache 2.0',
    include_package_data=True,
    long_description=io.open('README.rst', encoding='utf-8').read(),
    long_description_content_type='text/x-rst',
    packages=find_packages(),
    install_requires=[
        "aurora-prettyparser",
        "boto3",
        "botocore"
    ],
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: Apache Software License',
        'Programming Language :: Python :: 3.7',
    ],
)
