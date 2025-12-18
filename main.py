#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
from bs4 import BeautifulSoup
import concurrent.futures
from tqdm import tqdm
import csv
import os
import time
from urllib.parse import urljoin, urlparse
import hashlib
import zipfile

# --- Configuration ---
INPUT_CSV = 'phase2.csv'       # Input file with IDs to process
OUTPUT_CSV = 'secret_codes_summary.csv'
MAX_WORKERS = 100              # Number of parallel threads
HTML_DIR = 'html_responses'    # Folder to save HTML files
ASSETS_DIR = os.path.join(HTML_DIR, 'assets')  # Shared folder for assets
ZIP_FILE = 'html_responses.zip'  # Zip file name

# Ensure the HTML and Assets output directories exist
os.makedirs(HTML_DIR, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)

HEADERS = {
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'accept-language': 'en-US,en;q=0.9',
    'cache-control': 'max-age=0',
    'priority': 'u=0, i',
    'sec-ch-ua': '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'document',
    'sec-fetch-mode': 'navigate',
    'sec-fetch-site': 'same-origin',
    'sec-fetch-user': '?1',
    'upgrade-insecure-requests': '1',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
}


def download_asset(url, session):
    """
    Downloads an asset and saves it to ASSETS_DIR.
    Returns the relative path to be used in the HTML.
    """
    try:
        # Create a unique filename based on the URL content to avoid duplicates
        parsed = urlparse(url)
        filename = os.path.basename(parsed.path)
        if not filename:
            filename = 'index.html'

        # Generate a hash for the full URL to handle same filenames from diff paths
        url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()[:8]
        name, ext = os.path.splitext(filename)
        # Clean query params from extension if present
        if '?' in ext:
            ext = ext.split('?')[0]

        save_name = f"{name}_{url_hash}{ext}"
        save_path = os.path.join(ASSETS_DIR, save_name)

        # If file already exists, skip download (keep once repeated assets)
        if not os.path.exists(save_path):
            resp = session.get(url, headers=HEADERS, timeout=5)
            if resp.status_code == 200:
                with open(save_path, 'wb') as f:
                    f.write(resp.content)

        return f"assets/{save_name}"
    except Exception:
        return url  # Return original URL if download fails


def process_activation_id(activation_id_int):
    """
    Fetches URL.
    - If 200: Extracts ONLY the instruction-card div, downloads its assets, and saves to disk.
    - Returns: Dict with ID, Code, Status.
    """
    activation_id = f"{activation_id_int:06}"
    url = f'https://www.joinsecret.com/activation/{activation_id}'

    result = {
        'id': activation_id,
        'secret_code': 'Not Found',
        'status': 0
    }

    # Use a session for connection pooling
    session = requests.Session()

    try:
        response = session.get(url, headers=HEADERS, timeout=10)
        result['status'] = response.status_code

        if response.status_code == 200:
            original_soup = BeautifulSoup(response.text, 'html.parser')

            # --- 1. PARSE SECRET CODE (Business Logic) ---
            clipboard_div = original_soup.find('div', attrs={'data-controller': 'copytoclipboard'})
            if clipboard_div:
                input_field = clipboard_div.find('input')
                if input_field and input_field.get('value'):
                    result['secret_code'] = input_field['value']

            # --- 2. EXTRACT SPECIFIC DIV & SAVE HTML ---
            target_content = original_soup.find('div', class_='instruction-card')

            if target_content:
                # Create a new minimal HTML skeleton
                new_soup = BeautifulSoup('<!DOCTYPE html><html><head></head><body></body></html>', 'html.parser')

                # A. Move CSS Links
                css_links = original_soup.find_all('link', rel='stylesheet')
                for link in css_links:
                    new_soup.head.append(link)

                # B. Append the target content
                new_soup.body.append(target_content)

                # C. Download and rewrite assets
                for tag in new_soup.find_all('link', href=True):
                    if tag.get('rel') == ['stylesheet']:
                        abs_url = urljoin(url, tag['href'])
                        local_path = download_asset(abs_url, session)
                        tag['href'] = local_path

                for tag in new_soup.find_all('img', src=True):
                    abs_url = urljoin(url, tag['src'])
                    local_path = download_asset(abs_url, session)
                    tag['src'] = local_path

                for tag in new_soup.find_all('a', href=True):
                    tag['href'] = urljoin(url, tag['href'])

                # Save the Cleaned HTML
                html_filename = os.path.join(HTML_DIR, f"{activation_id}.html")
                with open(html_filename, 'w', encoding='utf-8') as f:
                    f.write(str(new_soup))

    except Exception:
        result['status'] = 0
        result['secret_code'] = "Error"

    return result


def create_zip_archive():
    """
    Creates a compressed zip file of the HTML_DIR folder.
    """
    print(f"\nCreating zip archive: {ZIP_FILE}...")
    
    with zipfile.ZipFile(ZIP_FILE, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(HTML_DIR):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, os.path.dirname(HTML_DIR))
                zipf.write(file_path, arcname)
    
    print(f"✓ Zip archive created successfully: {ZIP_FILE}")


def get_ids_from_csv(filename):
    """
    Reads Activation IDs from the provided CSV file.
    Assumes column names are 'Activation ID' and 'Status Code'.
    """
    ids = []
    try:
        with open(filename, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if 'Activation ID' in row:
                    try:
                        ids.append(int(row['Activation ID']))
                    except ValueError:
                        pass
    except FileNotFoundError:
        print(f"Error: {filename} not found.")
        return []
    return ids


def main():
    # 1. Load initial IDs to process
    print(f"Loading IDs from {INPUT_CSV}...")
    ids_to_process = get_ids_from_csv(INPUT_CSV)
    print(f"Found {len(ids_to_process)} IDs to process.")

    file_exists = os.path.isfile(OUTPUT_CSV)
    
    # Open CSV in append mode, but keep file handle logic simple by opening/closing inside loop 
    # or just keep it open. To be safe with retries, we'll append results as we get them.
    
    # Initialize CSV header if new file
    if not file_exists:
        with open(OUTPUT_CSV, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Activation ID', 'Secret Code', 'Status Code'])

    iteration = 1
    
    while ids_to_process:
        print(f"\n--- Iteration {iteration} ---")
        print(f"Processing {len(ids_to_process)} IDs...")
        
        retry_ids = []
        
        # We process in batches
        with open(OUTPUT_CSV, mode='a', newline='', encoding='utf-8') as f_out:
            writer = csv.writer(f_out)
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                # Submit all current tasks
                future_to_id = {
                    executor.submit(process_activation_id, i): i
                    for i in ids_to_process
                }

                pbar = tqdm(
                    concurrent.futures.as_completed(future_to_id),
                    total=len(future_to_id),
                    unit="req",
                    desc=f"Batch {iteration}",
                    colour="green"
                )

                for future in pbar:
                    data = future.result()
                    status = data['status']

                    # Check if we are done with this ID (200 or 404)
                    if status == 200 or status == 404:
                        # Success (or definite not found), write to file
                        writer.writerow([
                            data['id'],
                            data['secret_code'],
                            data['status']
                        ])
                    else:
                        # 429 (Rate Limit) or 0 (Error) -> Add to retry list
                        retry_ids.append(int(data['id']))

                    pbar.set_postfix(status=status, retries_len=len(retry_ids))

        # Update the list for the next loop
        ids_to_process = retry_ids
        
        if ids_to_process:
            print(f"\n{len(ids_to_process)} IDs failed (Status 429/0). Retrying in 10 seconds...")
            time.sleep(10) # Wait a bit to let rate limits cool down
            iteration += 1
        else:
            print("\nAll IDs processed successfully (200 or 404).")

    # Create zip archive after all files are extracted
    create_zip_archive()

if __name__ == "__main__":
    main()
