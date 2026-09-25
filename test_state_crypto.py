import sqlite3
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import InvalidToken
import state_crypto as s


class StateCryptoTests(unittest.TestCase):
    def test_roundtrip_and_wrong_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            plain=Path(folder)/'journal.db'; encrypted=Path(folder)/'journal.enc'
            db=sqlite3.connect(str(plain))
            db.execute('CREATE TABLE example (value TEXT)')
            db.execute('INSERT INTO example VALUES (?)',('private',))
            db.commit();db.close()
            self.assertTrue(s.encrypt('token','chat',plain,encrypted))
            self.assertFalse(plain.exists())
            self.assertNotIn(b'private',encrypted.read_bytes())
            with self.assertRaises(InvalidToken):
                s.decrypt('wrong','chat',plain,encrypted)
            self.assertFalse(plain.exists())
            self.assertTrue(s.decrypt('token','chat',plain,encrypted))
            db=sqlite3.connect(str(plain))
            self.assertEqual(db.execute('SELECT value FROM example').fetchone()[0],'private')
            db.close()


if __name__=='__main__': unittest.main()
