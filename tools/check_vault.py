import os

Z_DRIVE = r"Z:\\"

def audit_files():
    print(f"[*] Auditing directory: {Z_DRIVE}")
    file_registry = {}
    
    for filename in os.listdir(Z_DRIVE):
        if filename.endswith(".mp3"):
            filepath = os.path.join(Z_DRIVE, filename)
            size = os.path.getsize(filepath)
            
            if size not in file_registry:
                file_registry[size] = []
            file_registry[size].append(filename)

    for size, files in file_registry.items():
        if len(files) > 1:
            print(f"\n[!] WARNING: Potential Duplicates ({size} bytes):")
            for f in files:
                print(f"    - {f}")
        else:
            print(f"[OK] {files[0]} ({size} bytes)")

if __name__ == "__main__":
    audit_files()