import pytest
from app.encryption import encrypt_content, decrypt_content

def test_encryption_decryption():
    original = {"test_key": "test_value", "nested": {"list": [1, 2, 3]}}
    
    ciphertext = encrypt_content(original)
    
    assert isinstance(ciphertext, str)
    assert ciphertext != str(original)
    
    decrypted = decrypt_content(ciphertext)
    assert decrypted == original
