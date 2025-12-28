import subprocess
import sys
import time
import os
import matplotlib.pyplot as plt

def test_variant(variant, port):
    """Test a single variant with no loss and return chosen bitrate."""
    try:
        # Start server
        server_cmd = [sys.executable, "variant_file_server.py",
                     "--dir", "reps",
                     "--port", str(port),
                     "--variant", variant]
        
        server = subprocess.Popen(server_cmd)
        time.sleep(2)  # Wait for server to start
        
        # Start client
        client_cmd = [sys.executable, "variant_file_client.py",
                     "--port", str(port),
                     "--output", "received.mp4"]
        
        client = subprocess.Popen(client_cmd, 
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True)
        
        # Wait for client to finish
        output, _ = client.communicate(timeout=30)
        
        # Find chosen bitrate
        for line in output.split('\n'):
            if "[CHOSEN_BITRATE]" in line:
                bitrate = int(line.split()[1])
                return bitrate
        
        return 0
        
    except Exception as e:
        print(f"Error testing {variant}: {e}")
        return 0
        
    finally:
        # Cleanup
        try:
            server.terminate()
            client.terminate()
        except:
            pass
        
        if os.path.exists("received.mp4"):
            try:
                os.remove("received.mp4")
            except:
                pass

def main():
    results = {}
    base_port = 6000
    
    # Test each variant
    for variant in ["tahoe", "reno", "newreno"]:
        print(f"\nTesting {variant}...")
        bitrate = test_variant(variant, base_port)
        results[variant] = bitrate
        print(f"{variant} chose {bitrate} kbps")
        base_port += 1
        time.sleep(2)  # Wait between tests
    
    # Plot results
    plt.figure(figsize=(8, 6))
    variants = list(results.keys())
    bitrates = [results[v] for v in variants]
    
    plt.bar(variants, bitrates)
    plt.ylabel("Chosen bitrate (kbps)")
    plt.title("Bitrate Selection by TCP Variant")
    plt.savefig("variant_comparison.png")
    
    print("\nResults summary:")
    print("-" * 40)
    for variant, bitrate in results.items():
        print(f"{variant:>8}: {bitrate:>5} kbps")
    print("-" * 40)
    print("\nPlot saved as variant_comparison.png")

if __name__ == "__main__":
    main() 