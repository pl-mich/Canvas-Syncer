# Canvas-Syncer

A script that lets you sync your local files with those under the "Files" tab of your course sites on [Canvas](https://umich.instructure.com).

This forked version contains bug fixes, modifications to user input and output, as well as a logging function to facilitate futher debugging.

## Usage

**The following bash script would install the latest release version of the original BoYanZh/Canvas-Syncer from `pip`, instead of the version on this repository.**

```bash
pip3 install canvassyncer
canvassyncer
```

Then follow the onscreen guide to provide your access token, course name or number, local file storage location, etc.

If you have not installed `pip` yet, you may refer to <https://pip.pypa.io/en/stable/installing/> or the search engine to get your `pip`.

### Optional arguments

```
  -h, --help            show this help message and exit
  -r                    recreate config file
  -y                    confirm all prompts
  --no-subfolder        do not create a course code named subfolder when synchronizing files
  -p PATH, --path PATH  appoint config file path
  -x PROXY, --proxy PROXY
                        download proxy
  -V, --version         show program's version number and exit
  -d, --debug           show debug information
```

### Canvas Access Token Generation

Open Your Canvas-Account-Approved Integrations-New Access Token

You may also refer to <https://github.com/tc-imba/canvas-auto-rubric#generate-key-access-token-on-canvas>

## Futher Contributions

Please feel free to create issues and pull requests.

> TODO: Compile source code into executable and publish it on either `pip` or the release page of this repository.
