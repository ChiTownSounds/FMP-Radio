import os
import sys
import subprocess
import time

# Ensure the root dir is in sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from config import FTP_HOST, FTP_PORT, FTP_USER, FTP_PASS

RCLONE_PATH = os.path.join(parent_dir, "rclone.exe")

def mount_drive():
    print("====================================================")
    print("      FMP NATIVE RCLONE MOUNT UTILITY               ")
    print("====================================================")
    
    # 1. Check if Z: drive is already mounted
    if os.path.exists("Z:/"):
        print("[*] Z: drive is already mounted. Skipping mount step to prevent stream interruption.")
        return

    # 2. Verify rclone exists
    if not os.path.exists(RCLONE_PATH):
        print(f"[-] Error: rclone.exe not found at {RCLONE_PATH}. Run installation first!")
        return

    # 2. Configure Citrus3 FTP remote in rclone
    print("[*] Configuring Citrus3 connection in rclone...")
    try:
        # Create Citrus3 remote using existing FTP details
        cmd = [
            RCLONE_PATH, "config", "create", "citrus3", "ftp",
            "host", FTP_HOST,
            "port", str(FTP_PORT),
            "user", FTP_USER,
            "pass", FTP_PASS
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        print("[OK] Citrus3 remote configured successfully in rclone.")
    except Exception as e:
        print(f"[-] Configuration Failed: {e}")
        return

    # 3. Clean up any existing Z: drive mapping or mounts
    print("[*] Dismounting any existing Z: drive mounts...")
    try:
        # Try native Windows dismount first
        subprocess.run(["net", "use", "Z:", "/delete", "/y"], capture_output=True)
    except:
        pass
    
    try:
        # Try rclone native dismount
        subprocess.run([RCLONE_PATH, "mounttype", "Z:", "unmount"], capture_output=True)
    except:
        pass

    # 4. Start native local mount using Rclone with full VFS caching (WinFsp)
    print("[*] Mounting Citrus3 remote as native Z: drive...")
    mount_cmd = [
        RCLONE_PATH, "mount", "citrus3:/", "Z:",
        "--vfs-cache-mode", "full",
        "--vfs-cache-max-age", "24h",
        "--vfs-read-chunk-size", "1M",
        "--buffer-size", "16M",
        "--network-mode"
    ]
    
    try:
        # On Windows, we use CREATE_NO_WINDOW (0x08000000) to run it silently in the background
        subprocess.Popen(mount_cmd, creationflags=0x08000000)
        
        # Wait a moment for mount initialization
        time.sleep(2)
        
        if os.path.exists("Z:/Classics"):
            print("\n" + "="*50)
            print("[OK] SUCCESS! Citrus3 mounted as native Z: drive!")
            print("Your Z: drive is now fully active, cached, and powered by Rclone!")
            print("="*50 + "\n")
        else:
            print("\n" + "="*50)
            print("[WARNING] Mount command executed, but 'Z:/Classics' is not visible yet.")
            print("Please check Windows Explorer in a few seconds; WinFsp may still be mounting.")
            print("="*50 + "\n")
            
    except Exception as e:
        print(f"[-] Failed to execute Rclone mount: {e}")

if __name__ == '__main__':
    mount_drive()
