import os
from dotenv import load_dotenv
from modules.ingest import Gatekeeper

# Load environment variables if needed
load_dotenv()

def test_gatekeeper():
    print("Testing Gatekeeper module...")
    gk = Gatekeeper()
    
    # Complex URL: Big Buck Bunny
    test_url = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
    print(f"Feeding URL: {test_url}")
    
    success, metadata = gk.process_request(test_url)
    
    print("\n--- Gatekeeper Results ---")
    print(f"Success Status: {success}")
    if success:
        for key, value in metadata.items():
            # Truncate lyrics for cleaner output if they are very long
            if key == "lyrics" and len(value) > 200:
                print(f"{key}: {value[:200]}... [TRUNCATED]")
            else:
                print(f"{key}: {value}")
    else:
        print(f"Error: {metadata.get('error', 'Unknown Error')}")

if __name__ == '__main__':
    test_gatekeeper()
