#!/bin/python3
# ==================== CODE ===========================

import re
import logging
import time
import zipfile
import getpass
import argparse
from os import path as os_path

from collections import Counter
from threading import Lock
from requests import Session as RequestsSession
from concurrent.futures import ThreadPoolExecutor

from selenium.webdriver.chrome.options import Options
from selenium.webdriver import Chrome as ChromeWebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By

# ================== HARDCODED CONFIG / CREDENTIALS ================
# NTU email (e.g. bob1234@e.ntu.edu.sg)
EMAIL = None

# Location on disk to download .zip folders to
DOWNLOAD_DIR = os_path.expanduser('~/Downloads')

# It is bad practice to save passwords in plain-text
# do so at your own risk
PASSWORD = None

# maximum threads for concurrent downloads
MAX_WORKERS = 8

# ===================== CSS SELECTORS ==============
sso_form_selector = 'form[action*="https://login.microsoftonline.com"]'

email_input_selector = 'input[type="email"]'
password_input_selector = 'input[type="password"]'
next_input_selector = 'input[type="submit"][value="Next"]'
signin_input_selector = 'input[type="submit"][value="Sign in"]'
yes_input_selector = 'input[type="submit"][value="Yes"]'

course_list_selector = '#main-content-inner'
course_card_id_selector = '.course-id' 
course_card_title_selector = '.course-title .js-course-title-element'
course_card_status_selector = '.course-status'
course_card_selector = 'bb-base-course-card article'

content_tree_item_selector = 'li[id*="Link$ReferredToType:CONTENT"]'
content_tree_item_link_selector = 'a[href][title][target="content"]'
content_folder_attachment_selector = '[id="contentListItem:{0}"] .attachments li'
content_folder_attachment_link_selector = 'a[href*="/bbcswebdav"]'

body_selector = 'body'
# ==================== CODE ===========================

def clean_filename(name):
    name = re.sub(r'[^a-zA-Z-_0-9.]+', '_', name).strip('_')
    return name

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
                card_info = NTULearnClient.course_card_to_info(course_cards[i])
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
        options.add_argument('--headless')
        self.driver = ChromeWebDriver(
            options
        )

    def goto_home(self):
        logging.info(f'Navigate to {self.HOME_PAGE}')
        self.driver.get(self.HOME_PAGE)
        self.wait_for_page_or_signin(self.HOME_PAGE)
    
    def signin(self):
        email = self.credentials.get_email()
        password = self.credentials.get_password()
        print('Signing in with provided credentials')
        self.wait_for_input_then_send_keys(
                email_input_selector, email)
        self.wait_for_input_then_send_keys(
                password_input_selector, password)
        self.wait_for_button_presence_then_click(next_input_selector)
        self.wait_for_button_presence_then_click(signin_input_selector)
        self.wait_for_button_presence_then_click(yes_input_selector)

    @staticmethod
    def course_card_to_info(card):
        course_id = card.get_attribute('id').split('-')[-1]
        card_info = {'course_id': course_id}
        
        for key, selector in [
            ('short_name', course_card_id_selector),
            ('long_name', course_card_title_selector),
            ('status', course_card_status_selector)
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

    def enumerate_courses(self):
        logging.info(f'Navigate to {self.COURSES_PAGE}')
        self.driver.get(self.COURSES_PAGE)
        self.wait_for_page_or_signin(self.COURSES_PAGE)

        print("Extracting course list")
        course_cards = self.wait_for_cards_to_load()
        courses_info = NTULearnClient.course_cards_to_info(course_cards)
        open_courses_info = list(filter(
            lambda x: x['status'] == 'Open', 
            courses_info
        ))
        return open_courses_info
   
    def wait_for_cards_to_load(self, timeout=10):
        driver = self.driver

        course_cards = WebDriverWait(driver, timeout).until(
            EC.presence_of_all_elements_located((
                By.CSS_SELECTOR, course_card_selector
            ))
        )
        scrollable_course_list = driver.find_element(
                By.CSS_SELECTOR, course_list_selector)
        container_rect = Element.get_bounding_rect(
                driver, scrollable_course_list)

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
            WebDriverWait(driver, timeout).until(
                Condition.course_cards_are_complete(cards_in_view)
            )
            offset = offset + n_in_viewport
            logging.info(f'Course cards loaded: {offset}/{len(course_cards)}')
            
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
            EC.presence_of_element_located((By.CSS_SELECTOR, sso_form_selector)),
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

    def enumerate_content_folders(self, course_info, timeout=10):
        driver = self.driver
        course_id = course_info['course_id']
        path = NTULearnClient.COURSE_CONTENT_TREE_TEMPLATE.format(
            course_id
        )

        driver.get(path)
        contents = WebDriverWait(driver, timeout).until(
            EC.presence_of_all_elements_located((
                By.CSS_SELECTOR, content_tree_item_selector 
            ))
        )
        
        folders = []
        counter = Counter()
        for elem_idx, elem in enumerate(contents):
            # initialize folder
            content_id = elem.get_attribute('id').split(':::')[1]
            elem_link = elem.find_element(
                By.CSS_SELECTOR, content_tree_item_link_selector
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
            By.CSS_SELECTOR, body_selector
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
            filepath = os_path.join(
                course_info['short_name'], 
                *path_parts
            )
            folders[elem_idx]['filepath'] = filepath

        leaf_folders = [] 
        for elem_idx, elem in enumerate(contents):
            if counter[elem.id] == 1:
                leaf_folders.append(folders[elem_idx])

        return leaf_folders
    
    def enumerate_attachments_for_folders(self, folders, timeout=1):
        all_attachments = []
        for folder in folders:
            folder_attachments = self.enumerate_attachments_for_folder(
                    folder, timeout=timeout)
            all_attachments.extend(folder_attachments)
        n = len(all_attachments)
        logging.info(f"Found {n} attachments");
        print(f'Found {n} attachments')
        return all_attachments

    def enumerate_attachments_for_folder(self, folder, timeout=5):
        driver = self.driver
        content_id = folder['content_id']
        course_id = folder['course_id']
        selector = content_folder_attachment_selector.format(content_id)
        href = folder['href']

        driver.get(href)
        logging.debug(f'{selector} for {href}')
        try:
            attachment_elems = WebDriverWait(driver, timeout=timeout).until(
                EC.presence_of_all_elements_located((
                    By.CSS_SELECTOR, 
                    selector,
                ))
            )
            driver.implicitly_wait(1)
        except Exception as e: 
            logging.warning(
                "Unable to locate any attachments "
                f"for course:{course_id} content:{content_id} "
            )
            return []
        
        attachment_infos = []
        for a_idx, attachment_elem in enumerate(attachment_elems):
            link_elem = attachment_elem.find_element(
                By.CSS_SELECTOR, 
                content_folder_attachment_link_selector
            )
            try:
                m = re.match(r'(.*?)\s+\(([^\)]+)\)', attachment_elem.text)
                displayed_filename, displayed_filesize = m.groups()
                displayed_filename = displayed_filename.strip()
                displayed_filesize = displayed_filesize.strip()
                cleaned_filename = clean_filename(displayed_filename) 
                filepath = os_path.join(folder['filepath'], cleaned_filename)
                href = link_elem.get_attribute('href')

                attachment_info = {
                    'parentpath': folder['filepath'],
                    'filesize': displayed_filesize,
                    'filename': displayed_filename,
                    'filepath': filepath,
                    'href': href,
                }
                attachment_infos.append(attachment_info)
            except Exception as e:
                logging.warning(
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

class Downloader:
    def __init__(self, cookies={}, max_workers=None):
        self.cookies = cookies
        self.max_workers = max_workers
    
    def download_content(self, attachment_info):
        try:
            filename = attachment_info['filename']
            href = attachment_info['href']
            logging.info(f'Downloading {filename}')
            print(f'Downloading {filename}')
            sess = RequestsSession()
            sess.cookies.update(self.cookies)
            res = sess.get(href)
            content = res.content
            return attachment_info, content, None
        except Exception as e:
            return attachment_info, None, e
    
    def download_all_to_zip(
        self, zf_path, attachment_infos, overwrite=False
    ):
        if os_path.exists(zf_path) and not overwrite:
            raise Exception(f'{zf_path} already exists')

        lock = Lock()
        locked_items = {
            'zf': zipfile.ZipFile(zf_path, 'w'),
            'successful_downloads': 0,
        }
        executor = ThreadPoolExecutor(max_workers=self.max_workers)

        def done_callback(future):
            attachment_info, content, error = future.result()
            filename = attachment_info['filename']
            filepath = attachment_info['filepath']

            if content is not None:
                arc_path = attachment_info['filepath']
                with lock:
                    locked_items['zf'].writestr(arc_path, content)
                    locked_items['successful_downloads'] += 1
                    logging.debug(
                        f'Created entry for {filename} in {zf_path}: '
                        f'{arc_path}'
                    )
            else:
                filename = attachment_info['filename']
                logging.error(f"Failed to download {filename}\n{error}")

        logging.info(f'Downloading files to {zf_path}')
        print(f'Downloading files to {zf_path}')
        for attachment_info in attachment_infos:
            future = executor.submit(
                self.download_content, 
                attachment_info
            )
            future.add_done_callback(done_callback)


        executor.shutdown(wait=True)
        with lock:
            count = locked_items['successful_downloads']
            total = len(attachment_infos)
            logging.info(
                f'Downloaded {count} of {total} files to {zf_path}'
            )
            print(
                f'Downloaded {count} of {total} files to {zf_path}'
            )
        locked_items['zf'].close()

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--download-dir', 
        default=DOWNLOAD_DIR, 
        help='directory to download files to'
    )
    parser.add_argument(
        '--email', 
        default=EMAIL,
        help='NTU email (e.g. bob1234@e.ntu.edu.sg)'
    )
    parser.add_argument(
        '--password',
        default=PASSWORD,
        help='NTU email password',
    )
    parser.add_argument(
        '--max-concurrent', 
        default=MAX_WORKERS,
        help='Maximum number of workers used when downloading',
    )
    
    args = parser.parse_args()
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
    folders = client.enumerate_content_folders(course_info)
    attachment_infos = client.enumerate_attachments_for_folders(folders)
    client.close()

    downloader = Downloader(
        max_workers=args.max_concurrent,
        cookies=driver_cookies,
    )
    time_since_epoch = int(time.time())
    zipname = course_info['short_name']
    zipname = f'{zipname}_{time_since_epoch}.zip'
    zip_path = os_path.join(DOWNLOAD_DIR, zipname)
    downloader.download_all_to_zip(zip_path, attachment_infos)

