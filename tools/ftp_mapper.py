import ftplib
from config import FTP_HOST, FTP_USER, FTP_PASS, FTP_PORT

print("================================================================================")
print("  FMP ULTIMATE - FTP FOLDER MAPPER")
print("================================================================================\n")

try:
    print(f"Connecting to {FTP_HOST} on Port {FTP_PORT}...")
    ftp = ftplib.FTP()
    ftp.connect(FTP_HOST, FTP_PORT, timeout=10)
    ftp.login(FTP_USER, FTP_PASS)
    ftp.set_pasv(True)
    
    current_dir = ftp.pwd()
    print(f"\n[*] SUCCESS! You are logged in.")
    print(f"[*] Your FTP User's starting room is: {current_dir}\n")
    
    print("[*] Here are the folders visible in this room:")
    items = ftp.nlst()
    if not items:
        print("    (This folder is completely empty)")
    else:
        for item in items:
            print(f"    -> {item}")
            
    ftp.quit()
    print("\n================================================================================")
except Exception as e:
    print(f"\n[CRITICAL FAILURE] {e}")