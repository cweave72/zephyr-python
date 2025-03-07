
def bytesToHexStr(data: bytes):
    hexchars = [f"0x{i:02x}" for i in data]
    return ",".join(hexchars)
