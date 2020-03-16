#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import os
from setuptools import setup
if sys.version_info[0] == 2:
    # get the Py3K compatible `encoding=` for opening files.
    from io import open


HERE = os.path.abspath(os.path.dirname(__file__))


def make_readme(root_path):
    consider_files = ("README.rst", "LICENSE", "CHANGELOG", "CONTRIBUTORS")
    for filename in consider_files:
        filepath = os.path.realpath(os.path.join(root_path, filename))
        if os.path.isfile(filepath):
            with open(filepath, mode="r", encoding="utf-8") as f:
                yield f.read()

LICENSE = "BSD License"
URL = "https://github.com/kezabelle/python-enviable"
LONG_DESCRIPTION = "\r\n\r\n----\r\n\r\n".join(make_readme(HERE))
SHORT_DESCRIPTION = "Yet another wrapper object for environment variables. Does only the things I care about."
KEYWORDS = (
    "environment",
    "variables",
)

setup(
    name="enviable",
    version="0.2.1",
    author="Keryn Knight",
    author_email="python-enviable@kerynknight.com",
    maintainer="Keryn Knight",
    maintainer_email="python-enviable@kerynknight.com",
    description=SHORT_DESCRIPTION[0:200],
    long_description=LONG_DESCRIPTION,
    py_modules=["enviable"],
    include_package_data=True,
    install_requires=[],
    zip_safe=False,
    keywords=" ".join(KEYWORDS),
    license=LICENSE,
    url=URL,
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: BSD License",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
)
