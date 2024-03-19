"""
File: setup.py
Author: Coenraad de Beer

"""
from setuptools import setup

setup(
    name="labnexus_pyprobe",
    version="0.2.2",
    description="Experimental data automated sync",
    author="de Beer, Coenraad",
    author_email="coenraad.debeer@gmail.com",
    
    packages=['labnexus_pyprobe'],
    entry_points={
        'console_scripts': [
            'labnexus_pyprobe = labnexus_pyprobe.__main__:main',
        ],
    },
    install_requires=[
        "argparse",
        "datetime",
        "requests"
    ]
)


