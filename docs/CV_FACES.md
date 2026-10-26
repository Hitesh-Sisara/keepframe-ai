# CV_FACES — faces, eyes, face quality, enhancement

Modules: `keepframe/tools/{faces,eyes,face_quality,composition,enhance,publish_blur}.py`, shared
helpers and threshold defaults in `keepframe/tools/_face_defaults.py`. All plain functions returning
JSON-serialisable dicts; none import strands. Models: `models/README.md`.

```bash
uv venv .venv --python 3.12 && uv pip install opencv-contrib-python-headless==5.0.0.93 numpy==2.3.3 \
  pydantic==2.12.3 pyyaml==6.0.3 pytest==8.4.2 ruff==0.14.0
python scripts/fetch_models.py
.venv/bin/python -m pytest tests/unit/test_{faces,eyes,face_quality,enhance,fetch_models}.py -q
```

## Interfaces

Every tool takes `img` (BGR uint8, the `full` proxy) or `event_id` + `photo_id`, in which case it calls
`keepframe.io.proxies.load_full` lazily. Face coordinates are always pixels of that image.

| tool | call | returns |
|---|---|---|
| detect_faces | `(img=None, *, event_id, photo_id, record=None, min_score=None, reuse_existing=True)` | `{faces:[{face_id, bbox_px:[x,y,w,h], score, landmarks5:[[x,y]x5]\|None, source: frsInfo\|rekognition\|yunet}], n_faces}` |
| score_eyes_open | `(img, faces=None, face_ids=None, *, event_id, photo_id)` | `{per_face:[{face_id, left_open_p, right_open_p, state: open\|closed\|partial\|unknown, confidence, min_px, is_main}], any_main_subject_closed, main_subject_face_id, confidence}` |
| score_face_quality | `(img, faces=None, face_ids=None, *, event_id, photo_id, identity=False)` | `{per_face:[{face_id, fiqa, lap_var, minPx, good[, identity_group]}], main_subject_face_id}` |
| score_composition | `(img, *, event_id, photo_id)` | `{nima_mean: None, nima_std: None, nima_available: False, subject_centrality, saliency_centroid, saliency_bbox}` |
| enhance_keeper | `(img, ops=None, *, event_id, photo_id, iso=None, writer=None, out_path=None)` | `{s3_key, ops_applied, ops_skipped, brisque_before, brisque_after, delta_brisque, width, height, bytes, written[, jpeg]}` |
| blur_faces_for_publication | `(img, boxes=None, score_threshold=None)` | `{image, n_blurred, boxes_px}` (not an agent tool; for report/video assets) |

the specified fields are all present; extra fields (`confidence`/`min_px`/`is_main` per face, `good`,
`main_subject_face_id`, `ops_skipped`, BRISQUE before/after) are additive. `record` is a manifest row
(`PhotoRecord` or dict: `mfaces, frs_status, frs_faces[{bbox, det_score, min_px}], frs_src_w, frs_src_h,
rek_faces[{bbox, confidence}], width, height`). With ids but no `record`, `keepframe.io.manifest.read_manifest`
is tried. `enhance_keeper` never touches S3: the worker passes `writer=(key, bytes)`; key = `preview/<event>/<photo>.jpg`.

## Behaviour and decisions

- **Reuse first.** frsInfo (`status == indexed`) ratio boxes are relative to the **processed** image (data agent,
  verified on 12 real events: sourceImageWidth/Height == processed width/height and minPx == min(bw*srcW, bh*srcH)).
  Box = ratio x processed dims (`frs_src_w/h`, else the record's `width/height`), mapped into the analysed frame
  (identity on the `full` proxy, x1024/long-edge on p1024). Sanity check (2 % tolerance): processed aspect vs analysed
  image, and `frs_src_w/h` vs `width/height` when both exist; a mismatch distrusts the metadata and runs YuNet.
  Rekognition `faces[]` (ratio x processed w/h) is still supported but was empty in all 12 events. Reused boxes get
  landmarks from YuNet run on a 30 %-expanded crop resized to 320 px (score >= 0.5, IoU >= 0.3 with the reused box);
  no match -> `landmarks5: null` -> eyes `unknown`.
- **mfaces.** `0` skips detection; `None` (every row in the 12 events) is *unknown* and always runs YuNet.
- **YuNet** at long edge 1024; retry at 1600 when nothing is found and `mfaces > 0`, or when `mfaces` is unknown and
  `faces.detect_retry_on_unknown` is true (default false: it would add ~255 ms x86 to every face-free frame;
  the benchmark can flip it if small faces are being missed). frsInfo indexed with zero faces never triggers a retry.
  Faces sorted by area, `f0` largest.
- **Main subject** = largest face with min side >= 40 px. `any_main_subject_closed` = main face `closed` and mean
  (1 - p_open) >= 0.8 (the policy-3 reject bar). The ">= 3 faces, one non-main closed -> review" rule is left to the
  agent policy; per-face states make it a one-liner.
- **Unknown** when no landmarks, min side < 40, eye crop < 16 px, or face min side < 80 px and face-box mean Lab L < 40
  (policy 3: "dark scene and small faces -> don't run eye-state").
- **eDifFIQA alignment** = `FaceRecognizerSF.alignCrop`; identical (max diff 1 grey level) to the opencv_zoo demo's
  own 5-point warp on all fixtures, so FIQA scores match the reference pipeline.
- **Identity** grouping is optional, SFace cosine >= 0.363, embeddings never leave the call (`docs/RESPONSIBLE_USE.md`).
- **Composition**: no NIMA (no licence-clean, numerically verified ONNX exists; idealo weights would need an
  unvalidated tf2onnx conversion). Returns saliency `subject_centrality` only; `nima_*` are null.
- **Enhance** runs at the 1024 preview size; order wb -> denoise -> clahe; denoise gated by ISO >= 3200 or BRISQUE > 50.
  BRISQUE is not monotonic in noise (Gaussian noise sd 10 on a fixture scores 4.3, sd 30 scores 52.5; NLMeans h=3 barely
  moves it), so `delta_brisque` is reported, not used as a pass/fail.

## Eye-state model: OCEC-M (MIT)

Licence review of the candidate models:

| candidate | licence | verdict |
|---|---|---|
| PINTO0309/OCEC (`ocec_m.onnx`, 1.7 MB) | MIT; training data ODC-By | **chosen**: loads in `cv2.dnn`, fixed 1x3x24x40, outputs a probability |
| yakhyo/face-attribute | BSD-3 code; FaceAttribNet weights from Qualcomm AI Hub | not chosen: weight terms separate from repo licence, 43 MB, 128 px face crop |
| fodorad/blink_detection | MIT | not chosen: sequence/video models |
| train our own (MRL Eye / CEW) | — | not needed; torch 2.9.0+cpu does install here if we ever retrain |

**End-to-end check** (YuNet landmarks -> square crop -> OCEC, state rule open >= 0.6 / closed <= 0.3), 1,400 faces
from MichalMlodawski/closed-open-eyes (synthetic (machine-generated), 512 px), 1,373 with a YuNet face (27 missed), 500 open / 873 closed:

| crop | model | acc (mean p >= 0.5) | open -> open | closed -> closed | open -> closed (false reject) | partial |
|---|---|---|---|---|---|---|
| dataset eye boxes (upper bound) | OCEC-S | 0.992 | 0.988 | 0.926 | 0.000 | 0.047 |
| **0.5 x IOD square (design)** | **OCEC-M** | **0.985** | 0.904 | **0.931** | **0.002** | 0.075 |
| 0.5 x IOD square | OCEC-S | 0.982 | 0.906 | 0.874 | 0.002 | 0.109 |
| 0.5 x IOD square | OCEC-L | 0.972 | 0.866 | 0.836 | 0.004 | 0.143 |
| 0.4 x IOD square | OCEC-M | 0.989 | 0.872 | 0.951 | 0.004 | 0.076 |
| 0.6 x 0.36 IOD rect | OCEC-M | 0.993 | 0.878 | 0.898 | 0.000 | 0.107 |

Choice: OCEC-M with the 0.5 x IOD square. It is the best trade between catching closed eyes and never calling
open eyes closed (0.2 % false-reject rate); "partial" routes to review, not reject. **Caveat:** this set is OCEC's own
training source, so it validates the crop geometry and wiring, not generalisation to event photos. the benchmark labelled
real set (`docs/EVALUATION.md`) is the real test; expect glasses glare, profile faces, laughter squints and downcast eyes to land in
`partial`/`closed` (failure catalogue).

## Timings (x86 dev box, NOT Graviton)

`DO-Regular` 8 vCPU KVM @ 2.0 GHz, shared with other workloads (load avg ~3), `cv2.setNumThreads(1)`,
`OPENCV_FORCE_DNN_ENGINE=3`, opencv-contrib-python-headless 5.0.0.93. Synthetic 2560x1707 frame with 3 pasted faces
(700/450/300 px). Median of N runs, ms. Labelled x86 baseline only; c8g numbers come from the benchmark.

| stage | median ms | p90 ms | runs |
|---|---|---|---|
| YuNet @1024 long edge (incl. 2560->1024 resize ~35 ms) | 117.5 | 140.5 | 30 |
| YuNet @1600 long edge (retry path) | 254.8 | 268.4 | 15 |
| detect_faces with frsInfo reuse + YuNet landmark refine on 3 crops | 33.9 | 38.2 | 30 |
| eye crop (2 eyes) | < 0.1 | < 0.1 | 200 |
| OCEC-M classify (2 eyes, one batched forward) | 5.9 | 6.8 | 200 |
| score_eyes_open, 3 faces end to end | 19.0 | 22.9 | 50 |
| SFace alignCrop 112x112 | 0.2 | 0.2 | 200 |
| eDifFIQA(T), 1 face | 12.4 | 14.5 | 100 |
| score_face_quality, 3 faces end to end | 37.3 | 42.0 | 30 |
| score_face_quality identity=True, 3 faces | 142.8 | 156.4 | 20 |
| CLAHE on L @1024 | 18.1 | 19.5 | 100 |
| GrayworldWB @1024 | 1.8 | 1.9 | 100 |
| fastNlMeansDenoisingColored @1024 (h=3, 7, 21) | 2408.7 | 2970.8 | 5 |
| BRISQUE @1024 (cv2.quality, LIVE) | 72.1 | 75.9 | 10 |
| score_composition (StaticSaliencyFineGrained @1024) | 291.1 | 302.6 | 20 |
| blur_faces_for_publication (2560 frame, 2 scales) | 1116.2 | 1202.5 | 10 |

Observations: YuNet @1024 is ~105 ms of pure detect here (the design estimate was 20-40 ms on c8g); reusing frsInfo boxes is
3.5x cheaper, so reuse matters. NLMeans is ~2.4 s, 4-8x the design estimate; keep the top-50 cap or run it on a
smaller preview. Engine check: `OPENCV_FORCE_DNN_ENGINE=1` (classic only) makes `FaceDetectorYN.create` fail
("Input shape redefinition is not allowed"); 2 (new) and 3 (auto) work. OCEC/eDifFIQA are equal or faster on 3.
