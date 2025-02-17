from setuptools import setup, find_packages

NAME = "protorpc"
DESC = "Protobuf rpc library"
VERSION = "0.1.0"

required = [
    "click",
    "rich",
]

setup(
    name=NAME,
    version=VERSION,
    description=DESC,
    author='cdw',
    entry_points={
        'console_scripts': [
            'run_protorpc_gen=protorpc.generator.generator:entrypoint',
            'protoc-gen-protorpc=protorpc.generator.generator:generator_main',
        ],
    },
    packages=find_packages(),
    install_requires=required
)
