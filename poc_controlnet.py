import os
import base64
import json
import httpx
import asyncio

API_URL = "http://127.0.0.1:8700/v1/images/generations/binary"
API_KEY = "test-gen-key"

INIT_IMAGE_PATH = r"outputs\investiture-of-the-gods\unknown_loc\characters\daji\daji_sprite__r0__s303.png"
OUTPUT_DIR = r"outputs\controlnet_poc"

async def main():
    if not os.path.exists(INIT_IMAGE_PATH):
        print(f"Error: {INIT_IMAGE_PATH} not found.")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with open(INIT_IMAGE_PATH, "rb") as f:
        img_bytes = f.read()

    b64_str = base64.b64encode(img_bytes).decode("ascii")
    data_uri = f"data:image/png;base64,{b64_str}"

    rotations = [90, 180, 270]

    for rot in rotations:
        prompt = f"daji sprite, concept art, rich colors, intricate, no background, simple background, character sheet, {rot} degrees rotation view, looking sideways, detailed design"

        payload = {
            "model": "noobai-xl-controlnet-canny",
            "prompt": prompt,
            "negative_prompt": "worst quality, low quality, blurry, deformed, background",
            "init_image": data_uri,
            "mode": "sync",
            "n": 1,
            "size": "1024x1024",
            "steps": 30,
            "cfg": 5.0
        }

        print(f"Generating rotation {rot}...")

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {API_KEY}"}
            )

            if resp.status_code == 200:
                out_path = os.path.join(OUTPUT_DIR, f"daji_rot_{rot}.png")
                with open(out_path, "wb") as f:
                    f.write(resp.content)
                print(f"Saved to {out_path}")
            else:
                print(f"Error {resp.status_code}: {resp.text}")

if __name__ == "__main__":
    asyncio.run(main())
