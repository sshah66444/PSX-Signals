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
    raw_chat = chat if chat is not None else os.getenv('TELEGRAM_CHAT_ID')
    if not token or not raw_chat:
        raise ValueError('Telegram secrets are required to unlock the delivery journal')
    resolved_chat = raw_chat.split(',')[0].strip() if ',' in str(raw_chat) else str(raw_chat).strip()
    material = hashlib.sha256((str(token).strip()+'\0'+resolved_chat).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(material))


def decrypt(token=None, chat=None, plain=PLAIN, encrypted=ENCRYPTED):
    plain, encrypted = Path(plain), Path(encrypted)
    if not encrypted.exists():
        return False
    if plain.exists():
        raise FileExistsError('Refusing to overwrite an existing plaintext journal')

    enc_bytes = encrypted.read_bytes()
    candidates = []
    if chat is not None:
        candidates.append(chat)
        if ',' in str(chat):
            candidates.extend([c.strip() for c in str(chat).split(',') if c.strip()])
    else:
        env_chat = os.getenv('TELEGRAM_CHAT_ID', '')
        if env_chat:
            candidates.append(env_chat)
            if ',' in env_chat:
                candidates.extend([c.strip() for c in env_chat.split(',') if c.strip()])
        desk_chat = os.getenv('PSX_DESK_CHAT_ID', '').strip()
        if desk_chat and desk_chat not in candidates:
            candidates.append(desk_chat)
        if '1971314850' not in candidates:
            candidates.append('1971314850')

    last_err = None
    data = None
    for cand in candidates:
        try:
            dec = cipher(token, cand).decrypt(enc_bytes)
            if dec.startswith(b'SQLite format 3\x00'):
                data = dec
                break
        except Exception as e:
            last_err = e
            continue

    if data is None:
        if last_err is not None:
            raise last_err
        raise ValueError('Could not decrypt delivery journal with available secrets')

    plain.parent.mkdir(parents=True, exist_ok=True)
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
