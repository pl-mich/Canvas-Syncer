from setuptools import setup, find_packages

setup(
    name='canvassyncer',
    version='1.3.1',
    description='Utility to sync course files from Canvas',
    url='https://github.com/pl-mich/Canvas-Syncer',
    author='Peijing Li',
    organization='Engineering Student Government, University of Michigan',
    author_email='peijli@umich.edu',
    packages=find_packages(),
    python_requires='>=3.6',
    entry_points={
        'console_scripts': [
            'canvassyncer=canvassyncer:main',
        ],
    },
    project_urls={
        'Bug Reports': 'https://github.com/pl-mich/Canvas-Syncer/issues',
        'Source': 'https://github.com/pl-mich/Canvas-Syncer',
    },
    install_requires=[
        'requests',
        'tqdm'
    ]
)
