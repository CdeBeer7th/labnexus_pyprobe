"""
File: setup.py
Author: Coenraad de Beer

"""
from setuptools import setup

setup(
    name="labnexus_pyprobe",
    version="0.1",
    description="Experimental data automated sync",
    author="de Beer, Coenraad",
    author_email="coenraad.debeer@gmail.com",
    py_modules=['labnexus_pyprobe'],
    install_requires=[
        "argparse",
        "datetime",
        "requests"
    ]
)
