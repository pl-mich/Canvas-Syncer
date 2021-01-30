'''
TODO: Revise documentation and code formatting.
'''

import argparse
import json
import os
import re
import traceback
import requests
import requests.exceptions
import threading
import time
import urllib3.exceptions

from datetime import timezone, datetime
from functools import partial
from queue import Queue
from requests.adapters import HTTPAdapter
from threading import Thread
from urllib3.util.retry import Retry
from tqdm import tqdm
import ntpath

# Modules for logging
import logging
import logging.config

__version__ = "1.3.1"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           ".canvassyncer.json")
MAX_DOWNLOAD_COUNT = 64 # Used to be 8
LOGGER_CONFIG = {
                "version": 1,
                "formatters": {
                    "simpleFormatter": {
                        "class": "logging.Formatter",
                        "format": "[%(levelname)s] [%(asctime)s] %(message)s",
                        "datefmt": "%H:%M:%S"
                    }
                },
                "handlers": {
                    "fileHandler": {
                        "class": "logging.FileHandler",
                        "formatter": "simpleFormatter",
                        "filename": "canvas.log",
                        "mode": "w"
                    }
                },
                "loggers": {},
                "root": {
                    "level": "DEBUG",
                    "handlers": ["fileHandler"]
                },
            }
_sentinel = object()

print = partial(print, flush=True)

def process(s):
    '''Process the file name as input string s to ensure that it adheres to 
    Windows file naming requirements. '''
    if len(s) > 240: s = s[0,240]
    return ''.join(re.split(r"[\\\:\*\?\"\<\>\|]", s.strip()))

class MultithreadDownloader:
    # blockSize = 512
    blockSize = 1024

    def __init__(self, session, maxThread):
        self.sess = session
        self.maxThread = maxThread
        self.currentDownload = []
        self.countLock = threading.Lock()
        self.taskQueue = Queue()
        self.downloadedCnt = 0
        self.totalCnt = 0
        self.totalSize = 0
        self.tqdm = None
        self.stopSignal = False

    def downloadFile(self, i, queue):
        while True:
            if self.stopSignal:
                self.downloadedCnt = self.totalCnt
                break
            src, dst = queue.get()
            if src is _sentinel:
                queue.put([src, dst])
                break
            self.currentDownload[i] = dst.split('/')[-1].split('\\')[-1]
            tmpFilePath = ''
            try:
                r = self.sess.get(src, timeout=50, stream=True) # timeout used to be 10
                tmpFilePath = f"{dst}.tmp.{int(time.time())}"
                with open(tmpFilePath, 'wb') as fd:
                    for chunk in r.iter_content(MultithreadDownloader.blockSize):
                        self.tqdm.update(len(chunk))
                        fd.write(chunk)
                        if self.stopSignal:
                            break
                os.rename(tmpFilePath, dst)
            except Exception as e:
                print(
                    f"\nError: {e.__class__.__name__}. Failed to download {dst}")
                logger.error("Download failed!", exc_info=True)
            finally:
                if os.path.exists(tmpFilePath):
                    os.remove(tmpFilePath)
                with self.countLock:
                    self.downloadedCnt += 1

    def init(self):
        self.taskQueue = Queue()
        self.downloadedCnt = 0
        self.totalCnt = 0
        self.downloadingFileName = 'None'
        self.totalSize = 0
        self.stopSignal = False
        self.tqdm = None
        self.currentDownload = ['' for i in range(self.maxThread)]

    def create(self, infos, totalSize=0):
        self.init()
        self.totalSize = totalSize
        self.tqdm = tqdm(total=totalSize, unit='iB', unit_scale=True)
        for src, dst in infos:
            self.taskQueue.put((src, dst))
            self.totalCnt += 1
        self.taskQueue.put((_sentinel, _sentinel))

    def start(self):
        for i in range(self.maxThread):
            t = Thread(target=self.downloadFile,
                       args=(i, self.taskQueue),
                       daemon=True)
            t.start()

    def stop(self):
        self.stopSignal = True
        if self.tqdm:
            self.tqdm.close()
        print('\nOperation cancelled by user, exiting...')
        while not self.finished:
            time.sleep(0.1)

    @property
    def finished(self):
        return self.downloadedCnt >= self.totalCnt

    def waitTillFinish(self):
        word = 'None'
        downloadingFileName = ''
        while not self.finished:
            for fileName in reversed(self.currentDownload):
                if fileName:
                    downloadingFileName = fileName
                    break
            if word not in (downloadingFileName + ' ' * 5) * 2:
                word = downloadingFileName + ' ' * 5
            if len(word) <= 20:
                self.tqdm.set_description((word + ' ' * 10)[:15])
            else:
                self.tqdm.set_description(word[:15])
            # print("\r{:5d}/{:5d} Downloading... {}".format(
            #     self.downloadedCnt, self.totalCnt, word[:15]),
            #       end='')
            if len(word) > 20:
                word = word[1:] + word[0]
            time.sleep(0.1)
        if self.tqdm:
            self.tqdm.close()
        # if self.totalCnt > 0:
        #     print("\r{:5d}/{:5d} Finish!        {}".format(
        #         self.downloadedCnt, self.totalCnt, ' ' * 15))


class CanvasSyncer:
    def __init__(self, config):
        logger.debug("Initiated CanvasSyncer object.")
        self.confirmAll = config['y']
        self.config = config
        self.sess = requests.Session()
        retryStrategy = Retry(total=5,
                              status_forcelist=[429, 500, 502, 503, 504],
                              method_whitelist=["HEAD", "GET", "OPTIONS"])
        adapter = HTTPAdapter(max_retries=retryStrategy)
        self.sess.mount("https://", adapter)
        self.sess.mount("http://", adapter)
        self.downloadSize = 0
        self.laterDownloadSize = 0
        self.courseCode = {}
        self.baseurl = self.config['canvasURL'] + '/api/v1'
        self.downloadDir = os.path.normpath(self.config['downloadDir'] + "\\")
        self.newInfo = []
        self.laterFiles = []
        self.laterInfo = []
        self.skipfiles = []
        self.filesLock = threading.Lock()
        self.taskQueue = Queue()
        self.downloader = MultithreadDownloader(self.sess, MAX_DOWNLOAD_COUNT)
        if not os.path.exists(self.downloadDir):
            os.mkdir(self.downloadDir)
            logger.info(f"Created new directory at {downloadDir}.")
        logger.debug(f"CanvasSyncer.downloadDir = {self.downloadDir}")

    def sessGet(self, *args, **kwargs):
        logger.debug("Method CanvasSyncer.sessGet called.")
        if kwargs.get('timeout') is None:
            kwargs['timeout'] = 10
        if kwargs.get('header') is None:
            kwargs['headers'] = dict()
        kwargs['headers']['Authorization'] = f"Bearer {self.config['token']}"
        kwargs['proxies'] = self.config.get("proxies")
        try:
            return self.sess.get(*args, **kwargs)
        except (urllib3.exceptions.MaxRetryError,
                requests.exceptions.ConnectionError) as e:
            raise ConnectionError(e)

    def sessHead(self, *args, **kwargs):
        logger.debug("Method CanvasSyncer.sessHead called.")
        if kwargs.get('timeout') is None:
            kwargs['timeout'] = 10
        if kwargs.get('header') is None:
            kwargs['headers'] = dict()
        kwargs['headers']['Authorization'] = f"Bearer {self.config['token']}"
        kwargs['proxies'] = self.config.get("proxies")
        try:
            return self.sess.head(*args, **kwargs)
        except (urllib3.exceptions.MaxRetryError,
                requests.exceptions.ConnectionError) as e:
            raise ConnectionError(e)

    def createFolders(self, courseID, folders):
        for folder in folders.values():
            logger.debug(f"CanvasSyncer.createFolders.folder = {folder}")
            folder = os.path.normpath(folder)
            if self.config['no_subfolder']:
                folder_path = folder[1:]
                path = os.path.join(self.downloadDir, folder_path)
                logger.debug(f"CanvasSyncer.createFolders.path = {path}")
            else:
                folder_path = f"{self.courseCode[courseID]}{folder}"
                path = os.path.join(self.downloadDir, folder_path)
                logger.debug(f"CanvasSyncer.createFolders.path = {path}")
            if not os.path.exists(path):
                os.makedirs(path)
                logger.info(f"Created folder at {path}.")

    def getLocalFiles(self, courseID, folders):
        logger.debug('Method CanvasSyncer.getLocalFiles called.')
        localFiles = []
        for folder in folders.values():
            if self.config['no_subfolder']:
                path = os.path.join(self.downloadDir, folder[1:])
            else:
                path = os.path.join(self.downloadDir,
                                    f"{self.courseCode[courseID]}{folder}")
            localFiles += [
                os.path.join(folder, f).replace('\\', '/').replace('//', '/')
                for f in os.listdir(path)
                if not os.path.isdir(os.path.join(path, f))
            ]
        logger.debug(f"CanvasSyncer.getLocalFiles.localFiles = {localFiles}")
        return localFiles

    def getCourseFolders(self, courseID):
        folder_list = [
            folder
            for folder in self.getCourseFoldersWithID(courseID).values()
        ]
        return folder_list

    def getCourseFoldersWithID(self, courseID):
        res = {}
        page = 1
        while True:
            url = f"{self.baseurl}/courses/{courseID}/folders?page={page}"
            folders = self.sessGet(url).json()
            if not folders:
                logger.error(f"Error reading folders list at {url}.")
                break
            for folder in folders:
                res[folder['id']] = folder['full_name'].replace(
                    "course files", "")
                if not res[folder['id']]:
                    res[folder['id']] = '/'
                res[folder['id']] = process(res[folder['id']])
                logger.debug(
                    f"CanvasSyncer.getCourseFoldersWithID.res[folder['id']] = {res[folder['id']]}")
            page += 1
        # logger.debug(f"CanvasSyncer.getCourseFoldersWithID.res = {res}")

        return res

    def getCourseFiles(self, courseID):
        folders, res = self.getCourseFoldersWithID(courseID), {}
        page = 1
        while True:
            url = f"{self.baseurl}/courses/{courseID}/files?page={page}"
            files = self.sessGet(url).json()
            if not files:
                break
            if type(files) is dict: break
            for f in files:
                if f['folder_id'] not in folders.keys(): continue
                # f['display_name'] = re.sub(r"[\/\\\:\*\?\"\<\>\|]", "_", f['display_name'])
                f['display_name'] = process(f['display_name'])
                path = f"{folders[f['folder_id']]}/{f['display_name']}"
                path = path.replace('\\', '/').replace('//', '/')
                logger.debug(f"{f['folder_id']}.path = {path}")
                dt = datetime.strptime(f["modified_at"], "%Y-%m-%dT%H:%M:%SZ")
                modifiedTimeStamp = dt.replace(tzinfo=timezone.utc).timestamp()
                res[path] = (f["url"], int(modifiedTimeStamp))
            page += 1
        return folders, res

    def getCourseCode(self, courseID):
        url = f"{self.baseurl}/courses/{courseID}"
        logger.info(f"Retrieving course code at {url}.")
        return self.sessGet(url).json()['course_code']

    def getCourseID(self):
        res = {}
        page = 1
        if self.config.get('courseCodes'):
            lowerCourseCodes = [s.lower() for s in self.config['courseCodes']]
            while True:
                url = f"{self.baseurl}/courses?page={page}"
                courses = self.sessGet(url).json()
                if isinstance(courses, dict) and courses.get('errors'):
                    errMsg = courses['errors'][0].get('message',
                                                      'unknown error.')
                    print(f"\nError: {errMsg}")
                    logger.error(errMsg)
                    exit(1)
                if not courses:
                    break
                for course in courses:
                    if course.get('course_code',
                                  '').lower() in lowerCourseCodes:
                        res[course['id']] = course['course_code']
                        lowerCourseCodes.remove(
                            course.get('course_code', '').lower())
                page += 1
        if self.config.get('courseIDs'):
            for courseID in self.config['courseIDs']:
                res[courseID] = self.getCourseCode(courseID)
        return res

    def getCourseTaskInfo(self, courseID):
        
        folders, files = self.getCourseFiles(courseID)
        self.createFolders(courseID, folders)
        localFiles = self.getLocalFiles(courseID, folders)
        res = []
        
        for fileName, (fileUrl, fileModifiedTimeStamp) in files.items():
            if not fileUrl:
                continue
            if self.config['no_subfolder']:
                path = os.path.join(self.downloadDir, fileName[1:])
            else:
                path = os.path.join(self.downloadDir,
                                    f"{self.courseCode[courseID]}{fileName}")
            path = path.replace('\\', '/').replace('//', '/')
            
            if fileName in localFiles:
                localCreatedTimeStamp = int(os.path.getctime(path))
                if fileModifiedTimeStamp <= localCreatedTimeStamp:
                    continue
                response = self.sessHead(fileUrl)
                fileSize = int(response.headers.get('content-length', 0))
                self.laterDownloadSize += fileSize
                self.laterFiles.append((fileUrl, path))
                self.laterInfo.append(
                    f"{self.courseCode[courseID]}{fileName} ({round(fileSize / 2**20, 2)}MB)"
                )
                continue
            
            response = self.sessHead(fileUrl)
            fileSize = int(response.headers.get('content-length', 0))
            if fileSize / 2**20 > self.config['filesizeThresh']:
                if not self.confirmAll:
                    print(
                        f'\nTarget file: {self.courseCode[courseID]}{fileName} is too large ({round(fileSize / 2**20, 2)}MB), ignore?(Y/n) ',
                        end='')
                    isDownload = input()
                else:
                    print(
                        f'\nTarget file: {self.courseCode[courseID]}{fileName} is too large ({round(fileSize / 2**20, 2)}MB), ignore. '
                    )
                    isDownload = 'Y'
                
                if isDownload not in ['n', 'N']:
                    logger.warning(f"Ignoring file {self.courseCode[courseID]}{fileName} due to large size. Empty placeholder file created.")
                    print('Creating empty file as placeholder...')
                    open(path, 'w').close()
                    self.skipfiles.append(path)
                    continue

            self.newInfo.append(
                f"{self.courseCode[courseID]}{fileName} ({round(fileSize / 2**20, 2)}MB)"
            )
            self.downloadSize += fileSize
            res.append((fileUrl, path))
        return res

    def checkNewFiles(self):

        print("\rFinding files on Canvas...", end='')
        allInfos = []

        for courseID in self.courseCode.keys():
            for info in self.getCourseTaskInfo(courseID):
                allInfos.append(info)
        
        if len(allInfos) == 0:
            print("\rAll local files are up to date!")
            logger.info("All files up to date.")
        else:
            print(f"\rFound {len(allInfos)} new file(s)!           ")
            logger.info("Found {len(allInfos)} new file(s)!")
            
            if self.skipfiles:
                print(
                    f"The following file(s) will not be synced due to their size (over {self.config['filesizeThresh']} MB):"
                )
                [print(f) for f in self.skipfiles]
                logger.info(f"Skipped files {str(self.skipfiles)}")
            
            print(
                f"Start to download following file(s)! Total size: {round(self.downloadSize / 2**20, 2)}MB"
            )
            [print(s) for s in self.newInfo]
            logger.info(f"File downloading started. File(s) list: {str(self.newInfo)}")
            
            self.downloader.create(allInfos, self.downloadSize)
            self.downloader.start()
            self.downloader.waitTillFinish()

    def checkLaterFiles(self):

        '''Check for new versions of existing files on Canvas'''

        if not self.laterFiles:
            return

        print("These file(s) have later version on Canvas:")
        [print(s) for s in self.laterInfo]
        logger.info(f"Found new versions for files {self.laterInfo}")
        
        if not self.confirmAll:
            print('Update all?(Y/n) ', end='')
            isDownload = input()
        else:
            isDownload = 'Y'
        
        if isDownload in ['n', 'N']:
            logger.info("Update aborted.")
            return

        print(
            f"Start to download these files! Total size: {round(self.laterDownloadSize / 2**20, 2)}MB"
        )
        
        laterFiles = []
        
        for (fileUrl, path) in self.laterFiles:
            
            localCreatedTimeStamp = int(os.path.getctime(path))
            
            try:
                newPath = os.path.join(
                    ntpath.dirname(path),
                    f"{localCreatedTimeStamp}_{ntpath.basename(path)}")
                
                if not os.path.exists(newPath):
                    os.rename(path, newPath)
                else:
                    path = os.path.join(
                        ntpath.dirname(path),
                        f"{int(time.time())}_{ntpath.basename(path)}")
                laterFiles.append((fileUrl, path))
            
            except Exception as e:
                print(f"{e.__class__.__name__}! Skipped: {path}")
                logger.error(f"{e.__class__.__name__}! Skipped: {path}")
        
        self.downloader.create(laterFiles, self.laterDownloadSize)
        self.downloader.start()
        self.downloader.waitTillFinish()

    def sync(self):
        '''Main function for checking for new or updated versions of files.'''
        
        print("\rGetting course IDs...", end='')
        self.courseCode = self.getCourseID()
        logger.info(f"CanvasSyncer.courseCode = {str(self.courseCode)}")
        print(f"\rFound {len(self.courseCode)} available courses!")
        self.checkNewFiles()
        logger.info("Finished checking for new files.")
        self.checkLaterFiles()
        logger.info("Finished checking for new versions of existing files.")


def initConfig():

    '''Create new `.canvassyncer.json` file, with values to properties based on user input.'''

    oldConfig = dict()

    if os.path.exists(CONFIG_PATH):
        oldConfig = json.load(open(CONFIG_PATH))
    elif os.path.exists("./canvassyncer.json"):
        oldConfig = json.load(open("./canvassyncer.json"))
    print("Generating new config file...")

    url = input("Canvas URL (Default: https://umich.instructure.com):").strip()
    if not url:
        url = "https://umich.instructure.com"
    tipStr = f"(Default: {oldConfig.get('token', '')})" if oldConfig else ""

    token = input(f"Canvas access token{tipStr}:").strip()
    if not token:
        token = oldConfig.get('token', '')

    tipStr = f"(Default: {' '.join(oldConfig.get('courseCodes', list()))})" if oldConfig else ""
    courseCodes = input(
        f"Courses to sync in course codes(split with space){tipStr}:").strip(
        ).split()
    if not courseCodes:
        courseCodes = oldConfig.get('courseCodes', list())
    
    tipStr = f"(Default: {' '.join([str(item) for item in oldConfig.get('courseIDs', list())])})" if oldConfig else ""
    courseIDs = input(
        f"Courses to sync in course ID(split with space){tipStr}:").strip(
        ).split()
    if not courseIDs:
        courseIDs = oldConfig.get('courseIDs', list())
    courseIDs = [int(courseID) for courseID in courseIDs]
    
    tipStr = f"(Default: {oldConfig.get('downloadDir', os.path.abspath(''))})"
    downloadDir = input(f"Path to save Canvas files{tipStr}:").strip()

    if not downloadDir:
        downloadDir = oldConfig.get('downloadDir', os.path.abspath(''))

    tipStr = f"(Default: {oldConfig.get('filesizeThresh', '')})" if oldConfig else f"(Default: 250)"
    filesizeThresh = input(
        f"Maximum file size to download(MB){tipStr}:").strip()

    try:
        filesizeThresh = float(filesizeThresh)
    except:
        filesizeThresh = 250

    json.dump(
        {
            "canvasURL": url,
            "token": token,
            "courseCodes": courseCodes,
            "courseIDs": courseIDs,
            "downloadDir": downloadDir,
            "filesizeThresh": filesizeThresh
        }, open(CONFIG_PATH, mode='w', encoding='utf-8'), indent=4)


def run():

    ''' I really do not know whether this separate `run()` function is needed, but 
    I'd assume it's necessary for building a package and releasing it later on...'''

    Syncer, args = None, None

    try:

        parser = argparse.ArgumentParser(
            description='Utility to sync course files from Canvas')
        parser.add_argument('-r',
                            help='recreate config file',
                            action="store_true")
        parser.add_argument('-y',
                            help='confirm all yes/no prompts',
                            action="store_true")
        parser.add_argument('--no-subfolder',
                            help='does create subfolders named after course codes when synchronizing files',
                            action="store_true")
        parser.add_argument('-p',
                            '--path',
                            help='assign path for the JSON config file',
                            default=CONFIG_PATH)
        parser.add_argument('-x',
                            '--proxy',
                            help='download proxy',
                            default=None)
        parser.add_argument('-V',
                            '--version',
                            action='version',
                            version=__version__)
        parser.add_argument('-d',
                            '--debug',
                            help='show debug information',
                            action="store_true")

        args = parser.parse_args()
        configPath = args.path

        if args.r or not os.path.exists(configPath):

            if not os.path.exists(configPath):
                print('Config file does not exist, creating...')
                logger.warning(
                    f'Config file does not exist, creating new one at {configPath}')

            try:
                initConfig()

            except Exception as e:
                print(
                    f"\nError: {e.__class__.__name__}. Failed to create or read config file.")
                logger.critical("Failed to create or read config file", exc_info=True)
                if args.debug:
                    print(traceback.format_exc())
                exit(1)

            if args.r:
                return
        
        config = json.load(open(configPath, 'r', encoding='UTF-8'))
        config['y'] = args.y
        config['proxies'] = args.proxy
        config['no_subfolder'] = args.no_subfolder

        Syncer = CanvasSyncer(config)
        Syncer.sync()

    except ConnectionError as e:
        print("\nConnection Error. Please check your network and your token!")
        logger.critical("Connection error!", exc_info=True)
        if args.debug:
            print(traceback.format_exc())
        exit(1)

    except Exception as e:
        print(
            f"\nUnexpected Error: {e.__class__.__name__}. Please check your network and your token!")
        logger.critical("Unexpected error!", exc_info=True)
        if args.debug:
            print(traceback.format_exc())

    except KeyboardInterrupt as e:
        if Syncer:
            Syncer.downloader.stop()
        logger.critical(str(e))
        exit(1)


if __name__ == "__main__":
    try:
        logging.config.dictConfig(LOGGER_CONFIG)
        logger = logging.getLogger("root")
        logger.info("Program initiated.")
        logger.info(f"Path for json configuration: {CONFIG_PATH}")
    except Exception as e:
        print(e)
        print(
            "Logger failed to initiate! Please check existence and/or location of the config file")
    finally:
        run()
