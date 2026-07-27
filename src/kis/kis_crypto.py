from base64 import b64decode
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

def aes256_cbc_base64_decrypt(key: str, iv: str, cipher_text_b64: str) -> str:
    """
    key: 구독 성공 응답의 output.key (문자열)
    iv : 구독 성공 응답의 output.iv  (문자열)
    cipher_text_b64: 실시간 메시지의 마지막 필드(암호문, base64)
    """
    cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, iv.encode("utf-8"))
    plain_bytes = unpad(cipher.decrypt(b64decode(cipher_text_b64)), AES.block_size)
    return plain_bytes.decode("utf-8")

if __name__ == "__main__":
    print("crypto")