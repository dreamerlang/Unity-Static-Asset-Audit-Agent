from setuptools import setup, find_packages

setup(
    name="unity_audit",
    version="0.2.0",
    description="Unity Static Asset Audit Agent",
    packages=find_packages(),
    install_requires=[
        "Pillow>=10.0.0",
        "PyYAML>=6.0",
    ],
    python_requires=">=3.10",
    entry_points={
        "console_scripts": [
            "unity-audit=unity_audit.cli:main",
        ],
    },
)
