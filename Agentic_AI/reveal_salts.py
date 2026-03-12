#!/usr/bin/env python3
"""
reveal_salts.py — Grid Guardian Salt Reveal CLI
================================================
Reveals the salt for a given offer_id so that the commit hash can be
verified independently.

Usage:
    python reveal_salts.py --offer-id offer_family_home_01_1735689600 \
        --secrets-dir ./data/generated/secrets

With encryption:
    python reveal_salts.py --offer-id offer_family_home_01_1735689600 \
        --encrypted-file ./data/generated/secrets/encrypted_salts.bin
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes as crypto_hashes
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


def derive_key(passphrase: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=crypto_hashes.SHA256(), length=32, iterations=100_000,
        salt=b"grid_guardian_salt_key_v1",
    )
    return kdf.derive(passphrase.encode("utf-8"))


def reveal_from_plaintext(offer_id: str, secrets_dir: str) -> str | None:
    """Reveal salt from plaintext file store."""
    base = Path(secrets_dir)
    for salt_file in base.rglob(f"{offer_id}.salt"):
        return salt_file.read_text().strip()
    # Try all subdirectories
    for hh_dir in base.iterdir():
        if hh_dir.is_dir():
            f = hh_dir / f"{offer_id}.salt"
            if f.exists():
                return f.read_text().strip()
    return None


def reveal_from_encrypted(offer_id: str, enc_file: str,
                          passphrase: str) -> str | None:
    """Reveal salt from encrypted salt store."""
    if not HAS_CRYPTO:
        print("Error: cryptography package not installed", file=sys.stderr)
        return None

    data = Path(enc_file).read_bytes()
    nonce = data[:12]
    ct = data[12:]
    key = derive_key(passphrase)
    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(nonce, ct, None)
    except Exception as e:
        print(f"Decryption failed: {e}", file=sys.stderr)
        return None

    store = json.loads(plaintext.decode("utf-8"))
    for hh_id, offers in store.items():
        if offer_id in offers:
            return offers[offer_id]
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Reveal salt for a given offer_id")
    parser.add_argument("--offer-id", required=True,
                        help="The offer_id to look up")
    parser.add_argument("--secrets-dir", default=None,
                        help="Path to plaintext secrets directory")
    parser.add_argument("--encrypted-file", default=None,
                        help="Path to encrypted_salts.bin")
    parser.add_argument("--household-id", default=None,
                        help="Optional: narrow search to household_id directory")
    args = parser.parse_args()

    if not args.secrets_dir and not args.encrypted_file:
        print("Error: provide --secrets-dir or --encrypted-file",
              file=sys.stderr)
        return 1

    salt = None

    if args.encrypted_file:
        passphrase = getpass.getpass("Passphrase: ")
        salt = reveal_from_encrypted(args.offer_id, args.encrypted_file,
                                     passphrase)
    elif args.secrets_dir:
        if args.household_id:
            search_dir = str(Path(args.secrets_dir) / args.household_id)
        else:
            search_dir = args.secrets_dir
        salt = reveal_from_plaintext(args.offer_id, search_dir)

    if salt:
        print(f"offer_id:  {args.offer_id}")
        print(f"salt_hex:  {salt}")
        return 0
    else:
        print(f"Salt not found for offer_id: {args.offer_id}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
