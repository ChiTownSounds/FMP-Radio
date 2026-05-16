import ftplib
import socket
from config import FTP_HOST, FTP_USER, FTP_PASS, FTP_PORT

print("================================================================================")
print("  FMP ULTIMATE - FTP DIAGNOSTIC PROBE (CITRUS3 EDITION)")
print("================================================================================\n")

def test_standard_ftp():
    print(f"[TEST 1] Standard FTP (Port {FTP_PORT}, Passive Mode)...")
    try:
        ftp = ftplib.FTP()
        ftp.connect(FTP_HOST, FTP_PORT, timeout=10)
        ftp.login(FTP_USER, FTP_PASS)
        ftp.set_pasv(True)
        ftp.nlst() 
        ftp.quit()
        print("   -> [SUCCESS] Your server uses Standard FTP.\n")
        return True
    except Exception as e:
        print(f"   -> [FAILED] {e}\n")
        return False

def test_explicit_ftps():
    print(f"[TEST 2] Explicit FTPS (Port {FTP_PORT}, Passive Mode)...")
    try:
        ftp = ftplib.FTP_TLS()
        ftp.connect(FTP_HOST, FTP_PORT, timeout=10)
        ftp.login(FTP_USER, FTP_PASS)
        ftp.prot_p()
        ftp.set_pasv(True)
        ftp.nlst()
        ftp.quit()
        print("   -> [SUCCESS] Your server uses Explicit FTPS.\n")
        return True
    except Exception as e:
        print(f"   -> [FAILED] {e}\n")
        return False

if __name__ == "__main__":
    if not FTP_HOST or FTP_HOST == "ftp.yourradiostation.com":
        print("[CRITICAL ERROR] You need to update your FTP credentials in config.py first.")
        import sys
        sys.exit()

    print(f"Targeting Server: {FTP_HOST} on Port {FTP_PORT}\n")
    
    if not test_standard_ftp():
        if not test_explicit_ftps():
            print("================================================================================")
            print(" [CONCLUSION] Both protocols failed on Port 2121.")
            print("================================================================================")