"""
File: setup.py
Project: PyEnzyme
Author: Coenraad de Beer

"""

from setuptools import setup

setup(
    name="install",
    version="0.1",
    description="Experimental data automated sync",
    url="https://github.com/CdeBeer7th/labnexus_pyprobe",
    author="de Beer, Coenraad",
    author_email="coenraad.debeer@gmail.com",
    packages=setuptools.find_packages(),
    install_requires=[
        "argparse",
        "datetime",
        "requests"
    ]
)
