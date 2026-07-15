import argparse
import json
import os
import uvicorn
from fastapi import FastAPI, HTTPException

app = FastAPI()
VALUES_DIR = "/data/values"

@app.get("/{app_name}")
async def get_values(app_name: str):
    values_path = os.path.join(VALUES_DIR, f"{app_name}.value.json")
    if not os.path.exists(values_path):
        raise HTTPException(status_code=404, detail="Values not found")
    else:
        try:
            with open(os.path.join(VALUES_DIR, f"{app_name}.value.json"), "r") as f:
                values = json.load(f)
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail="Invalid JSON format in values file.")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

    return values

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Values Service")
    parser.add_argument("--values-dir", type=str, default="../data/values", help="Directory containing values JSON files")
    parser.add_argument("--listen", type=str, default="0.0.0.0:5002")
    args = parser.parse_args()
    VALUES_DIR = args.values_dir
    try:
        host, port_str = args.listen.split(":")
        port = int(port_str)
    except ValueError:
        print("Invalid --listen format. Use host:port (e.g.,")
        exit(1)
    print(f"Starting Values Service on {host}:{port}, reading values from {VALUES_DIR}")
    uvicorn.run(app, host=host, port=port)
    
