# Download course content from NTU Learn

## Installing
```
git clone https://github.com/briyanii/ntu-learn-downloader.git
cd ntu-learn-downloader
python -m pip install selenium==4.38.0
```

## Usage
```
usage: download_files.py [-h] [--download-dir DOWNLOAD_DIR] [--email EMAIL]
                         [--password PASSWORD]
                         [--max-concurrent MAX_CONCURRENT]

options:
  -h, --help            show this help message and exit
  --download-dir DOWNLOAD_DIR
                        directory to download files to
  --email EMAIL         NTU email (e.g. bob1234@e.ntu.edu.sg)
  --password PASSWORD   NTU email password
  --max-concurrent MAX_CONCURRENT
                        Maximum number of workers used when downloading
```

## What the script does
1. Prompt you for your email & password, unless you have modified the script
2. Signs in to NTU Learn with you provided credentials
2. Extract your open courses from `https://ntulearn.ntu.edu.sg/ultra/course`
3. Prompt you to choose a course to download files from
4. Extracts content folder structure from `https://ntulearn.ntu.edu.sg/webapps/blackboard/content/courseMenu.jsp?course_id={course_idd}&newWindow=true&openInParentWindow=true`
5. Extracts attachment URLS from those content folder sections
6. Downloads all the files to `<DOWNLOAD_DIR>/<COURSE_NAME>_<TIME>.zip`

## Config
Credentials and download directory can be hard coded into the script.
Search for the section below (around line 22) and update the values
```
# ================== HARDCODED CONFIG / CREDENTIALS ================
# NTU email (e.g. bob1234@e.ntu.edu.sg)
EMAIL = None

# Location on disk to download .zip folders to
DOWNLOAD_DIR = os_path.expand_user('~/Downloads')

# It is bad practice to save passwords in plain-text
# do so at your own risk
PASSWORD = None

# maximum threads for concurrent downloads
MAX_WORKERS = 8
```

## Help
- If the script crashes due to webdriver timeout or stale element, try running the script again.

## References
I was trying to download files from NTU Learn without having to manually click each link and found this repository: `https://github.com/wilsonteng97/NTULearn-Blackboard-Downloader` which no longer works as the authentication process has changed.
