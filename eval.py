import os
import time
import json
import re
import hashlib
import argparse
from pathlib import Path

import cv2
from google import genai
from google.genai import types

MODEL = "gemini-3.1-pro-preview"
FPS = 1.0
TARGET_W = 720
TARGET_H = 480
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}

PROMPT_TEMPLATE = """
You are an expert computer vision judge evaluating a video object removal
task using THREE separate videos.

The three videos provided represent:
INPUT Video: The original reference video where the target object and its
aftereffects ARE PRESENT.
GT Video (Ground Truth): The IDEAL result video where the object and
aftereffects are perfectly removed, showing the true background
across all frames.
RESULT Video: Our method’s output video. Your goal is to evaluate how
well the RESULT video removes the target object and reconstructs a
plausible, natural background.

Context for this sample:
Target object (present in INPUT; absent in GT): {fg_object}
Aftereffect types to check: {focus_effect}

EVALUATION PROCESS (THINKING STEPS)
Before scoring, you MUST output a step-by-step reasoning process using
the following structure:
Step 1: Target Identification: Watch the INPUT video and identify the
exact appearance, movement, and location of the Target Object ({fg_object})
and its Aftereffects ({focus_effect}).
Step 2: Expected Background Pattern (GT Analysis): Watch the GT video.
Understand the general context, texture, and lighting of the
background where the object used to be.
Step 3: Output vs GT Comparison (Naturalness Check): Compare the RESULT
video against the GT. Evaluate the inpainted area. Does the filled
background look natural and fit the surrounding scene harmoniously?
Did the model successfully create a clean background, or did it
hallucinate a new foreground object?
Step 4: Error Categorization: Determine which identified differences are
related to the main Target Object removal, and which are related to
the Aftereffect removal. Find the specific flaws based on the rubrics
below.

SCORING RUBRICS (1-5 Scale)
HUMAN-LIKE JUDGING GUIDELINES:
- Humans care most about SEMANTIC removal. If the identifiable features
of the target object are gone, humans consider the removal mostly
successful (Score 4), EVEN IF a blurry smudge or a dark moving blob
is left behind.
- A score of 3 should be reserved for partial temporal failures or when a
small piece of the object remains.
- Punish heavily (Scores 1-2) ONLY if the object is fully visible for a
long time, if the model hallucinates a completely new recognizable
object, or if the artifact left behind is grotesquely out of place.

ObjectScore Rubric (How well the main target object was removed):
5 Points: Perfect or Near Perfect. The object is completely removed. The
inpainted background seamlessly matches the GT. Minor, almost
unnoticeable imperfections are acceptable.
4 Points: Good (Forgiving of Smudges). The core semantic features of the
target object are completely gone. The inpainted area might contain
noticeable smudges, blurriness, or dark moving blobs, but it does not
severely break the overall geometry of the scene. The human eye
forgives these as long as the object itself is unrecognizable.
3 Points: Fair (Partial/Distracting). The removal is flawed but not a
total failure. Use this if: 1) A small but recognizable part of the
object remains. 2) Temporal failure: The object is removed, but
suddenly reappears for a few frames. 3) The object is gone, but the
artifact left behind is highly distracting and physically illogical
for the scene.
2 Points: Poor. Huge remnants of the target object are still clearly
visible and moving. OR the model hallucinated a completely new,
identifiable foreground object that completely ruins the background.
1 Point: Fail. The object is practically untouched and fully visible for
the majority of the video, or the generated artifacts cause extreme,
video-breaking corruption.

AftereffectScore Rubric (How well object-caused effects like shadows/
reflections were removed):
5 Points: Perfect. The aftereffect is completely removed, matching the GT
seamlessly.
4 Points: Good. Aftereffect is successfully removed and the area looks
natural, even if lighting/texture slightly differs from the exact GT.
3 Points: Fair. Aftereffect area is noticeably blurry, smudged, or
improperly lit compared to the rest of the scene.
2 Points: Poor. Severe visual artifacts in the aftereffect region.
1 Point: Fail. The aftereffect is still clearly visible in the RESULT
video, completely ignored by the removal process.

OUTPUT FORMAT
You MUST format your response exactly like this:
Reasoning:
Step 1: [Your reasoning]
Step 2: [Your reasoning]
Step 3: [Your reasoning]
Step 4: [Your reasoning]
AftereffectScore: X, ObjectScore: Y
"""


def natural_key(value):
    return [
        int(token) if token.isdigit() else token.lower()
        for token in re.split(r"(\d+)", str(value))
    ]


def normalize_id(value):
    value = str(value)

    if value.isdigit():
        return str(int(value))

    return value


def get_video_fps(cap):
    fps = cap.get(cv2.CAP_PROP_FPS)

    if fps is None or fps <= 0 or fps != fps:
        return 25.0

    return float(fps)


def resize_video(src_path, preproc_dir):
    src_path = Path(src_path)
    preproc_dir.mkdir(parents=True, exist_ok=True)

    stat = src_path.stat()
    fingerprint = hashlib.sha1(
        f"{src_path.resolve()}_{stat.st_size}_{stat.st_mtime_ns}".encode()
    ).hexdigest()[:16]

    output_path = preproc_dir / (
        f"{src_path.stem}_{fingerprint}_{TARGET_W}x{TARGET_H}.mp4"
    )

    if output_path.exists() and output_path.stat().st_size > 0:
        return output_path

    cap = cv2.VideoCapture(str(src_path))

    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {src_path}")

    output_fps = get_video_fps(cap)

    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        output_fps,
        (TARGET_W, TARGET_H),
    )

    if not writer.isOpened():
        cap.release()
        raise RuntimeError(f"Could not create output video: {output_path}")

    try:
        while True:
            success, frame = cap.read()

            if not success:
                break

            resized = cv2.resize(
                frame,
                (TARGET_W, TARGET_H),
                interpolation=cv2.INTER_AREA,
            )

            writer.write(resized)

    finally:
        cap.release()
        writer.release()

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Resized video was not created: {output_path}")

    return output_path


def upload_and_wait(client, file_path, timeout_seconds=180):
    uploaded_file = client.files.upload(file=str(file_path))
    deadline = time.time() + timeout_seconds

    while True:
        remote_file = client.files.get(name=uploaded_file.name)
        state = getattr(remote_file.state, "name", str(remote_file.state))

        if state == "ACTIVE":
            part = types.Part(
                file_data=types.FileData(
                    file_uri=remote_file.uri,
                    mime_type=remote_file.mime_type or "video/mp4",
                ),
                video_metadata=types.VideoMetadata(fps=FPS),
            )
            return remote_file, part

        if state in {"FAILED", "DELETED"}:
            raise RuntimeError(
                f"Video processing failed on Gemini: {file_path}"
            )

        if time.time() > deadline:
            raise TimeoutError(
                f"Video upload timed out: {file_path}"
            )

        time.sleep(2)


def delete_remote_file(client, remote_file):
    if remote_file is None:
        return

    try:
        client.files.delete(name=remote_file.name)
    except Exception:
        pass


def generate_with_retry(client, contents, max_attempts=5):
    wait_seconds = 20
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            return client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.0
                ),
            )

        except Exception as error:
            last_error = error
            error_text = str(error).lower()

            retryable = any(
                keyword in error_text
                for keyword in [
                    "429",
                    "500",
                    "502",
                    "503",
                    "504",
                    "unavailable",
                    "high demand",
                    "rate limit",
                    "resource exhausted",
                    "internal error",
                ]
            )

            if not retryable or attempt == max_attempts:
                raise

            print(
                f"    Retry {attempt}/{max_attempts}: {error}"
            )

            time.sleep(wait_seconds)
            wait_seconds = min(wait_seconds * 2, 180)

    raise RuntimeError(f"Gemini request failed: {last_error}")


def get_sample_id(video_path, root_dir, is_result=False):
    relative = video_path.relative_to(root_dir).with_suffix("")
    sample_id = relative.as_posix()

    if is_result and sample_id.endswith("_remove"):
        sample_id = sample_id[:-7]

    if "/" not in sample_id:
        sample_id = normalize_id(sample_id)

    return sample_id


def build_video_index(folder, is_result=False):
    index = {}

    for path in folder.rglob("*"):
        if not path.is_file():
            continue

        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue

        sample_id = get_sample_id(path, folder, is_result)

        if sample_id not in index:
            index[sample_id] = path
            continue

        current_name = index[sample_id].stem
        candidate_name = path.stem

        if candidate_name == sample_id and current_name != sample_id:
            index[sample_id] = path

    return index


def load_metadata(data_json_path):
    with open(data_json_path, "r", encoding="utf-8") as file:
        raw_data = json.load(file)

    metadata = {}

    for item in raw_data:
        sample_id = normalize_id(item["id"])
        metadata[sample_id] = item

    return metadata


def load_results(output_path):
    if not output_path.exists():
        return []

    try:
        with open(output_path, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, list):
            return data

    except json.JSONDecodeError:
        pass

    return []


def save_results(output_path, results):
    temp_path = output_path.with_suffix(".tmp")

    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(results, file, ensure_ascii=False, indent=2)

    temp_path.replace(output_path)


def parse_score(text, field_name):
    match = re.search(
        rf"{field_name}\s*:\s*([1-5])",
        text,
        flags=re.IGNORECASE,
    )

    if match:
        return int(match.group(1))

    return None


def evaluate_sample(
    client,
    sample_id,
    fg_path,
    bg_path,
    result_path,
    item_data,
    preproc_dir,
):
    remote_fg = None
    remote_bg = None
    remote_result = None

    fg_object = str(item_data["fg_object"])
    effects = item_data["after_effect_type"]

    if isinstance(effects, list):
        focus_effect = ", ".join(map(str, effects))
    else:
        focus_effect = str(effects)

    try:
        fg_720 = resize_video(fg_path, preproc_dir)
        bg_720 = resize_video(bg_path, preproc_dir)
        result_720 = resize_video(result_path, preproc_dir)

        remote_fg, part_fg = upload_and_wait(client, fg_720)
        remote_bg, part_bg = upload_and_wait(client, bg_720)
        remote_result, part_result = upload_and_wait(client, result_720)

        prompt = PROMPT_TEMPLATE.format(
            fg_object=fg_object,
            focus_effect=focus_effect,
        )

        contents = [
            "INPUT VIDEO",
            part_fg,
            "GT VIDEO",
            part_bg,
            "RESULT VIDEO",
            part_result,
            prompt,
        ]

        response = generate_with_retry(client, contents)
        model_output = response.text or ""

        return {
            "id": sample_id,
            "fg_object": fg_object,
            "after_effects": focus_effect,
            "fg_video": str(fg_path),
            "bg_video": str(bg_path),
            "result_video": str(result_path),
            "ObjectScore": parse_score(model_output, "ObjectScore"),
            "AftereffectScore": parse_score(
                model_output,
                "AftereffectScore",
            ),
            "model_output": model_output,
            "status": "ok",
        }

    except Exception as error:
        return {
            "id": sample_id,
            "fg_object": fg_object,
            "after_effects": focus_effect,
            "fg_video": str(fg_path),
            "bg_video": str(bg_path),
            "result_video": str(result_path),
            "status": "error",
            "error": str(error),
        }

    finally:
        delete_remote_file(client, remote_fg)
        delete_remote_file(client, remote_bg)
        delete_remote_file(client, remote_result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=str, default=".")
    parser.add_argument(
        "--api_key",
        type=str,
        default=os.getenv("GEMINI_API_KEY"),
    )
    args = parser.parse_args()

    if not args.api_key:
        raise RuntimeError(
            "API key was not found. Set GEMINI_API_KEY or use --api_key."
        )

    root_dir = Path(args.root)
    fg_dir = root_dir / "fg"
    bg_dir = root_dir / "bg"
    result_dir = root_dir / "result"
    data_json_path = root_dir / "data.json"
    preproc_dir = root_dir / "_preproc_720x480"
    output_path = root_dir / "core_evaluation_results.json"

    for folder in [fg_dir, bg_dir, result_dir]:
        if not folder.is_dir():
            raise FileNotFoundError(
                f"Required folder was not found: {folder}"
            )

    if not data_json_path.exists():
        raise FileNotFoundError(
            f"data.json was not found: {data_json_path}"
        )

    client = genai.Client(api_key=args.api_key)
    metadata = load_metadata(data_json_path)

    fg_index = build_video_index(fg_dir)
    bg_index = build_video_index(bg_dir)
    result_index = build_video_index(result_dir, is_result=True)

    common_ids = sorted(
        set(fg_index) & set(bg_index) & set(result_index),
        key=natural_key,
    )

    existing_results = load_results(output_path)

    completed_ids = {
        normalize_id(result["id"])
        for result in existing_results
        if result.get("status") == "ok"
    }

    print(f"Matched fg/bg/result video triples: {len(common_ids)}")

    for index, sample_id in enumerate(common_ids, start=1):
        normalized_id = normalize_id(sample_id)

        if normalized_id not in metadata:
            print(
                f"[{index}/{len(common_ids)}] "
                f"{sample_id}: not found in data.json, skipping."
            )
            continue

        if normalized_id in completed_ids:
            print(
                f"[{index}/{len(common_ids)}] "
                f"{sample_id}: already completed, skipping."
            )
            continue

        print(
            f"[{index}/{len(common_ids)}] "
            f"{sample_id}: starting Gemini evaluation."
        )

        record = evaluate_sample(
            client=client,
            sample_id=normalized_id,
            fg_path=fg_index[sample_id],
            bg_path=bg_index[sample_id],
            result_path=result_index[sample_id],
            item_data=metadata[normalized_id],
            preproc_dir=preproc_dir,
        )

        existing_results.append(record)
        save_results(output_path, existing_results)

        if record["status"] == "ok":
            print(
                f"[{index}/{len(common_ids)}] "
                f"{sample_id}: completed | "
                f"ObjectScore={record['ObjectScore']} | "
                f"AftereffectScore={record['AftereffectScore']}"
            )
        else:
            print(
                f"[{index}/{len(common_ids)}] "
                f"{sample_id}: failed | {record['error']}"
            )

        time.sleep(1)

    print(f"Evaluation completed. Results saved to: {output_path}")


if __name__ == "__main__":
    main()