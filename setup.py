#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from setuptools import find_packages
from setuptools import setup


setup(
    name='asynch2',
    version='0.0.0',

    packages=find_packages(),

    install_requires=[
        'h2',
        'multidict',
    ],

    author='Adam Rothman',
    author_email='adam@other.xyz',
    description='asyncio-based HTTP/2 implementation',
    url='https://github.com/other-xyz/asynch2',
)
