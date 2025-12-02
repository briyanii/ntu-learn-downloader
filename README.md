# Download course content from NTU Learn

## Installing
```
git clone https://github.com/briyanii/ntu-learn-downloader.git
cd ntu-learn-downloader
python -m pip install selenium==4.38.0
```

## Usage
```
python download_files.py --help
usage: download_files.py [-h] [--download-dir DOWNLOAD_DIR] [--email EMAIL] [--password PASSWORD]
                         [--max-concurrent MAX_CONCURRENT] [--use-ffmpeg] [--ffmpeg-path FFMPEG_PATH]

options:
  -h, --help            show this help message and exit
  --download-dir DOWNLOAD_DIR
                        directory to download files to
  --email EMAIL         NTU email (e.g. bob1234@e.ntu.edu.sg)
  --password PASSWORD   NTU email password
  --max-concurrent MAX_CONCURRENT
                        Maximum number of workers used when downloading attachments
  --use-ffmpeg          Set this flag to indicate that the script should use ffmpeg to convert .m3u8
                        playlist to .mp4. Requires "ffmpeg" to be installed.
  --ffmpeg-path FFMPEG_PATH
                        Path to ffmpeg
```
## Config
Credentials and download directory can be hard coded into the script by modifying the `config.py` file.
* `DOWNLOAD_DIR`: set this to the location you want to download the .zip files to. `~/Downloads` by default.
* `EMAIL` and `PASSWORD`: your credentials
* `FFMPEG_PATH`: path to ffmpeg, "ffmpeg" by default

## User Credentials
The script provides 3 ways to input your password. 
1. Entering it upon being prompted by the script
2. Passing it as a command line argument with the `--password` flag
3. Hard coding it into the `config.py` file

> Using the `--password` flag or hard coding your password into the `config.py` file will expose your password in plain-text. This is considered bad practice in terms of security. You may choose to do so at your own convenience, but understand that doing so can be risky, especially on a shared device.

## What the script does
1. Prompt you for your email & password, unless you have modified the script
2. Signs in to NTU Learn with you provided credentials
2. Extract your open courses from `https://ntulearn.ntu.edu.sg/ultra/course`
3. Prompt you to choose a course to download files from
4. Extracts content folder structure from `https://ntulearn.ntu.edu.sg/webapps/blackboard/content/courseMenu.jsp?course_id={course_id}&newWindow=true&openInParentWindow=true`
5. Extracts attachment URLS from those content folder sections
6. Extracts media links from Course Media page
7. Starts playing each video to trigger request for HLS stream / .m3u8 files
8. Retrieves .m3u8 file containing `#EXT-X-STREAM-INF` from network responses using Chrome Devtools Protocol
9. Downloads all the files to `<DOWNLOAD_DIR>/<COURSE_NAME>-<RANDOM_UUID>.zip`

## How to play .m3u8 files
* Open file in browser `file://<PATH_TO_M3U8_FILE>`
* Use a video player like VLC
* ffmpeg
* ...?

## How to convert the .m3u8 files to mp4 manually.

`path/to/stream` is the URI under the #EXT-X-STREAM-INF headers
`path/to/subtitles` is the URI on the #EXT-X-MEDIA line

```
ffmpeg \
    -i path/to/stream \
    -i path/to/subtitles \
    -map 0:v \
    -map 0:a \
    -map 1:s \
    -c:v copy \
    -c:a copy \
    -c:s mov_text \
    path/to/output.mp4
```

## Help
- If the script crashes due to webdriver timeout or stale element, try running the script again.

## References
I was trying to download files from NTU Learn without having to manually click each link and found this repository: `https://github.com/wilsonteng97/NTULearn-Blackboard-Downloader` which no longer works as the authentication process has changed.
