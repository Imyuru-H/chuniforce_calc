# src/utils.py
import secrets
import hashlib
import base64
import urllib.parse

def generate_code_verifier():
    return secrets.token_urlsafe(64)

def generate_code_challenge(verifier: str):
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode()