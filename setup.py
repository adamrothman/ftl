#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from setuptools import find_packages
from setuptools import setup


setup(
    name='ftl',
    version='0.0.1',

    packages=find_packages(),

    install_requires=[
        'h2',
        'multidict',
    ],

    author='Adam Rothman',
    author_email='adam@other.xyz',
    license='Apache License 2.0',

    description='asyncio-based HTTP/2 implementation',
    classifiers=[
        'Intended Audience :: Developers',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
    ],

    url='https://github.com/other-xyz/ftl',
)
