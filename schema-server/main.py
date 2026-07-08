from fastapi import FastAPI, HTTPException
import os
import argparse
import json
import uvicorn


app = FastAPI()

SCHEMA_DIR = "data/schemas"

@app.get("/{app_name}")
async def get_schema(app_name: str):
    """
    Belirtilen uygulama (app_name) için JSON şemasını döner.
    Örn: /turnike -> /data/schemas/turnike.schema.json dosyasını okur.
    """
    schema_path = os.path.join(SCHEMA_DIR, f"{app_name}.schema.json")
    if not os.path.exists(schema_path):
        raise HTTPException(status_code=404, detail="Schema not found")
    else:
        try:
            with open(schema_path, "r") as f:
                schema = json.load(f)
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail="Invalid JSON format in schema file.")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

    return schema


if __name__ == "__main__":
    # Parametreleri (Arguments) ayarlama
    parser = argparse.ArgumentParser(description="Run the Schema Service")
    parser.add_argument("--schema-dir", type=str, default="../data/schemas", help="Directory containing schema JSON files")
    parser.add_argument("--listen", type=str, default="0.0.0.0:5001", help="Host and port to listen on (e.g., 0.0.0.0:5001)")

    args = parser.parse_args()
    
    # Global SCHEMA_DIR değişkenini güncelle
    SCHEMA_DIR = args.schema_dir
    
    # --listen parametresini host ve port olarak ayır (Örn: "0.0.0.0:5001" -> "0.0.0.0", 5001)
    try:
        host, port_str = args.listen.split(":")
        port = int(port_str)
    except ValueError:
        print("Invalid --listen format. Use host:port (e.g., 0.0.0.0:5001)")
        exit(1)

    # Uvicorn sunucusunu başlat
    print(f"Starting Schema Service on {host}:{port}, reading schemas from {SCHEMA_DIR}")
    uvicorn.run(app, host=host, port=port)
