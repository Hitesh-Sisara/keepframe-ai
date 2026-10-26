# Face fixtures: provenance and licence

All five images are synthetic (machine-generated) faces (no real person, no source customer data). They come from the
"Open and Closed Eyes Dataset" by Michał Młodawski, licensed under the Open Data Commons Attribution
License (ODC-By) v1.0: https://huggingface.co/datasets/MichalMlodawski/closed-open-eyes
(revision 2ce8f332f80203a7b7d6a9b5323cd21d8a510b15). The dataset card states the images are synthetic (machine-generated).
Bytes are unmodified; file name = original JPEG's sha256.

| file | shard | row | Image_id | label | sha256 (= original filename) |
|---|---|---|---|---|---|
| closed_a.jpg | data/dataset_002.parquet | 0 | 100 | closed_eyes | b7d3e48db0988cdfff09935a8cb922d7751b475b6c78138ea30b5fd31a2f5768 |
| closed_b.jpg | data/dataset_002.parquet | 4 | 104 | closed_eyes | 7fde09fc1246f88bd62c5ec2b87a8e6a3664f96579ab64d7956c2d75fb9b9b4c |
| open_a.jpg | data/dataset_1001.parquet | 0 | 100000 | open_eyes | 4075341316b4fbb492168aa89ee59d846050f40dd81e35c802b83b8528a7f0d6 |
| open_b.jpg | data/dataset_1001.parquet | 3 | 100003 | open_eyes | d20b038035183a71ac48b809ce6a9057cb04fcaee467301e95b22941f0bc61f7 |
| open_c.jpg | data/dataset_1001.parquet | 10 | 100010 | open_eyes | e38a7a00f9e989cbf901d85afbc4a16a95015cdd6967207ca26e7a49033b0fd3 |

Note: this dataset is also OCEC's training source, so these fixtures test wiring, not generalisation.
The tests also build group photos, small faces, dark faces and blurred faces from these at test time.
