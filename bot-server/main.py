import argparse
import copy
import json
import os
import re
import sys
from typing import Literal
#testing out github webhook
import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from jsonschema import ValidationError, validate
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://ollama:11434"
).rstrip("/")

SCHEMA_SERVICE_URL = os.getenv(
    "SCHEMA_SERVICE_URL",
    "http://schema-server:5001"
).rstrip("/")

VALUES_SERVICE_URL = os.getenv(
    "VALUES_SERVICE_URL",
    "http://values-server:5002"
).rstrip("/")

OLLAMA_MODEL = os.getenv(
    "OLLAMA_MODEL",
    "qwen2.5:3b"
)

OLLAMA_GENERATE_URL = f"{OLLAMA_BASE_URL}/api/generate"
ALLOWED_SERVICES = ["turnike", "yetkilendirme", "entegrasyon"]

 
MAX_SERVICES_PER_PROMPT = 3
MAX_RETRIES = 3 # LLM'in hatasını düzeltmesi için verilecek maksimum şans
MAX_CHANGES_PER_SERVICE = 5

app = FastAPI()

@app.get("/health")
def health_check():
    return {"status": "healthy"}

class UserMessage(BaseModel):
    input: str


class PlannedTask(BaseModel):
    service: Literal[
        "turnike",
        "yetkilendirme",
        "entegrasyon",
    ]
    changes: list[str]


class TaskPlan(BaseModel):
    tasks: list[PlannedTask]

def clean_json_response(text: str) -> str:
    """LLM'in üretebileceği markdown bloklarını temizler ve sadece JSON kısmını alır."""
    text = text.strip()
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        return match.group(0)
    return text

def apply_patch_to_dict(original_dict, path_str, new_value):
    """Noktalı yol (dot-notation) ile verilen yamayı sözlüğe (dict) uygular."""
    keys = path_str.split('.')
    d = original_dict
    for i, k in enumerate(keys[:-1]):
        if k not in d:
            d[k] = {} 
        d = d[k]
    d[keys[-1]] = new_value
    return original_dict

async def identify_tasks(user_input: str) -> list:
    """Kullanıcı girdisini yapılandırılmış görev listesine dönüştürür."""

    system_prompt = f"""
    You are a strict Kubernetes Task Router.

    Parse the user's input and identify which services must be modified.

    Valid services:
    {ALLOWED_SERVICES}

    Rules:
    1. Users may use ';' to separate changes for the SAME service.
    2. Users may use '.' or 'and' to separate DIFFERENT services.
    3. Preserve whether the request refers to request, limit, environment
       variable, memory, or CPU.
    4. Do not invent services.
    5. Return data matching the supplied JSON schema.

    Example:
    {{
      "tasks": [
        {{
          "service": "turnike",
          "changes": ["set cpu limit to 800"]
        }}
      ]
    }}
    """

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": (
            "Convert this user request into a task plan: "
            f"{json.dumps(user_input, ensure_ascii=False)}"
        ),
        "system": system_prompt,
        "stream": False,

        # "json" yerine gerçek JSON Schema gönderiyoruz.
        "format": TaskPlan.model_json_schema(),

        "options": {
            "num_ctx": 4096,
            "temperature": 0,
        },
    }

    try:
        async with httpx.AsyncClient() as client:
            print("[STEP 1] Planning tasks from user input...", flush=True)

            response = await client.post(
                OLLAMA_GENERATE_URL,
                json=payload,
                timeout=60.0,
            )
            response.raise_for_status()

            llm_output = response.json().get("response", "").strip()

            print(
                f"[TASK PLANNER RAW OUTPUT] -> {llm_output}",
                flush=True,
            )

            # clean_json_response kullanmıyoruz.
            # Pydantic hem JSON'u hem alan tiplerini doğrular.
            task_plan = TaskPlan.model_validate_json(llm_output)

            return [
                task.model_dump()
                for task in task_plan.tasks
            ]

    except (PydanticValidationError, httpx.HTTPError, ValueError) as exc:
        print(f"[TASK PLANNER ERROR] -> {exc}", flush=True)

        raise HTTPException(
            status_code=502,
            detail="Task planner returned an invalid structured response.",
        ) from exc

async def fetch_schema_and_values(service_name: str) -> dict:
    """Adım 2: İlgili servis için Schema ve Mevcut Değerleri (Values) çeker."""
    async with httpx.AsyncClient() as client:
        schema_resp = await client.get(f"{SCHEMA_SERVICE_URL}/{service_name}", timeout=10.0)
        if schema_resp.status_code == 404:
            raise HTTPException(status_code=404, detail=f"Schema for '{service_name}' not found.")
        schema_resp.raise_for_status()
        
        values_resp = await client.get(f"{VALUES_SERVICE_URL}/{service_name}", timeout=10.0)
        if values_resp.status_code == 404:
            raise HTTPException(status_code=404, detail=f"Values for '{service_name}' not found.")
        values_resp.raise_for_status()
    
    return {"schema": schema_resp.json(), "current_values": values_resp.json()}       

async def generate_patches_for_service(service_name: str, schema: dict, current_values: dict, changes: list) -> dict:

    """Adım 3: Görev listesini alır ve JSON Schema kurallarına göre bir 'Yama Listesi' (Patch Array) üretir."""

    correct_example = json.dumps([
    {
        "path": "...",
        "new_value": 850
    }
    ], indent=2)

    incorrect_example = json.dumps({
        "path": "...",
        "new_value": 850
    }, indent=2)

    
   
    system_prompt = f"""You are an expert Kubernetes configuration editor.
    Your job is to generate a JSON patch array for the '{service_name}' service based on the requested changes.
    
    Context:
    - JSON Schema (Rules): {json.dumps(schema)}
    - Current Values (State): {json.dumps(current_values)}
    
    CRITICAL RULES:
    1. Determine the EXACT dot-notation path of the field that needs to change based on the Current Values structure.
    2. Output ONLY a valid JSON ARRAY of patch objects.
    Even if there is only ONE patch, you MUST return an array.

    Correct:
    {correct_example}

    Incorrect:
    {incorrect_example}
    
    3. Ensure the 'new_value' strictly matches the data type defined in the JSON Schema.
    4. Output ONLY a valid JSON array of patch objects. NO markdown. NO explanations.
    5. The "path" MUST exactly trace the nested structure of the 'Current Values' JSON. 
    6. Do NOT invent paths. Do NOT skip intermediate levels (like 'containers' arrays or dicts).
    7. The "new_value" MUST strictly match the data type and limits defined in the JSON Schema.
    8. Example output format (use actual paths from Current Values):
    [
      {{"path": "workloads.deployments.turnike.containers.turnike.resources.cpu.requestMilliCPU", "new_value": 850}}
    ]
    9. DO NOT use forward slashes (/). You MUST use strict DOT-NOTATION (e.g., a.b.c).
    10. CHECK YOUR SPELLING! Ensure keys perfectly match the 'Current Values' tree. (e.g., Do not write 'turkike' if the key is 'turnike').
    """

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": f"Requested changes: {json.dumps(changes)}. Generate the patch array:",
        "system": system_prompt,
        "stream": False,
        "format": "json",
        "options": {"num_ctx": 8192}
    }

    try:
        timeout_config = httpx.Timeout(300.0, connect=60.0) 
        async with httpx.AsyncClient(timeout=timeout_config) as client:
            print(f"[STEP 3] Generating patches for '{service_name}'...", flush=True)
            response = await client.post(OLLAMA_GENERATE_URL, json=payload)
            response.raise_for_status()
            
            llm_output = response.json().get("response", "")
            clean_output = clean_json_response(llm_output)
            print(f"[{service_name.upper()} PATCH OUTPUT] -> {clean_output}", flush=True)
            
            patches = json.loads(clean_output)

            # Tek patch geldiyse listeye çevir
            if isinstance(patches, dict):
                patches = [patches]

            # Güvenlik kontrolü
            if not isinstance(patches, list):
                raise TypeError(f"Expected list, got {type(patches)}")

            return patches

    except Exception as e:
        print(f"[PATCH GENERATION ERROR] -> {e}")
        raise HTTPException(status_code=500, detail=f"AI failed to generate valid patches for {service_name}.")
    
@app.post("/message")
async def process_user_request(message: UserMessage):
    user_input = message.input.strip()
    if not user_input:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    
    # 1. Girdiyi analiz et ve görevlere böl
    tasks = await identify_tasks(user_input)
    
    if not tasks:
        raise HTTPException(status_code=400, detail="Could not identify any valid services in the request.")

    
    if len(tasks) > MAX_SERVICES_PER_PROMPT:
        raise HTTPException(
            status_code=400, 
            detail=f"Too many services requested. Limit is {MAX_SERVICES_PER_PROMPT}. You requested {len(tasks)}."
        )

    final_results = {}

    
    for task in tasks:
        service_name = task.get("service")
        changes = task.get("changes", [])

        if not changes:
            continue

        if len(changes) > MAX_CHANGES_PER_SERVICE:
            raise HTTPException(
                status_code=400, 
                detail=f"Too many changes for service '{service_name}'. Limit is {MAX_CHANGES_PER_SERVICE}."
            )

        print(f"\n--- Processing Service: {service_name} ---")
        app_data = await fetch_schema_and_values(service_name)
        
        # SELF-HEALING (OTO-DÜZELTME) DÖNGÜSÜ BAŞLIYOR
        attempt = 0
        success = False
        last_error = ""
        current_changes_prompt = changes # İlk denemede sadece kullanıcının isteği gider

        while attempt < MAX_RETRIES and not success:
            attempt += 1
            if attempt > 1:
                print(f"[RETRY {attempt}/{MAX_RETRIES}] AI made a mistake. Retrying with error feedback...")
                # LLM'e hatasını söylüyoruz ki düzeltebilsin
                current_changes_prompt = f"PREVIOUS ATTEMPT FAILED. ERROR: '{last_error}'. FIX YOUR MISTAKE! Original request: {changes}"

            
            patches = await generate_patches_for_service(
                service_name=service_name,
                schema=app_data["schema"],
                current_values=app_data["current_values"],
                changes=current_changes_prompt
            )

            updated_values = copy.deepcopy(app_data["current_values"])
            
            # 1. SAVUNMA KATMANI: PYTHON SANİTİZASYONU
            for patch in patches:
                path_str = str(patch.get("path", "")).strip()
                new_val = patch.get("new_value")
                
                # LLM inatla slash (/) kullanmışsa, noktaya (.) çevir
                path_str = path_str.removeprefix("/") # Baştaki kök slash'i at
                path_str = path_str.replace("/", ".") # Kalanları noktaya çevir
                
                if path_str and new_val is not None:
                    # Sanitize edilmiş yolu yama olarak uygula
                    updated_values = apply_patch_to_dict(updated_values, path_str, new_val)

            try:
                # Validasyonu test et
                validate(instance=updated_values, schema=app_data["schema"])
                print(f"[VALIDATION SUCCESS] Changes for '{service_name}' strictly match enterprise rules.")
                final_results[service_name] = updated_values
                success = True # Başarılı olduysa döngüden çık
                
            except ValidationError as e:
                # LLM "turkike" gibi bir typo yaparsa buraya düşer
                last_error = e.message
                print(f"[VALIDATION FAILED - ATTEMPT {attempt}] Error: {last_error}")
                
        
        if not success:
            raise HTTPException(
                status_code=500, 
                detail=f"Validation failed for '{service_name}' after {MAX_RETRIES} attempts. AI could not resolve the schema path. Last error: {last_error}"
            )

    
    print("\n[STEP 5] Orchestration complete. Returning all updated configurations.")
    return final_results

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Bot Service Orchestrator")
    parser.add_argument("--listen", type=str, default="0.0.0.0:5003", help="Host and port to listen on")
    
    args = parser.parse_args()
    
    try:
        host, port_str = args.listen.split(":")
        port = int(port_str)
    except ValueError:
        print("Invalid --listen format. Use host:port")
        sys.exit(1)

    print(f"Starting Multi-Agent Orchestrator on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
