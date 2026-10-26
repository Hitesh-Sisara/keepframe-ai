# models/

Model files are **not committed** (`.gitignore`: `models/*.onnx`, `models/*.yml`). They are pinned in
`config/models.lock` (`name<TAB>url<TAB>sha256<TAB>licence`) and fetched with:

```bash
python scripts/fetch_models.py                       # -> ./models (idempotent, verifies sha256)
python scripts/fetch_models.py --out /opt/models     # Dockerfile models stage
python scripts/fetch_models.py --check               # verify only, no network
```

Runtime lookup order: `$CULL_MODELS_DIR`, then `/opt/models` (container), then `<repo>/models`.
All inference runs through OpenCV 5 (`cv2.FaceDetectorYN`, `cv2.FaceRecognizerSF`, `cv2.dnn.readNet`,
`cv2.quality`). No onnxruntime.

| file | used by | source (pinned revision) | licence | sha256 |
|---|---|---|---|---|
| face_detection_yunet_2023mar.onnx | detect_faces, publish_blur | huggingface.co/opencv/face_detection_yunet @ 3cc26e7f | MIT (Shiqi Yu) | 8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4 |
| face_recognition_sface_2021dec.onnx | face_quality (alignCrop, optional identity) | huggingface.co/opencv/face_recognition_sface @ 3d708243 | Apache-2.0 | 0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79 |
| ediffiqa_tiny_jun2024.onnx | face_quality (eDifFIQA(T)) | huggingface.co/opencv/face_image_quality_assessment_ediffiqa @ 4fce5f6a | CC-BY-4.0 (attribution: Babnik, Peer, Štruc, eDifFIQA, IEEE T-BIOM 2024) | 9426c899cc0f01665240cb7d9e7f98e18e24e456c178326c771a43da289bfc6a |
| ocec_m.onnx | eyes (eye-state) | github.com/PINTO0309/OCEC release `onnx` | MIT (Katsuya Hyodo); trained on MichalMlodawski/closed-open-eyes (ODC-By 1.0, synthetic (machine-generated) faces) | 810cf02f9c5a933fde688285aaf4548be0355092bcacbf3ee256ccfd01df6f6e |
| brisque_model_live.yml | enhance (delta_brisque); shareable with sharpness | opencv_contrib @ 1275703c modules/quality/samples | Apache-2.0 | 4c44c7eec9e5139830c0bb0e4a044c9cd351ba5bbc9e3798beca3243e2782ff4 |
| brisque_range_live.yml | same | same | Apache-2.0 | a18427f4f7ad087524bd0d63389649a3899b51ca6d44a4d5f8d8171f1f914a9c |

Notes
- The OCEC release asset is a mutable GitHub release (tag `onnx`), not a commit. The sha256 pin makes a
  silent swap fail the build rather than change behaviour. Mirroring it as a release asset of our own
  repo is a follow-up.
- OCEC model contract: input `images` float32 `N x 3 x 24 x 40`, RGB, `x / 255`; output `prob_open`
  (`N`, sigmoid). opset 17. Loads with `cv2.dnn.readNet` under the new engine (`OPENCV_FORCE_DNN_ENGINE=3`).
- Licence summary for redistribution in the container image: MIT, Apache-2.0, CC-BY-4.0 all permit it with
  attribution; the attributions above must appear in docs/REPORT.md / the image's NOTICE.
- Not used: NIMA (no licence-clean, numerically verified ONNX); yakhyo/face-attribute (BSD-3 code, but the
  43 MB FaceAttribNet weights originate from Qualcomm AI Hub with separate terms); fodorad/blink_detection
  (MIT, but video/sequence models).
