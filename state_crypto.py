"""Protect the delivery journal before committing it to a public repository."""
import argparse
import base64
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent
PLAIN = ROOT/'.state'/'briefing.sqlite3'
ENCRYPTED = ROOT/'.state'/'briefing.sqlite3.enc'


def cipher(token=None, chat=None):
    token = token or os.getenv('TELEGRAM_BOT_TOKEN')
    chat = chat or os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat:
        raise ValueError('Telegram secrets are required to unlock the delivery journal')
    material = hashlib.sha256((token+'\0'+chat).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(material))


def decrypt(token=None, chat=None, plain=PLAIN, encrypted=ENCRYPTED):
    plain, encrypted = Path(plain), Path(encrypted)
    if not encrypted.exists():
        return False
    if plain.exists():
        raise FileExistsError('Refusing to overwrite an existing plaintext journal')
    data = cipher(token,chat).decrypt(encrypted.read_bytes())
    if not data.startswith(b'SQLite format 3\x00'):
        raise ValueError('Decrypted journal is not SQLite')
    plain.parent.mkdir(parents=True,exist_ok=True)
    plain.write_bytes(data)
    return True


def encrypt(token=None, chat=None, plain=PLAIN, encrypted=ENCRYPTED):
    plain, encrypted = Path(plain), Path(encrypted)
    if not plain.exists():
        return False
    data = plain.read_bytes()
    if not data.startswith(b'SQLite format 3\x00'):
        raise ValueError('Journal is not SQLite')
    encrypted.parent.mkdir(parents=True,exist_ok=True)
    temporary = encrypted.with_suffix(encrypted.suffix+'.tmp')
    temporary.write_bytes(cipher(token,chat).encrypt(data))
    temporary.replace(encrypted)
    plain.unlink()
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation',choices=('encrypt','decrypt'))
    args = parser.parse_args()
    print(f'{args.operation}: {globals()[args.operation]()}')


if __name__ == '__main__':
    main()
