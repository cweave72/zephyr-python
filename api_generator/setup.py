from setuptools import setup, find_packages

NAME = "api_generator"
DESC = "Protobuf api generator"
VERSION = "0.1.0"

required = [
    "click",
    "rich",
    "PyYAML",
]

setup(
    name=NAME,
    version=VERSION,
    description=DESC,
    author='cdw',
    entry_points={
        'console_scripts': [
            'api_gen=api_generator.main:main'
        ],
    },
    packages=find_packages(),
    install_requires=required
)
