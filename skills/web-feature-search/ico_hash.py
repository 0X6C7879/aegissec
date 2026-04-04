#!/usr/bin/env python3
import argparse
import hashlib
import logging
import os
import sys

import requests

import mmh3, base64
import hashlib


def calculate_ico_hash(ico_data: bytes) -> list:
    hash_value = []
    b64 = base64.encodebytes(ico_data)
    icon_hash = mmh3.hash(b64)  # 得到如 -1588080585 的整数
    hash_value.append(icon_hash)

    web_icon = hashlib.md5(ico_data).hexdigest()
    hash_value.append(web_icon)

    return hash_value


def fetch_ico_from_url(url: str) -> bytes:
    """Fetch ICO file from URL."""

    try:
        response = requests.get(url, timeout=30, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        response.raise_for_status()
        return response.content
    except requests.exceptions.Timeout:
        raise RuntimeError(f"Request timeout for URL: {url}")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Failed to fetch ICO from URL: {url}, error: {e}")


def read_ico_from_file(file_path: str) -> bytes:
    """Read ICO file from local path."""

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    if not os.path.isfile(file_path):
        raise ValueError(f"Path is not a file: {file_path}")

    try:
        with open(file_path, 'rb') as f:
            data = f.read()
        return data
    except IOError as e:
        raise RuntimeError(f"Failed to read file: {file_path}, error: {e}")


def validate_ico_format(data: bytes) -> bool:
    """Validate if the data is a valid image file (ICO, PNG, JPEG)."""
    if len(data) < 6:
        return False

    if data[0:8] == b'\x89PNG\r\n\x1a\n':
        return True

    if data[0:3] == b'\xff\xd8\xff':
        return True

    if data[0:2] != b'\x00\x00':
        return False

    if data[2:4] != b'\x01\x00' and data[2:4] != b'\x02\x00':
        return False

    return True



def main():
    parser = argparse.ArgumentParser(
        description='ICO Hash Calculator - Calculate ICO hash for FOFA and Hunter',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s --ico-url https://example.com/favicon.ico
  %(prog)s --ico-file ./icon.ico
  %(prog)s -u https://example.com/favicon.ico -q

Output formats:
  FOFA: fofa: icon_hash="<hash>"
  Hunter: hunter: web.icon=="<hash>"
'''
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        '-u', '--ico-url',
        help='URL to fetch ICO file from'
    )
    group.add_argument(
        '-f', '--ico-file',
        help='Path to local ICO file'
    )

    args = parser.parse_args()

    try:
        if args.ico_url:
            ico_data = fetch_ico_from_url(args.ico_url)
        else:
            ico_data = read_ico_from_file(args.ico_file)
        ico_hash = calculate_ico_hash(ico_data)

        print(f"FOFA: icon_hash=\"{ico_hash[0]}\"")
        print(f"Hunter: web.icon==\"{ico_hash[1]}\"")
        return 0

    except Exception as e:
        return 1


if __name__ == '__main__':
    sys.exit(main())
