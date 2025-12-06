#!/bin/python3
# ==================== IMPORTS ===========================
import json
import shutil
import uuid
import re
import base64
import logging
import time
import zipfile
import tempfile
import getpass
import argparse
import subprocess
import os

from collections import Counter
from threading import Lock
from requests import Session as RequestsSession
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as wait_for_futures

# ==================== IMPORTS REQURING PIP INSTALL ===========================
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver import Chrome as ChromeWebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By

# =========== IMPORT FROM OTHER SCRIPT ==============
import config

# ===================== OTHER CONSTANTS ==============
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36"
WINDOW_W = 1920
WINDOW_H = 1080

# ===================== CSS SELECTORS ==============
SSO_FORM_SELECTOR = 'form[action*="https://login.microsoftonline.com"]'

EMAIL_INPUT_SELECTOR = 'input[type="email"]'
PASSWORD_INPUT_SELECTOR = 'input[type="password"]'
NEXT_INPUT_SELECTOR = 'input[type="submit"][value="Next"]'
SIGNIN_INPUT_SELECTOR = 'input[type="submit"][value="Sign in"]'
YES_INPUT_SELECTOR = 'input[type="submit"][value="Yes"]'

COURSE_LIST_MANAGEMENT_CONTAINER_SELECTOR = '.course-overview-management-container'
COURSE_LIST_FILTER_DELETE_SELECTOR = '.course-overview-management-container [class*="makeStyleschipContainer"] button[aria-label="delete"]'
COURSE_LIST_ITEMS_PER_PAGE_BUTTON_SELECTOR = '.course-overview-management-container button[aria-label*="items per page"]'
COURSE_LIST_ITEM_PER_PAGE_OPTION = '.course-overview-management-container #page-selector-menu li[role="menuitem"]'
COURSE_LIST_SELECTOR = '#main-content-inner'
COURSE_CARD_ID_SELECTOR = '.course-id' 
COURSE_CARD_TITLE_SELECTOR = '.course-title .js-course-title-element'
COURSE_CARD_STATUS_SELECTOR = '.course-status'
COURSE_CARD_SELECTOR = 'bb-base-course-card article'

CONTENT_TREE_ITEM_SELECTOR = 'li[id*="Link$ReferredToType:CONTENT"]'
CONTENT_TREE_ITEM_LINK_SELECTOR = 'a[href][title][target="content"]'
CONTENT_FOLDER_ATTACHMENT_SELECTOR = '[id="contentListItem:{0}"] .attachments li'
CONTENT_FOLDER_ATTACHMENT_LINK_SELECTOR = 'a[href*="/bbcswebdav"]'

IFRAME_SELECTOR_TEMPLATE = 'iframe[src*="{0}"]'

CONTENT_TREE_COURSE_MEDIA_LINK_SELECTOR = (
    'li[id*="Link$ReferredToType:TOOL"] '
    'a[title="Course Media"][target="content"]'
)
COURSE_MEDIA_THUMBNAIL_SELECTOR = '#galleryGrid .thumbnail'
COURSE_MEDIA_THUMBNAIL_NAME_SELECTOR = 'p.thumb_name_content'
COURSE_MEDIA_THUMBNAIL_LINK_SELECTOR = 'a.item_link[href]'

KALTURA_PLAYER_SELECTOR = '#kplayer'
KALTURA_PLAY_BUTTON_SELECTOR = '#kplayer button[aria-label="Play"]'

BODY_SELECTOR = 'body'
# ==================== LOGGING ===========================
logger = logging.getLogger('ntu-learn-downloader')
logger.setLevel(logging.INFO)

handler = logging.StreamHandler()
formatter = logging.Formatter('%(levelname)s - %(name)s - %(message)s')

handler.setFormatter(formatter)
logger.addHandler(handler)
# ==================== CODE ===========================

def clean_filename(name):
    name = re.sub(r'[^a-zA-Z-_0-9.]+', '_', name).strip('_')
    return name

class M3U8:
    STREAM_INFO_PREFIX = '#EXT-X-STREAM-INF'
    MEDIA_PREFIX = '#EXT-X-MEDIA'

    @staticmethod
    def split_m3u8(text):
        delim = '#EXT'
        parts = re.split(r'(#EXT)', text)
        chunk = []
        for part in parts:
            if part == delim:
                chunk = ''.join(chunk)
                chunk = chunk.strip()
                if chunk:
                    yield chunk
                chunk = [part]
            else:
                chunk.append(part)
        
        chunk = ''.join(chunk)
        chunk = chunk.strip()
        if chunk:
            yield chunk
        
    @staticmethod
    def parse_kv_string(text):
        try:
            parsed = {}
            for kv in text.split(','):
                # text may include '='
                # ensure that we only split for the first '='
                k,v = kv.split('=', 1)
                v = v.strip('"')
                parsed[k] = v
            return parsed
        except Exception as e:
            logger.error(
                f'Error occured while parsing key-value string {text}'
            )
            raise e

    @staticmethod
    def parse_m3u8_chunk(chunk_text):
        try:
            if chunk_text == '#EXTM3U':
                # header
                return None

            _, chunk_type, chunk_kv_string = re.split(
                r'(#EXT[-A-Z]+):', chunk_text)
            if chunk_type == M3U8.STREAM_INFO_PREFIX:
                chunk_kv_string, uri = chunk_kv_string.split('\n')
                parsed = M3U8.parse_kv_string(chunk_kv_string)
                parsed['URI'] = uri
                parsed['chunk_type'] = 'stream_info'
            elif chunk_type == M3U8.MEDIA_PREFIX:
                parsed = M3U8.parse_kv_string(chunk_kv_string)
                parsed['chunk_type'] = 'media'
            else:
                logger.warning(f'No parser implemented for {chunk_type}')
                return None
            
            return parsed
        except Exception as e:
            logger.error(f"Exception occured while parsing m3u8 chunk:\n{e}")
            raise e

    @staticmethod
    def parse_m3u8(text):
        streams = []
        subtitles = []

        for chunk in M3U8.split_m3u8(text):
            parsed = M3U8.parse_m3u8_chunk(chunk)
            if parsed is None:
                continue
            parsed['original_m3u8_text'] = chunk
            chunk_type = parsed['chunk_type']
            if (
                chunk_type == 'media'
            ) and (
                parsed['TYPE'] == 'SUBTITLES'
            ):
                subtitles.append(parsed)
            elif chunk_type == 'stream_info':
                streams.append(parsed)

        # sort streams by quality (in case they are not already)
        streams = sorted(
            streams,
            key=lambda x: -int(x['BANDWIDTH'])
        )
        for stream in streams:
            stream['subtitles'] = list(filter(
                lambda x: x['GROUP-ID'] == stream['SUBTITLES'],
                subtitles
            ))
        
        return {
            'streams': streams,
        }
    
class Element:
    @staticmethod
    def get_bounding_rect(driver, elem):
        rect = driver.execute_script('return arguments[0].getBoundingClientRect()', elem)
        return rect

    @staticmethod
    def scroll_by(driver, elem, x=0, y=0):
        driver.execute_script('arguments[0].scrollBy(arguments[1], arguments[2])', elem, x, y)

    @staticmethod
    def set_attribute(driver, elem, key: str, val: str):
        driver.execute_script(
            'arguments[0].setAttribute(arguments[1], arguments[2]);',
            elem, key, str(val),
        )

    @staticmethod
    def toggle_attribute(driver, elem, key: str, val: bool):
        driver.execute_script(
            'arguments[0].toggleAttribute(arguments[1], arguments[2]);',
            elem, key, bool(val),
        )

    @staticmethod
    def get_parent(driver, elem):
        return driver.execute_script(
            'return arguments[0].parentElement;',
            elem
        )

    @staticmethod
    def has_attribute(driver, elem, key: str):
        return driver.execute_script(
            'return arguments[0].hasAttribute(arguments[1]);',
            elem, key
        )

class Condition:
    @staticmethod
    def url_is_any(*urls):
        url_set = set(urls)
        def condition(driver):
            current_url = driver.current_url
            if current_url in url_set:
                return current_url
        return condition

    @staticmethod
    def url_contains_any(*queries):
        def condition(driver):
            current_url = driver.current_url
            for i, query in enumerate(queries):
                if query in current_url:
                    return current_url
        return condition

    @staticmethod
    def contains_text(elem, text):
        def condition(driver):
            in_text = text in elem.text
            return in_text
        return condition
    
    @staticmethod
    def course_cards_are_complete(course_cards):
        incomplete_set = set(range(len(course_cards)))
        def condition(driver):
            values = list(incomplete_set)
            for i in values:
                card_info = NTULearnClient.course_card_to_info(
                    course_cards[i]
                )
                is_complete = True
                for v in card_info.values():
                    if len(v.strip()) == 0:
                        is_complete = False
                        break
                if is_complete:
                    incomplete_set.remove(i)
            return len(incomplete_set) == 0
        return condition

class Credentials:
    def __init__(self, email=None, password=None, path=None):
        self.email = email
        self.password = password
    
    def get_email(self):
        if self.email is None:
            email = input("Enter NTU email: ")
            return email
        return self.email
    
    def get_password(self):
        if self.password is None:
            password = getpass.getpass('Enter NTU email password: ')
            return password
        return self.password

class StatefulKalturaResponseHistoryFilter:
    def __init__(self):
        self.filter_start_time = time.time()
        self.latest_time_seen = -1000
        self.filter_end_time = float('inf')
    
    def prepare_for_use(self):
        self.filter_start_time = max(
            self.filter_start_time, 
            self.latest_time_seen
        )
        self.filter_end_time = time.time()

    def __call__(self, x):
        if x['time'] <= self.filter_start_time:
            return False
        elif x['time'] >= self.filter_end_time:
            return False
        if x['mimeType'] not in {
            'application/x-mpegurl',
            'application/vnd.apple.mpegurl',
        }:
            return False
        
        self.latest_time_seen = max(self.latest_time_seen, x['time'])
        return True


class NTULearnClient:
    BASE_URL = 'https://ntulearn.ntu.edu.sg'
    SSO_LOGIN_BASE_URL = 'https://login.microsoftonline.com'
    
    HOME_PAGE = BASE_URL + '/ultra/institution-page'
    COURSES_PAGE = BASE_URL + '/ultra/course'
    COURSE_CONTENT_TREE_TEMPLATE = BASE_URL + (
        '/webapps/blackboard/content/courseMenu.jsp'
        '?course_id={0}&newWindow=true&openInParentWindow=true'
    )

    def __init__(self, credentials):
        self.credentials = credentials
        options = Options()
        options.add_argument('--headless=new')
        options.add_argument(f"--window-size={WINDOW_W},{WINDOW_H}")
        options.add_argument(f'user-agent={USER_AGENT}')
        options.set_capability('goog:loggingPrefs', {
            'performance': 'ALL',
        })
        self.driver = ChromeWebDriver(
            options=options
        )
        self.driver.execute_cdp_cmd("Network.enable", {})
        self.driver.execute_cdp_cmd("Page.enable", {})

        self.log_thread_executor = ThreadPoolExecutor(max_workers=1)
        self.log_thread_executor.submit(self.log_watcher_loop)
        self.link_history_lock = Lock()
        self.link_history = []
        
        self.response_history_lock = Lock()
        self.response_history = []

    def log_watcher_loop(self):
        try:
            while True:
                for entry in self.driver.get_log('performance'):
                    message = json.loads(entry['message'])['message']
                    method = message['method']
                    # track frame navigation log events
                    try:
                        if method == 'Page.frameNavigated':
                            url = message['params']['frame']['url']
                            with self.link_history_lock:
                                self.link_history.append({
                                    'url': url,
                                    'time': time.time(),
                                })
                            logger.debug(f'Navigated to: {url}')
                    
                        elif method == 'Network.responseReceived':
                            resp = message['params']['response']
                            request_id = message['params']['requestId']
                            url = resp.get('url', '')
                            status = resp.get('status')
                            mimetype = resp.get('mimeType')
                            
                            with self.response_history_lock:
                                self.response_history.append({
                                    'request_id': request_id,
                                    'url': url,
                                    'mimeType': mimetype,
                                    'status': status,
                                    'time': time.time(),
                                })
                    except Exception as e:
                        logger.error(f'Failed to parse {method}')
                        print(f"Error while parsing {method}")

            time.sleep(0.2)
        except Exception as e:
            logger.error(f'Log watcher failed:\n{e}')

    def goto_home(self):
        logger.info(f'Navigate to {self.HOME_PAGE}')
        self.driver.get(self.HOME_PAGE)
        self.wait_for_page_or_signin(self.HOME_PAGE)
    
    def signin(self):
        email = self.credentials.get_email()
        password = self.credentials.get_password()
        print('Signing in with provided credentials')
        self.wait_for_input_then_send_keys(
                EMAIL_INPUT_SELECTOR, email)
        self.wait_for_input_then_send_keys(
                PASSWORD_INPUT_SELECTOR, password)
        self.wait_for_button_presence_then_click(NEXT_INPUT_SELECTOR)
        self.wait_for_button_presence_then_click(SIGNIN_INPUT_SELECTOR)
        self.wait_for_button_presence_then_click(YES_INPUT_SELECTOR)

    @staticmethod
    def course_card_to_info(card):
        course_id = card.get_attribute('id').split('-')[-1]
        card_info = {'course_id': course_id}
        
        for key, selector in [
            ('short_name', COURSE_CARD_ID_SELECTOR),
            ('long_name', COURSE_CARD_TITLE_SELECTOR),
            ('status', COURSE_CARD_STATUS_SELECTOR)
        ]:
            info_elem = card.find_element(By.CSS_SELECTOR, selector)
            text = info_elem.get_attribute('textContent').strip()
            card_info[key] = text

        return card_info

    @staticmethod
    def course_cards_to_info(cards):
        return [
            NTULearnClient.course_card_to_info(card) 
            for card in cards
        ]
    
    def enumerate_course_media(self, course_info, timeout=10):
        driver = self.driver
        self.goto_course_content_tree(course_info)
        
        # get course media link
        link = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((
                By.CSS_SELECTOR,
                CONTENT_TREE_COURSE_MEDIA_LINK_SELECTOR
            ))
        )
        href = link.get_attribute('href')
        # navigate to course media
        # this will open an iframe
        driver.get(href)
        iframe = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((
                By.CSS_SELECTOR,        
                'iframe'
                #IFRAME_SELECTOR_TEMPLATE.format(href)
            ))
        )
        driver.switch_to.frame(iframe)

        # find gallery thumbnails
        thumbnails = WebDriverWait(driver, timeout).until(
            EC.presence_of_all_elements_located((
                By.CSS_SELECTOR,
                COURSE_MEDIA_THUMBNAIL_SELECTOR
            ))
        )

        media_infos = []
        for thumbnail in thumbnails:
            media_name = thumbnail.find_element(
                By.CSS_SELECTOR,
                COURSE_MEDIA_THUMBNAIL_NAME_SELECTOR
            )
            media_name = media_name.text.strip()
            media_link = thumbnail.find_element(
                By.CSS_SELECTOR,
                COURSE_MEDIA_THUMBNAIL_LINK_SELECTOR
            )
            href = medi_link = media_link.get_attribute('href')
            media_infos.append({
                'short_name': course_info['short_name'],
                'name': media_name,
                'href': href
            })
        msg = f'Found {len(media_infos)} media items'
        logger.info(msg)
        driver.switch_to.default_content()
        return media_infos
    
    def extract_playlists_from_media_infos(self, media_infos):
        playlists = [
            self.extract_m3u8_playlist(x)
            for x in media_infos if x is not None
        ]
        playlists = list(filter(lambda x: x is not None, playlists))
        return playlists

    def enumerate_courses(self, timeout=10):
        logger.info(f'Navigate to {self.COURSES_PAGE}')
        driver = self.driver
        driver.get(self.COURSES_PAGE)
        self.wait_for_page_or_signin(self.COURSES_PAGE)
        # wait for presence of mangement container element
        # this should indicate that the page has loaded
        WebDriverWait(driver, timeout).until(
            EC.visibility_of_element_located((
                By.CSS_SELECTOR, 
                COURSE_LIST_MANAGEMENT_CONTAINER_SELECTOR,
            ))
        )

        print("Extracting course list")
        self.show_maximum_course_cards()
        self.disable_course_filters_if_any()
        course_cards = self.wait_for_cards_to_load()
        courses_info = NTULearnClient.course_cards_to_info(course_cards)
        open_courses_info = list(filter(
            lambda x: x['status'] == 'Open', 
            courses_info
        ))
        return open_courses_info

    def show_maximum_course_cards(self, timeout=5):
        driver = self.driver

        menu = driver.find_element(
            By.CSS_SELECTOR, 
            COURSE_LIST_ITEMS_PER_PAGE_BUTTON_SELECTOR
        )
        print(menu)
        print(menu.is_displayed())
        menu = WebDriverWait(driver, timeout).until(
            #EC.element_to_be_clickable((
            #EC.visibility_of_element_located((
            EC.presence_of_element_located((
                By.CSS_SELECTOR, 
                COURSE_LIST_ITEMS_PER_PAGE_BUTTON_SELECTOR
            ))
        )
        print(menu)
        print(menu.is_displayed())
        # click page item count menu to show dropdown
        menu.click()
        
        # find option buttons
        options = WebDriverWait(driver, timeout).until(
            EC.visibility_of_all_elements_located((
                By.CSS_SELECTOR, 
                COURSE_LIST_ITEM_PER_PAGE_OPTION
            ))
        )

        # find option with maximum value
        max_count = 0
        max_count_button = None
        for option in options:
            value = option.get_attribute("value");
            try:
                count = int(value)
            except Exception as e:
                logger.error(
                    f"Found non-integer page item count option: {value}"
                )
                continue
            if count > max_count:
                max_count = count
                max_count_button = option

        # select maximum value option
        if max_count > 0:
            print(f"Setting page items to {max_count}")
            max_count_button.click()

    def disable_course_filters_if_any(self, timeout=5):
        driver = self.driver
        chips = driver.find_elements(
            By.CSS_SELECTOR, 
            COURSE_LIST_FILTER_DELETE_SELECTOR
        )
        print(f"Disabling {len(chips)}(?) filters")
        disable_count = 0
        for del_chip in chips:
            try:
                del_chip.click()
                disable_count += 1
            except StaleElementReferenceException:
                # certain delete buttons cause other buttons to be removed
                # e.g. the delete button in the text input area deletes the chips too 
                pass
        return disable_count

    def wait_for_cards_to_load(self, timeout=10):
        driver = self.driver
        driver.implicitly_wait(1)
        course_cards = driver.find_elements(
            By.CSS_SELECTOR, 
            COURSE_CARD_SELECTOR
        )
        scrollable_course_list = driver.find_element(
            By.CSS_SELECTOR, 
            COURSE_LIST_SELECTOR
        )
        container_rect = Element.get_bounding_rect(
            driver,
            scrollable_course_list
        )

        height = driver.execute_script('return window.innerHeight');
        offset = 0
        while offset < len(course_cards):
            # count elements in viewport
            n_in_viewport = 0
            has_remaining = False
            for course_card in course_cards[offset:]:
                item_rect = Element.get_bounding_rect(driver, course_card)
                is_in_viewport = (
                    0 < item_rect['bottom'] < height
                ) or (
                    0 < item_rect['top'] < height
                )
                if is_in_viewport:
                    n_in_viewport += 1
                else:
                    has_remaining = True
                    break

            cards_in_view = course_cards[offset:offset+n_in_viewport]
            # course card needs to be in viewport for information to load
            # wait for course info to be loaded
            WebDriverWait(driver, timeout).until(
                Condition.course_cards_are_complete(cards_in_view)
            )
            offset = offset + n_in_viewport
            logger.info(f'Course cards loaded: {offset}/{len(course_cards)}')
            
            if has_remaining:
                next_rect = Element.get_bounding_rect(
                    driver, course_cards[offset]
                )
                scroll_h = next_rect['top']
                Element.scroll_by(
                    driver, scrollable_course_list, x=0, y=scroll_h
                )

        return course_cards

    def wait_for_page(self, page, timeout=10):
        driver = self.driver
        res = WebDriverWait(driver, timeout).until(
            Condition.url_is_any(page)
        )
        
    def wait_for_page_or_signin(self, page, timeout=10):
        driver = self.driver
        res1 = WebDriverWait(driver, timeout).until(EC.any_of(
            # returns element
            EC.presence_of_element_located((By.CSS_SELECTOR, SSO_FORM_SELECTOR)),
            # returns page url
            Condition.url_is_any(page), 
        ))
        if not isinstance(res1, str):
            self.signin()

        res2 = WebDriverWait(driver, timeout).until(
            Condition.url_is_any(page), 
        )
        if isinstance(res1, str):
            print('Authenticated!')

    def wait_for_input_then_send_keys(self, css_selector, keys, timeout=10):
        driver = self.driver
        input_el = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, css_selector)))
        input_el.send_keys(keys)

    def wait_for_button_presence_then_click(self, css_selector, timeout=10):
        driver = self.driver
        btn_el = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, css_selector)))
        btn_el.click()

    def goto_course_content_tree(self, course_info, timeout=10):
        driver = self.driver
        course_id = course_info['course_id']
        path = NTULearnClient.COURSE_CONTENT_TREE_TEMPLATE.format(
            course_id
        )
        driver.get(path)

    def enumerate_content_folders(self, course_info, timeout=10):
        self.goto_course_content_tree(course_info)

        driver = self.driver
        course_id = course_info['course_id']
        contents = WebDriverWait(driver, timeout).until(
            EC.presence_of_all_elements_located((
                By.CSS_SELECTOR, CONTENT_TREE_ITEM_SELECTOR 
            ))
        )
        
        folders = []
        counter = Counter()
        for elem_idx, elem in enumerate(contents):
            # initialize folder
            content_id = elem.get_attribute('id').split(':::')[1]
            elem_link = elem.find_element(
                By.CSS_SELECTOR, CONTENT_TREE_ITEM_LINK_SELECTOR
            )
            filename = elem_link.get_attribute('title').strip()
            filename = clean_filename(filename)
            href = elem_link.get_attribute('href')
            folders.append({
                'content_id': content_id,
                'course_id': course_id,
                'course_short_name': course_info['short_name'],
                'course_long_name': course_info['long_name'],
                'filename': filename,
                'href': href,
            })
            
            Element.set_attribute(
                driver, elem, 'data-tree-filename', filename
            )
            Element.toggle_attribute(
                driver, elem, 'data-tree-leaf-folder', True,
            )

        # when we traverse up parents, collect the index
        body = driver.find_element(
            By.CSS_SELECTOR, BODY_SELECTOR
        )
        

        counter = Counter()
        for elem_idx, elem in enumerate(contents):
            parent = elem
            parts = []
            while parent.id != body.id:
                counter[parent.id] += 1
                filename = parent.get_attribute('data-tree-filename')
                if parent.id != elem.id:
                    Element.toggle_attribute(
                        driver, parent, 'data-tree-leaf-folder', False
                    )
                if filename:
                    parts.append(filename)
                parent = Element.get_parent(driver, parent);
            path_parts = list(reversed(parts))
            filepath = os.path.join(*path_parts)
            folders[elem_idx]['filepath'] = filepath

        leaf_folders = [] 
        for elem_idx, elem in enumerate(contents):
            if counter[elem.id] == 1:
                leaf_folders.append(folders[elem_idx])

        return leaf_folders
    
    def enumerate_attachments_for_course(self, course_info):
        folders = self.enumerate_content_folders(course_info)
        attachments = self.enumerate_attachments_for_folders(folders)
        return attachments

    def enumerate_attachments_for_folders(self, folders, timeout=1):
        all_attachments = []
        for folder in folders:
            folder_attachments = self.enumerate_attachments_for_folder(
                    folder, timeout=timeout)
            all_attachments.extend(folder_attachments)
        n = len(all_attachments)
        logger.info(f"Found {n} attachments");
        print(f'Found {n} attachments')
        return all_attachments

    def enumerate_attachments_for_folder(self, folder, timeout=5):
        driver = self.driver
        content_id = folder['content_id']
        course_id = folder['course_id']
        selector = CONTENT_FOLDER_ATTACHMENT_SELECTOR.format(content_id)
        href = folder['href']

        driver.get(href)
        logger.debug(f'{selector} for {href}')
        try:
            attachment_elems = WebDriverWait(driver, timeout=timeout).until(
                EC.presence_of_all_elements_located((
                    By.CSS_SELECTOR, 
                    selector,
                ))
            )
            driver.implicitly_wait(1)
        except Exception as e: 
            logger.warning(
                "Unable to locate any attachments "
                f"for course:{course_id} content:{content_id} "
            )
            return []
        
        attachment_infos = []
        for a_idx, attachment_elem in enumerate(attachment_elems):
            link_elem = attachment_elem.find_element(
                By.CSS_SELECTOR, 
                CONTENT_FOLDER_ATTACHMENT_LINK_SELECTOR
            )
            try:
                m = re.match(r'(.*?)\s+\(([^\)]+)\)', attachment_elem.text)
                displayed_filename, displayed_filesize = m.groups()
                displayed_filename = displayed_filename.strip()
                displayed_filesize = displayed_filesize.strip()
                cleaned_filename = clean_filename(displayed_filename) 
                filepath = os.path.join(folder['filepath'], cleaned_filename)
                href = link_elem.get_attribute('href')

                attachment_info = {
                    'attachment': {
                        'href': href,
                    },
                    'filepath': filepath,
                }
                attachment_infos.append(attachment_info)
            except Exception as e:
                logger.warning(
                    "Unable to retrieve attachment information for "
                    f"course:{course_id} content:{content_id} attachment:{a_idx}"
                )
        return attachment_infos
    
    def get_cookies(self):
        cookies = self.driver.get_cookies()
        cookies = {c['name']:c['value'] for c in cookies}
        return cookies
    
    def close(self):
        self.driver.quit()
        self.log_thread_executor.shutdown(wait=False)
    
    def extract_m3u8_playlist(self, media_info, timeout=10):
        driver = self.driver
        start_time = time.time()
        

        filter_func = StatefulKalturaResponseHistoryFilter()
        filter_func.prepare_for_use()

        def get_responses():
            with self.response_history_lock:
                kaltura_m3u8s = list(filter(
                    filter_func, self.response_history))
            filter_func.prepare_for_use()
            return kaltura_m3u8s

        #filter_func.prepare_for_use()
        name = media_info['name']
        logger.info(
            f'Extracting .m3u8 file from network responses for {name}'
        )
        driver.get(media_info['href'])
        player = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((
                By.CSS_SELECTOR,
                KALTURA_PLAYER_SELECTOR
            ))
        )

        player.click()
        m3u8_infos = []
        seen = set()
        for i in range(4): 
            kaltura_m3u8s = get_responses()

            for x in kaltura_m3u8s:
                request_id = x['request_id']
                if request_id in seen:
                    continue
                seen.add(request_id)
                try:
                    body = driver.execute_cdp_cmd("Network.getResponseBody", {
                        "requestId": request_id
                    })
                    if body.get('base64Encoded') == True:
                        body = base64.b64decode(body['body'])
                        body = body.decode('utf-8')
                    else:
                        body = body['body']
                    
                    if M3U8.STREAM_INFO_PREFIX in body:
                        # this file is the main manifest file
                        filename = clean_filename(media_info['name'])
                        filename = f'{filename}.m3u8'
                        filepath = os.path.join('media', filename)
                        logger.info(f'Extracted {filename}')
                        return {
                            'playlist': {
                                'body': body,
                            },
                            'filepath': filepath,
                        }

                except Exception as e:
                    logger.error(
                        f'Error occured while extracting .m3u8:\n{e}'
                    )
            time.sleep(.5)
        logger.warning('Did not find .m3u8')

class ThreadSharedZipFile(zipfile.ZipFile):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._thread_shared_zf_lock = Lock()
    
    def writestr_with_lock(self, arcpath, content):
        success = False
        with self._thread_shared_zf_lock:
            super().writestr(arcpath, content)
            success = True
        
        return success

    def write_with_lock(self, src_path, arcpath):
        success = False
        with self._thread_shared_zf_lock:
            super().write(src_path, arcname=arcpath)
            success = True
        return success

class Downloader:
    def __init__(self, 
                 cookies={}, 
                 max_workers=config.MAX_WORKERS, 
                 download_dir=config.DOWNLOAD_DIR,
                 ffmepg_path=config.FFMPEG_PATH,
                 temp_dir=None, 
                 ):
        self.cookies = cookies
        self.max_workers = max_workers
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers
        )
        self.ffmpeg_executor = ThreadPoolExecutor(
            max_workers=1,
        )
        self.ffmpeg_path = ffmpeg_path
        self.download_dir = download_dir
        self.temp_dir = temp_dir

    def download_content(self, idx, download_info, zf: ThreadSharedZipFile):
        def done_callback(future):
            download_info, error = future.result()
            filepath = download_info['filepath']
            if error:
                msg = (
                    'Error occured while downloading '
                    f'{filepath}:\n{error}'
                )
                print(msg)
                logger.error(msg)
            else:
                print(f'Successfully downloaded {filepath}')
            
        if 'playlist' in download_info:
            future = self.executor.submit(
                self.download_playlist,
                idx,
                download_info,
                zf,
            )
            future.add_done_callback(done_callback)
            return [future]
        elif 'playlist_as_mp4' in download_info:
            future = self.ffmpeg_executor.submit(
                self.download_playlist_as_mp4,
                idx,
                download_info,
                zf,
            )
            future.add_done_callback(done_callback)
            return [future]
        elif 'attachment' in download_info:
            future = self.executor.submit(
                self.download_attachment,
                idx,
                download_info,
                zf,
            )
            future.add_done_callback(done_callback)
            return [future]
        else:
            return []

    def download_playlist(self, idx, download_info, zf):
        try: 
            filepath = download_info['filepath']
            print(f'Downloading {filepath}')
            playlist_info = download_info['playlist']
            content = playlist_info['body']
            zf.writestr_with_lock(filepath, content)
            return download_info, None
        except Exception as e:
            return download_info, e

    def download_playlist_as_mp4(self, idx, download_info, zf):
        try: 
            filepath = download_info['filepath']
            print(f'Downloading {filepath}')

            playlist_info = download_info['playlist_as_mp4']
            parsed = M3U8.parse_m3u8(playlist_info['body'])

            # select stream 0 (highest bandwidth stream)
            stream = parsed['streams'][0]

            # build ffmpeg command
            cmd = [self.ffmpeg_path]
            cmd.extend([
                '-i', stream['URI'],
            ])
            for sub in stream['subtitles']:
                cmd.extend([
                    '-i', sub['URI'],
                ])

            # single AV stream
            cmd.extend([
                '-map', '0:v',
                '-map', '0:a',
            ])

            # multi sub track
            for sub_idx, sub in enumerate(stream['subtitles']):
                cmd.extend([
                    '-map', f'{sub_idx+1}:s',
                ])
            cmd.extend([
                '-c:v', 'copy',
                '-c:a', 'copy',
            ])
            if len(stream['subtitles']) > 0:
                cmd.extend([
                    '-c:s', 'mov_text',
                ])
        
            random_name = str(uuid.uuid4()) + '.mp4'
            outpath = os.path.join(self.temp_dir, random_name)
            cmd.append(outpath)
                
            p = subprocess.Popen(cmd, text=True, stderr=subprocess.PIPE)
            duration = None
            n_opened = 0
            for line in p.stderr:
                line = line.strip()
                if 'Duration:' in line and duration is None:
                    m = re.search(r'Duration: ([0-9:.]+)', line)
                    duration = m.group(0)
                elif 'time=' in line:
                    m = re.search(r'time=([0-9:.]+)', line)
                    time = m.group(0)
                    msg = (
                        f'Progress: {time}/{duration}'
                        #f' | {n_opened} files opened'
                    )
                    logger.info(msg)
                #elif 'Opening' in line:
                    #n_opened += 1
                    #logger.debug(line)
            p.wait()

            if os.path.exists(outpath):
                zf.write_with_lock(outpath, filepath)

            return download_info, None
        except Exception as e:
            return download_info, e
    
    def download_attachment(self, idx, download_info, zf):
        try:
            filepath = download_info['filepath']
            print(f'Downloading {filepath}')
            attachment_info = download_info['attachment']
            href = attachment_info['href']
            sess = RequestsSession()
            sess.cookies.update(self.cookies)
            res = sess.get(href)
            content = res.content
            zf.writestr_with_lock(filepath, content)
            return download_info, None
        except Exception as e:
            return download_info, e
  
    def download_all_to_zip(
        self, 
        download_infos,
        overwrite=False,
        prefix='',
    ):
        uid = str(uuid.uuid4())
        prefix = clean_filename(prefix)
        zf_name = f'{prefix}{uid}.zip'
        zf_path = os.path.join(self.download_dir, zf_name)

        zf = ThreadSharedZipFile(zf_path, 'w')
        logger.info(f'Downloading files to {zf_path}')
        #print(f'Downloading files to {zf_path}')

        all_futures = []
        for info_idx, download_info in enumerate(download_infos):
            futures = self.download_content(info_idx, download_info, zf)
            all_futures.extend(futures)

        wait_for_futures(all_futures)
        logger.info(f'Files can be found at {zf_path}')
        #print(f'Files can be found at {zf_path}')

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--download-dir', 
        default=config.DOWNLOAD_DIR, 
        help='directory to download files to'
    )
    parser.add_argument(
        '--email', 
        default=config.EMAIL,
        help='NTU email (e.g. bob1234@e.ntu.edu.sg)'
    )
    parser.add_argument(
        '--password',
        default=config.PASSWORD,
        help='NTU email password',
    )
    parser.add_argument(
        '--max-concurrent', 
        default=config.MAX_WORKERS,
        help='Maximum number of workers used when downloading attachments',
    )
    parser.add_argument(
        '--use-ffmpeg',
        action='store_true',
        help=(
            'Set this flag to indicate that the script should use '
            'ffmpeg to convert .m3u8 playlist to .mp4. '
            'Requires "ffmpeg" to be installed.'
        )
    )
    parser.add_argument(
        '--ffmpeg-path',
        default=config.FFMPEG_PATH,
        help='Path to ffmpeg',
    )
    
    args = parser.parse_args()

    if args.use_ffmpeg:
        path = shutil.which(args.ffmpeg_path)
        if path is None:
            logger.warning(
                f'ffmpeg cannot be found at: {args.ffmpeg_path}. '
                'Please install ffmpeg to use this feature'
            )
            args.use_ffmpeg = False
    
    args.download_dir = os.path.expanduser(args.download_dir)
    args.download_dir = os.path.abspath(args.download_dir)

    return args

if __name__ == '__main__':
    args = parse_args()

    creds = Credentials(
        email = args.email,
        password = args.password,
    )
    client = NTULearnClient(
        credentials=creds,
    )
    course_infos = client.enumerate_courses()
    driver_cookies = client.get_cookies()
    
    # prompt user to select course to download from
    for i, x in enumerate(course_infos):
        print(f'{i+1}. {x["long_name"]}')

    selected = input('Select a course: ')
    course_info = course_infos[int(selected) - 1] 
    download_infos = []

    folders = client.enumerate_content_folders(course_info)
    attachment_infos = client.enumerate_attachments_for_folders(folders)
    download_infos.extend(attachment_infos)

    media_infos = client.enumerate_course_media(course_info) 
    media_infos = media_infos
    playlist_infos = client.extract_playlists_from_media_infos(media_infos)
    download_infos.extend(playlist_infos)

    if args.use_ffmpeg:
        playlist_as_mp4_infos = [{
            'playlist_as_mp4': x['playlist'],
            'filepath': x['filepath'].replace('.m3u8', '.mp4'),
        } for x in playlist_infos]
        download_infos.extend(playlist_as_mp4_infos)
    
    for x in download_infos:
        x['filepath'] = os.path.join(
            course_info['short_name'], 
            x['filepath']
        )

    client.close()
    
    with tempfile.TemporaryDirectory(
        dir=args.download_dir,
        prefix='ntu-learn-downloader-',
        suffix='-temp'
    ) as tmpdir:
        downloader = Downloader(
            max_workers=args.max_concurrent,
            cookies=driver_cookies,
            download_dir=args.download_dir,
            temp_dir=tmpdir,
        )
        downloader.download_all_to_zip(
            download_infos,
            prefix = (
                course_info['short_name'] + '-'
            ),
        )

